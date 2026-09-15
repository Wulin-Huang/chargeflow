from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class LoginReq(BaseModel):
    phone: str
    password: str


class RegisterReq(BaseModel):
    phone: str
    password: str
    nickname: str = ""


class TokenResp(BaseModel):
    token: str
    role: str
    user_id: int
    nickname: str


class StationOut(BaseModel):
    id: int
    name: str
    address: str
    lat: float
    lng: float
    total_piles: int = 0
    free_piles: int = 0
    charging_piles: int = 0
    price_hint: str = ""


class PileOut(BaseModel):
    id: int
    code: str
    connector: str
    max_power_kw: int
    status: str


class StartChargingReq(BaseModel):
    pile_id: int
    request_id: str = Field(min_length=8, max_length=64)


class SessionOut(BaseModel):
    id: int
    pile_id: int
    status: str
    start_at: datetime | None = None
    end_at: datetime | None = None
    kwh_wh: int = 0
    stop_reason: str = ""


class ReservationReq(BaseModel):
    pile_id: int
    start_at: datetime
    end_at: datetime

    @field_validator("end_at")
    @classmethod
    def check_window(cls, v, info):
        if "start_at" in info.data and v <= info.data["start_at"]:
            raise ValueError("结束时间必须晚于开始时间")
        return v


class ReservationOut(BaseModel):
    id: int
    pile_id: int
    start_at: datetime
    end_at: datetime
    status: str


class ChatReq(BaseModel):
    messages: list[dict] = Field(max_length=12)


class PricePolicyReq(BaseModel):
    station_id: int
    periods: list[dict]
    service_fee_cents_per_kwh: int = 50
    free_occupy_minutes: int = 30
    occupy_fee_cents_per_min: int = 50

class SetTargetReq(BaseModel):
    target_soc: int | None = Field(default=None, ge=20, le=100)
    target_cents: int | None = Field(default=None, gt=0, le=100000)
    vehicle_id: int | None = None
