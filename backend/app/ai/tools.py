"""Function Calling 工具注册表：白名单制 + pydantic 严格校验。

安全设计：
  1. LLM 只能调用白名单工具，不能"造"工具；
  2. 参数全部经 pydantic 校验，非法参数拒绝；
  3. 唯一写前置工具 prepare_reservation 只生成参数卡片，落库必须走用户确认的 REST。
"""

import json
import math
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.bus import get_bus
from app.db import SessionLocal, utcnow
from app.models import BillingOrder, ChargingSession, Pile, PricePolicy, Reservation, Station, Telemetry

SYSTEM_PROMPT = (
    "你是充电站运营平台「ChargeFlow」的智能助手小电。\n"
    "用户会询问充电桩、电价、账单、预约相关的问题。\n"
    "规则：\n"
    "1. 优先调用工具查询实时数据，禁止编造桩位、价格或账单数字；\n"
    "2. 回答简洁自然，中文，重要数字（价格、距离、时段）清晰列出；\n"
    "3. 涉及预约等写操作时，调用 prepare_reservation 生成参数让用户确认，不要声称已预约成功；\n"
    "4. 工具查不到就如实说不知道。"
)


class FindStationsArgs(BaseModel):
    lat: float | None = Field(None, ge=-90, le=90, description="用户位置纬度（未知时先询问用户所在城市）")
    lng: float | None = Field(None, ge=-180, le=180, description="用户位置经度")
    radius_km: float = Field(10.0, gt=0, le=500, description="搜索半径（公里），站点覆盖全省，建议 5-50")
    connector: Literal["DC", "AC", "any"] = "DC"
    min_free: int = Field(1, ge=0, description="至少空闲数")


class StationDetailArgs(BaseModel):
    station_id: int = Field(gt=0)


class RecommendWindowArgs(BaseModel):
    station_id: int = Field(gt=0)
    hours_ahead: int = Field(12, gt=0, le=48)


class ExplainBillArgs(BaseModel):
    session_id: int = Field(gt=0)


class PrepareReservationArgs(BaseModel):
    pile_id: int = Field(gt=0)
    start_minutes_later: int = Field(15, ge=0, le=24 * 60)
    duration_minutes: int = Field(45, ge=10, le=240)


