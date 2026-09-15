import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import client as llm
from app.ai.tools import SYSTEM_PROMPT, execute_tool, openai_tools_schema
from app.db import get_db
from app.models import AiLog, User
from app.schemas import ChatReq
from app.security import current_user

router = APIRouter(prefix="/ai", tags=["ai"])

MAX_TOOL_ROUNDS = 3


@router.post("/chat")
async def chat(req: ChatReq, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}, *req.messages[-8:]]

    async def sse():
        try:
            for _round in range(MAX_TOOL_ROUNDS + 1):
                if _round < MAX_TOOL_ROUNDS:
                    # 工具轮：非流式，拿到 tool_calls 或直接最终答案
                    msg = await llm.chat(messages, tools=openai_tools_schema(), scene="assistant_tools")
                    calls = msg.get("tool_calls")
                    if not calls:
                        text = msg.get("content", "")
                        yield _sse("delta", {"content": text})
                        break
                    messages.append({"role": "assistant", "tool_calls": calls, "content": msg.get("content") or ""})
                    for call in calls:
                        fn = call.get("function", {})
                        try:
                            raw_args = json.loads(fn.get("arguments") or "{}")
                        except json.JSONDecodeError:
                            raw_args = {}
                        result = await execute_tool(fn.get("name", ""), raw_args)
                        yield _sse("tool", {"name": fn.get("name"), "args": raw_args, "result": result})
                        messages.append(
                            {"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, ensure_ascii=False)[:2000]}
                        )
                else:
                    # 最终轮：流式输出（工具已齐，摘掉工具定义减少 token）
                    stream = llm.chat_stream(messages, scene="assistant_final")
                    async for chunk in stream:
                        if chunk["type"] == "delta":
                            yield _sse("delta", {"content": chunk["content"]})
            yield _sse("done", {})
        except llm.LLMError as e:
            yield _sse("error", {"message": f"智能服务暂时不可用（{e}），请稍后再试或使用常规操作"})

    return StreamingResponse(sse(), media_type="text/event-stream")


@router.get("/logs")
async def ai_logs(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if user.role == "customer":
        raise PermissionError
    rows = (
        await db.execute(select(AiLog).order_by(AiLog.id.desc()).limit(100))
    ).scalars().all()
    total_cost = sum(float(r.cost_usd) for r in rows)
    ok_rate = sum(1 for r in rows if r.ok) / max(1, len(rows))
    return {
        "total_cost_usd": round(total_cost, 6),
        "call_count": len(rows),
        "ok_rate": round(ok_rate, 3),
        "calls": [
            {
                "id": r.id, "scene": r.scene, "model": r.model,
                "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                "cache_hit_tokens": r.cache_hit_tokens, "latency_ms": r.latency_ms,
                "cost_usd": float(r.cost_usd), "ok": r.ok, "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
