from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy import true as sa_true
from sqlalchemy.orm import Mapped, mapped_column

from millicall.db import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="admin", server_default="admin"
    )
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default="local", server_default="local"
    )
    # Phase 6 (TOTP 2FA) 用に予約。Phase 1 では常に NULL。
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Extension(Base):
    __tablename__ = "extensions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 強ランダム自動生成のみ。ユーザー/API から指定不可。
    sip_password: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Trunk(Base):
    __tablename__ = "trunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # sofia gateway 名にも使う slug（英数と - _ のみ想定）
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    host: Mapped[str] = mapped_column(String(100), nullable=False)  # HGW の LAN 側 IP
    username: Mapped[str] = mapped_column(String(50), nullable=False)  # 認証ID = 内線番号
    # HGW 側で決まるユーザー入力値。平文保存(暗号化は Phase 6)。API レスポンスには出さない。
    password: Mapped[str] = mapped_column(String(128), nullable=False)
    did_number: Mapped[str] = mapped_column(
        String(30), nullable=False, default="", server_default=""
    )
    caller_id: Mapped[str] = mapped_column(  # 表示番号（自局番号）
        String(30), nullable=False, default="", server_default=""
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        """Custom repr that excludes the password field."""
        attrs = [
            f"{k}={v!r}" for k, v in self.__dict__.items()
            if not k.startswith("_") and k != "password"
        ]
        return f"<{self.__class__.__name__}({', '.join(attrs)})>"


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    # RouteTargetType の値。Phase 2 は "extension" のみ。将来 ring_group/workflow/ai_agent。
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # extensions への FK は意図的に張らない: 参照整合性は書き込み時検証のみ（内線削除でダングリングになり得る — dialplan生成側は存在しないターゲットを無視する前提。Phase 5でUI警告を検討）
    target_value: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(30), nullable=False)
    company: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    department: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", server_default=""
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Cdr(Base):
    __tablename__ = "cdr"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_uuid: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    direction: Mapped[str] = mapped_column(
        String(20), nullable=False, default="", server_default=""
    )
    src_number: Mapped[str] = mapped_column(
        String(80), nullable=False, default="", server_default=""
    )
    dst_number: Mapped[str] = mapped_column(
        String(80), nullable=False, default="", server_default=""
    )
    caller_id_name: Mapped[str] = mapped_column(
        String(120), nullable=False, default="", server_default=""
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    billsec_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    hangup_cause: Mapped[str] = mapped_column(
        String(40), nullable=False, default="", server_default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    # 'llm' | 'tts' | 'stt'
    type: Mapped[str] = mapped_column(String(10), nullable=False)
    # 'openai_compatible'|'anthropic'|'gemini'|'voicevox'|'openjtalk'|'whisper'|'google_stt'
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # 非機密設定（base_url/model/voice/engine_url 等）の JSON 文字列
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    # Fernet トークン。APIキー不要な kind では NULL。
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        attrs = [
            f"{k}={v!r}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and k != "api_key_encrypted"
        ]
        return f"<{self.__class__.__name__}({', '.join(attrs)})>"


class AiAgent(Base):
    __tablename__ = "ai_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    greeting: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    llm_provider_id: Mapped[int] = mapped_column(Integer, nullable=False)
    tts_provider_id: Mapped[int] = mapped_column(Integer, nullable=False)
    stt_provider_id: Mapped[int] = mapped_column(Integer, nullable=False)
    max_history: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    silence_end_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=600, server_default="600"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 着信番号。UNIQUE（同 number の Route を自動プロビジョニングする — Phase 4b 裁定#9）。
    number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # ワークフロー既定 TTS プロバイダ（ノードで tts_provider_id 未指定時のフォールバック）。
    default_tts_provider_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # WorkflowDefinition({nodes, edges}) の JSON 文字列。GUI メタ含む生 JSON を保持。
    definition_json: Mapped[str] = mapped_column(
        Text, nullable=False, default='{"nodes": [], "edges": []}',
        server_default='{"nodes": [], "edges": []}',
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CallMessage(Base):
    __tablename__ = "call_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_uuid: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
