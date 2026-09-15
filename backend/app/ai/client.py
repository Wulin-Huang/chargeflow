"""DeepSeek 客户端封装：超时 / 重试 / 并发信号量 / ai_logs 成本观测 / 降级。

铁律：所有 AI 调用必须可失败——上层捕获 LLMError 后走降级路径，核心业务零依赖。
"""

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator

import httpx

from app.config import settings
from app.db import SessionLocal, utcnow
from app.models import AiLog

logger = logging.getLogger("chargeflow.ai")

TIMEOUT = httpx.Timeout(60.0, connect=5.0)
SEM = asyncio.Semaphore(4)


class LLMError(Exception):
    pass


def _cost_usd(usage: dict) -> float:
    p = settings.deepseek_price
    prompt = usage.get("prompt_tokens", 0)
    hit = usage.get("prompt_cache_hit_tokens", 0)
    miss = max(0, prompt - hit)
    comp = usage.get("completion_tokens", 0)
    return (miss * p["cache_miss"] + hit * p["cache_hit"] + comp * p["output"]) / 1_000_000


async def _log(scene: str, model: str, usage: dict, latency_ms: int, ok: bool, error: str = "") -> None:
    try:
        async with SessionLocal() as db:
            db.add(
                AiLog(
                    scene=scene,
                    model=model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    cache_hit_tokens=usage.get("prompt_cache_hit_tokens", 0),
                    latency_ms=latency_ms,
                    cost_usd=_cost_usd(usage),
                    ok=ok,
                    error=error[:250],
                    created_at=utcnow(),
                )
            )
            await db.commit()
    except Exception:
        logger.exception("ai_logs 写入失败")


async def chat(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    json_mode: bool = False,
    model: str | None = None,
    scene: str = "chat",
) -> dict:
    """非流式调用，返回完整响应。失败重试 1 次后抛 LLMError。"""
    model = model or settings.deepseek_model
    body: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        body["tools"] = tools
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    last_err: Exception | None = None
    for attempt in range(2):
        t0 = time.monotonic()
        try:
            async with SEM:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    resp = await client.post(
                        f"{settings.deepseek_base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                        json=body,
                    )
            resp.raise_for_status()
            data = resp.json()
            latency = int((time.monotonic() - t0) * 1000)
            await _log(scene, model, data.get("usage", {}), latency, True)
            return data["choices"][0]["message"]
        except Exception as e:  # noqa: BLE001 统一降级口径
            last_err = e
            latency = int((time.monotonic() - t0) * 1000)
            await _log(scene, model, {}, latency, False, str(e))
            logger.warning("DeepSeek 调用失败(第 %d 次) scene=%s: %s", attempt + 1, scene, e)
            await asyncio.sleep(1 + attempt)  # 指数退避

    raise LLMError(f"DeepSeek 不可用：{last_err}")


async def chat_stream(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    model: str | None = None,
    scene: str = "chat",
) -> AsyncIterator[dict]:
    """流式调用，yield 增量片段；结束时记录 ai_logs。"""
    model = model or settings.deepseek_model
    body: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if tools:
        body["tools"] = tools
    usage: dict = {}
    t0 = time.monotonic()
    collected = 0
    try:
        async with SEM:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    f"{settings.deepseek_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                    json=body,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        if raw == "[DONE]":
                            break
                        chunk = json.loads(raw)
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            collected += 1
                            yield {"type": "delta", "content": content}
                        tool_calls = delta.get("tool_calls")
                        if tool_calls:
                            yield {"type": "tool_calls", "tool_calls": tool_calls}
    except Exception as e:  # noqa: BLE001
        await _log(scene, model, {"completion_tokens": collected}, int((time.monotonic() - t0) * 1000), False, str(e))
        raise LLMError(f"DeepSeek 流式调用失败：{e}")
    await _log(scene, model, {**usage, "completion_tokens": usage.get("completion_tokens", collected)}, int((time.monotonic() - t0) * 1000), True)


async def chat_json(prompt_messages: list[dict], *, scene: str, model: str | None = None) -> Any:
    """JSON 模式调用：解析失败视为失败（不重试结构），抛 LLMError。"""
    msg = await chat(prompt_messages, json_mode=True, model=model, scene=scene)
    content = msg.get("content", "")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        await _log(scene, model or settings.deepseek_model, {}, 0, False, f"JSON 解析失败: {e}")
        raise LLMError(f"JSON 输出解析失败：{e}")


def deepseek_available() -> bool:
    return bool(settings.deepseek_api_key)
