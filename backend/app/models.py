from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default="customer")  # customer | operator | admin
    password_hash: Mapped[str] = mapped_column(String(256))
    salt: Mapped[str] = mapped_column(String(32))
    balance_cents: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    address: Mapped[str] = mapped_column(String(256), default="")
    lat: Mapped[float] = mapped_column(Numeric(10, 6))
    lng: Mapped[float] = mapped_column(Numeric(10, 6))
    capacity_kw: Mapped[int] = mapped_column(Integer, default=0)  # 台区变压器可用容量，有序充电调度约束
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Pile(Base):
    __tablename__ = "piles"
    __table_args__ = (UniqueConstraint("station_id", "code", name="uq_pile_station_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    code: Mapped[str] = mapped_column(String(32))
    connector: Mapped[str] = mapped_column(String(8), default="DC")  # DC | AC
    max_power_kw: Mapped[int] = mapped_column(Integer, default=120)
    status: Mapped[str] = mapped_column(String(16), default="offline")  # online-idle 等，由 MQTT 驱动


class PricePolicy(Base):
    """分时电价策略；periods=[{start,end,price_cents}]，版本化支持未来调价。"""

    __tablename__ = "price_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    periods: Mapped[list] = mapped_column(JSON)
    service_fee_cents_per_kwh: Mapped[int] = mapped_column(Integer, default=50)
    free_occupy_minutes: Mapped[int] = mapped_column(Integer, default=30)
    occupy_fee_cents_per_min: Mapped[int] = mapped_column(Integer, default=50)
    effective_from: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Reservation(Base):
    """桩预约；部分唯一索引兜底并发（同桩活跃预约仅一条）。"""

    __tablename__ = "reservations"
    __table_args__ = (
        Index(
            "uq_res_active",
            "pile_id",
            unique=True,
            sqlite_where=text("status IN ('pending','active')"),
            postgresql_where=text("status IN ('pending','active')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    pile_id: Mapped[int] = mapped_column(ForeignKey("piles.id"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|active|fulfilled|cancelled|expired
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChargingSession(Base):
    __tablename__ = "charging_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    pile_id: Mapped[int] = mapped_column(ForeignKey("piles.id"), index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("user_vehicles.id"), nullable=True)  # 关联用户车辆
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 指令幂等键
    status: Mapped[str] = mapped_column(String(16), default="starting", index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    kwh_wh: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str] = mapped_column(String(32), default="")
    target_soc: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 充电目标 SOC%（None=不设目标）
    target_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 充电目标金额（分，None=不设金额目标）
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # 启动时电价快照：契约成立时锁定
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Telemetry(Base):
    """遥测明细，只追加；高频写与业务表分离。"""

    __tablename__ = "telemetry"
    __table_args__ = (Index("ix_tel_session_ts", "session_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("charging_sessions.id"), index=True)
    pile_id: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    power_kw: Mapped[int] = mapped_column(Numeric(8, 2), default=0)
    voltage: Mapped[int] = mapped_column(Integer, default=0)
    current_a: Mapped[int] = mapped_column(Integer, default=0)
    soc: Mapped[int] = mapped_column(Integer, default=0)
    gun_temp: Mapped[float] = mapped_column(Numeric(5, 1), default=25.0)
    cum_wh: Mapped[int] = mapped_column(Integer, default=0)  # 累积电量（计费引擎输入）


class BillingOrder(Base):
    __tablename__ = "billing_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("charging_sessions.id"), unique=True, index=True)
    energy_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    service_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    occupy_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    segments: Mapped[list] = mapped_column(JSON, default=list)  # 分段明细（时段×电量×单价），对账与账单解释的原始凭据
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    pile_id: Mapped[int] = mapped_column(ForeignKey("piles.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|processing|closed
    trigger_rule: Mapped[str] = mapped_column(String(128), default="")
    telemetry_digest: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_diagnosis: Mapped[dict] = mapped_column(JSON, default=dict)
    resolution: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VehicleProfile(Base):
    """AI 生成的车型仿真参数包——「活数据」的源头，生成一次永久复用。"""

    __tablename__ = "vehicle_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(64), unique=True)
    battery_kwh: Mapped[int] = mapped_column(Integer)
    soc_curve: Mapped[list] = mapped_column(JSON)  # [[soc, power_kw], ...]
    temp_coeff: Mapped[float] = mapped_column(Numeric(4, 2), default=1.0)
    anomaly_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    in_use: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserVehicle(Base):
    """车主「我的爱车」：用户自定义车辆，可关联车型参数包以获得精准充电预估。"""

    __tablename__ = "user_vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    nickname: Mapped[str] = mapped_column(String(32))  # 昵称，如「小白」
    plate: Mapped[str] = mapped_column(String(16), default="")  # 车牌号
    battery_kwh: Mapped[int] = mapped_column(Integer, default=60)  # 电池容量（kWh）
    default_target_soc: Mapped[int] = mapped_column(Integer, default=80)  # 默认充电目标 SOC%
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle_profiles.id"), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # 默认车辆
    total_sessions: Mapped[int] = mapped_column(Integer, default=0)  # 累计充电次数
    total_wh: Mapped[int] = mapped_column(Integer, default=0)  # 累计充电量（Wh）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AiLog(Base):
    """LLM 调用可观测性：场景/模型/token/缓存命中/延迟/成本/成败。"""

    __tablename__ = "ai_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scene: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(32))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class DailyReport(Base):
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[str] = mapped_column(String(10), index=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    narrative: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
