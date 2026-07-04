from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MILLICALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("data")
    database_url: str = "sqlite+aiosqlite:///data/millicall.db"
    fs_config_dir: Path = Path("data/freeswitch")

    sip_domain: str = "millicall.local"
    sip_port: int = 5060
    sip_ip: str = "auto"
    rtp_ip: str = "auto"
    sip_bind_ip: str | None = None  # env MILLICALL_SIP_BIND_IP; overrides sip_ip/rtp_ip when set

    esl_host: str = "127.0.0.1"
    esl_port: int = 8021
    event_socket_ip: str = "127.0.0.1"

    session_cookie_name: str = "millicall_session"
    session_max_age: int = 60 * 60 * 24 * 7
    cookie_secure: bool = True
    cookie_samesite: str = "lax"


@lru_cache
def get_settings() -> Settings:
    return Settings()
