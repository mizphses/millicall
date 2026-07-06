from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
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
    # SPA（管理 GUI）の配信元。存在するときのみ StaticFiles + SPA fallback を有効化する。
    # core イメージでは Dockerfile が /app/static にビルド済み dist を配置する。
    # 開発時は既定パスが存在しないため無効化され、Vite dev server + proxy を使う。
    static_dir: Path = Path("static")
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

    # --- MCP サーバー (Phase 4a) ---
    # /mcp を有効化するか（False で完全に非マウント）。
    mcp_enabled: bool = True
    # OAuth 2.1 の issuer / resource server URL。RFC8414 メタデータの base。
    # SDK 制約: HTTPS 必須（localhost / 127.0.0.1 のみ http 許可）。本番は https://<host> を env で設定。
    mcp_issuer_url: str = "http://localhost"
    # mod DNS リバインド対策の許可 Host（TransportSecuritySettings.allowed_hosts）。
    # 本番ホスト名を必ず含めること（漏れると /mcp が全拒否される）。
    mcp_allowed_hosts: list[str] = ["localhost", "127.0.0.1"]
    # converse 既定エージェント（Phase 4a Task 4 で使用）。None なら enabled な ai_agents 最小 id。
    mcp_default_agent_id: int | None = None

    @field_validator("mcp_allowed_hosts", mode="before")
    @classmethod
    def _split_allowed_hosts(cls, v: object) -> object:
        # env からはカンマ区切り文字列で渡せるようにする（既存 outbound_* と同系の運用）。
        if isinstance(v, str):
            return [h.strip() for h in v.split(",") if h.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
