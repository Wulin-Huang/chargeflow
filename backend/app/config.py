from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
DB_PATH = Path(__file__).resolve().parents[1] / "chargeflow.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_PATH, env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ChargeFlow"
    db_url: str = f"sqlite+aiosqlite:///{DB_PATH}"
    redis_url: str = ""
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    embed_broker: bool = True
    simulator_piles: int = 168
    jwt_secret: str = "chargeflow-dev-secret-change-in-prod"
    jwt_expire_hours: int = 72

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_model_pro: str = "deepseek-reasoner"

    # USD / 百万 token（缓存命中 / 未命中 / 输出），用于 ai_logs 成本核算
    deepseek_price: dict = {"cache_hit": 0.0028, "cache_miss": 0.14, "output": 0.28}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
