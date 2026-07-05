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
    # TTS 音声を書き出す共有ディレクトリ（FreeSWITCH コンテナにも同一パスで bind mount）
    tts_cache_dir: Path = Path("data/freeswitch/tts")
    # FreeSWITCH の mod_audio_stream が core の音声受け WS へ接続するベース URL。
    # host ネットワーキング前提のため既定は 127.0.0.1:8000（パス /media/audio-fork/<uuid> が付与される）。
    media_ws_base_url: str = "ws://127.0.0.1:8000"

    sip_domain: str = "millicall.local"
    sip_port: int = 5060
    external_sip_port: int = 5080
    sip_ip: str = "auto"
    rtp_ip: str = "auto"
    sip_bind_ip: str | None = None  # env MILLICALL_SIP_BIND_IP; overrides sip_ip/rtp_ip when set
    outbound_international_allow: str = ""  # env MILLICALL_OUTBOUND_INTERNATIONAL_ALLOW; comma-separated prefixes

    esl_host: str = "127.0.0.1"
    esl_port: int = 8021
    esl_timeout_seconds: float = 5.0
    event_socket_ip: str = "127.0.0.1"

    session_cookie_name: str = "millicall_session"
    session_max_age: int = 60 * 60 * 24 * 7
    cookie_secure: bool = True
    cookie_samesite: str = "lax"


@lru_cache
def get_settings() -> Settings:
    return Settings()