def _hint(price: PricePolicy) -> str:
    if not price or not price.periods:
        return ""
    lo = min(p["price_cents"] for p in price.periods)
    hi = max(p["price_cents"] for p in price.periods)
    return f"¥{lo / 100:.2f}–¥{hi / 100:.2f}/kWh"


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    rlat1, rlng1, rlat2, rlng2 = map(math.radians, (lat1, lng1, lat2, lng2))
    a = math.sin((rlat2 - rlat1) / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin((rlng2 - rlng1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


async def _find_stations(args: FindStationsArgs) -> list[dict]:
    out = []
    async with SessionLocal() as db:
        stations = (await db.execute(select(Station))).scalars().all()
        for st in stations:
            if args.lat is not None and args.lng is not None:
                dist = _haversine_km(args.lat, args.lng, st.lat, st.lng)
                if dist > args.radius_km:
                    continue
            else:
                dist = None
            piles = (await db.execute(select(Pile).where(Pile.station_id == st.id))).scalars().all()
            free = 0
            for p in piles:
                if args.connector != "any" and p.connector != args.connector:
                    continue
                latest = await get_bus().get(f"pile:{p.id}:latest")
                status = p.status if not latest else ("online-" + p.status if latest else "offline")
                if latest and p.status in ("idle", "reserved") and p.status != "reserved":
                    free += 1
            if free >= args.min_free:
                policy = (
                    await db.execute(select(PricePolicy).where(PricePolicy.station_id == st.id).order_by(PricePolicy.id.desc()).limit(1))
                ).scalar()
                out.append(
                    {
                        "station_id": st.id,
                        "name": st.name,
                        "address": st.address,
                        "free_{args.connector}": free,
                        "total": len(piles),
                        "price": _hint(policy),
                        **({"distance_km": round(dist, 1)} if dist is not None else {}),
                    }
                )
    if args.lat is not None and args.lng is not None:
        out.sort(key=lambda s: s["distance_km"])
    return out[:8]


async def _station_detail(args: StationDetailArgs) -> dict:
    async with SessionLocal() as db:
        st = await db.get(Station, args.station_id)
        if st is None:
            return {"error": "站点不存在"}
        piles = (await db.execute(select(Pile).where(Pile.station_id == st.id))).scalars().all()
        policy = (
            await db.execute(select(PricePolicy).where(PricePolicy.station_id == st.id).order_by(PricePolicy.id.desc()).limit(1))
        ).scalar()
        pile_list = []
        for p in piles:
            latest = await get_bus().get(f"pile:{p.id}:latest")
            pile_list.append(
                {
                    "pile_id": p.id,
                    "code": p.code,
                    "connector": p.connector,
                    "max_power_kw": p.max_power_kw,
                    "status": "offline" if not latest else p.status,
                }
            )
        return {
            "station": {"name": st.name, "address": st.address},
            "piles": pile_list,
            "price_periods": policy.periods if policy else [],
            "service_fee_cents_per_kwh": policy.service_fee_cents_per_kwh if policy else 50,
        }


async def _recommend_window(args: RecommendWindowArgs) -> dict:
    """统计模型算数字（近 14 天按小时空闲率），LLM 负责表达——各干各擅长的事。"""
    async with SessionLocal() as db:
        since = utcnow() - timedelta(days=14)
        rows = (
            await db.execute(
                select(Telemetry.pile_id, func.strftime("%H", Telemetry.ts), func.count())
                .where(Telemetry.ts >= since)
                .group_by(Telemetry.pile_id, func.strftime("%H", Telemetry.ts))
            )
        ).all()
    busy_by_hour: dict[str, int] = {}
    for _, hour, cnt in rows:
        busy_by_hour[hour] = busy_by_hour.get(hour, 0) + cnt
    if busy_by_hour:
        peak = max(busy_by_hour.values())
        hours = [
            {"hour": h, "busy_index": round(v / peak, 2)}
            for h, v in sorted(busy_by_hour.items(), key=lambda kv: kv[1])
        ][:6]
    else:
        hours = [{"hour": f"{(utcnow().hour + i) % 24:02d}", "busy_index": 0.2} for i in range(6)]
    async with SessionLocal() as db:
        policy = (
            await db.execute(select(PricePolicy).where(PricePolicy.station_id == args.station_id).order_by(PricePolicy.id.desc()).limit(1))
        ).scalar()
    cheapest = min(policy.periods, key=lambda p: p["price_cents"]) if policy else None
    return {
        "least_busy_hours": [h["hour"] + ":00" for h in hours],
        "cheapest_period": f"{cheapest['start']}-{cheapest['end']}（¥{cheapest['price_cents'] / 100:.2f}/kWh）" if cheapest else "未知",
        "note": "数据为近 14 天小时级活跃度统计，值越低越空闲",
    }


async def _explain_bill(args: ExplainBillArgs) -> dict:
    async with SessionLocal() as db:
        order = (await db.execute(select(BillingOrder).where(BillingOrder.session_id == args.session_id))).scalar()
        session = await db.get(ChargingSession, args.session_id)
        if order is None:
            return {"error": "该会话尚未结算"}
        return {
            "session_id": args.session_id,
            "total_cents": order.total_cents,
            "energy_fee_cents": order.energy_fee_cents,
            "service_fee_cents": order.service_fee_cents,
            "occupy_fee_cents": order.occupy_fee_cents,
            "total_wh": session.kwh_wh if session else 0,
            "segments": order.segments,
        }


async def _prepare_reservation(args: PrepareReservationArgs) -> dict:
    start = utcnow() + timedelta(minutes=args.start_minutes_later)
    end = start + timedelta(minutes=args.duration_minutes)
    return {
        "action": "confirm_reservation",
        "pile_id": args.pile_id,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "confirm_hint": "请让用户点击确认后调用预约接口，本工具不落库",
    }


TOOL_REGISTRY: dict[str, dict] = {
    "find_stations": {
        "description": "查找附近有空闲充电桩的站点（站点覆盖广东省 21 市）。若用户未提供位置，先询问其所在城市/地标，再带 lat/lng 调用；返回实时空闲数、价格区间与距离",
        "args_model": FindStationsArgs,
        "handler": _find_stations,
    },
    "get_station_detail": {
        "description": "查站点详情：桩位实时状态、分时电价、服务费",
        "args_model": StationDetailArgs,
        "handler": _station_detail,
    },
    "recommend_window": {
        "description": "推荐充电时段：基于近14天统计的空闲小时与最低电价时段",
        "args_model": RecommendWindowArgs,
        "handler": _recommend_window,
    },
    "explain_bill": {
        "description": "解释某次充电会话的账单构成（分段明细）",
        "args_model": ExplainBillArgs,
        "handler": _explain_bill,
    },
    "prepare_reservation": {
        "description": "准备预约参数（用户确认后才真正落库）",
        "args_model": PrepareReservationArgs,
        "handler": _prepare_reservation,
    },
}


def openai_tools_schema() -> list[dict]:
    out = []
    for name, spec in TOOL_REGISTRY.items():
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec["description"],
                    "parameters": spec["args_model"].model_json_schema(),
                },
            }
        )
    return out


async def execute_tool(name: str, raw_args: dict) -> dict:
    """执行白名单工具：pydantic 校验失败时返回错误对象（回喂模型自纠一次）。"""
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return {"error": f"未知工具：{name}"}
    try:
        args = spec["args_model"](**(raw_args or {}))
    except Exception as e:  # noqa: BLE001
        return {"error": f"参数校验失败：{e}"}
    result = await spec["handler"](args)
    # 结果序列化后原样返回（JSON 兼容 dict/list）
    return result if isinstance(result, (dict, list)) else {"result": str(result)}
