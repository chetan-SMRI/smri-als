from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./ebio.db"
    admin_username: str = "admin"
    admin_password: str = "chetan@123"
    session_secret: str = "change-this-session-secret-in-production"
    public_base_url: str = "http://localhost:8010"
    timezone: str = "Asia/Kolkata"
    device_error_delay: int = 30
    device_trans_interval: int = 1
    webhook_poll_seconds: float = 2.0

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()

