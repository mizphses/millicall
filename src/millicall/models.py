from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy import false as sa_false
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
        String(20), nullable=False, default="user", server_default="user"
    )
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default="local", server_default="local"
    )
    # UNIQUE（migration 0017）。SQLite は複数 NULL を許容するためローカル既定 admin 等は影響なし。
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    session_epoch: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # SecretBox（Fernet）で暗号化した base32 TOTP シークレット。
    # 平文は /totp/setup レスポンスでのみ返す。ログ・repr・audit に出してはならない。
    totp_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # totp_secret が存在しても verify 完了前は False。ログインゲートに使うのはこちら。
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_false()
    )
    # Argon2 ハッシュ済みリカバリコードの JSON 配列。平文は格納しない。
    recovery_codes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        """秘密フィールド(totp_secret/recovery_codes/hashed_password)を除外したrepr。"""
        hidden = frozenset({"totp_secret", "recovery_codes", "hashed_password"})
        attrs = [
            f"{k}={v!r}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and k not in hidden
        ]
        return f"<{self.__class__.__name__}({', '.join(attrs)})>"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_label: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
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
    # 発信権限ティア: "internal"（内線のみ）/ "domestic"（国内まで）/ "international"（国際可）
    # デフォルトは "domestic"。国際発信はデフォルト禁止（トールフラウド対策 §7）。
    calling_permission: Mapped[str] = mapped_column(
        String(20), nullable=False, default="domestic", server_default="domestic"
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
    # 着信先内線番号（統一番号プラン）。空 = このトランクの着信を受けない。
    # public コンテキストで destination_number(username/did_number) 一致時に
    # この番号へ transfer する。番号の実体は extension/ai_agent/workflow/ring_group のいずれか。
    inbound_extension: Mapped[str] = mapped_column(
        String(20), nullable=False, default="", server_default=""
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
            f"{k}={v!r}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and k != "password"
        ]
        return f"<{self.__class__.__name__}({', '.join(attrs)})>"


class RingGroup(Base):
    """グループ着信（一斉鳴動）。統一番号プランの一員として内線番号を持つ。

    この番号への着信（内線発信・トランク着信の transfer どちらも）で
    メンバー内線が一斉に鳴る。鳴動戦略は一斉のみ。
    """

    __tablename__ = "ring_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class RingGroupMember(Base):
    __tablename__ = "ring_group_members"
    __table_args__ = (Index("ux_ring_group_member", "group_id", "extension_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ring_groups.id", ondelete="CASCADE"), nullable=False
    )
    extension_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("extensions.id", ondelete="CASCADE"), nullable=False
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
    config_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
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
    # 内線番号（統一番号プラン・任意）。NULL = 番号なし（ワークフロー内部からのみ使用）。
    # 一意性は numberplan.assert_number_free で4テーブル横断チェックする。
    number: Mapped[str | None] = mapped_column(String(20), nullable=True, unique=True)
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
        Text,
        nullable=False,
        default='{"nodes": [], "edges": []}',
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


class NetworkConfig(Base):
    """ネットワーク設定（単一行テーブル; 常に id=1 を使用）。tailscale_auth_key_encrypted は repr/ログに出力しない。"""

    __tablename__ = "network_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lan_interface: Mapped[str] = mapped_column(
        String(20), nullable=False, default="enp3s0", server_default="enp3s0"
    )
    lan_ip: Mapped[str] = mapped_column(
        String(45), nullable=False, default="172.20.0.1", server_default="172.20.0.1"
    )
    lan_prefix: Mapped[int] = mapped_column(
        Integer, nullable=False, default=16, server_default="16"
    )
    dhcp_range_start: Mapped[str] = mapped_column(
        String(45), nullable=False, default="172.20.1.1", server_default="172.20.1.1"
    )
    dhcp_range_end: Mapped[str] = mapped_column(
        String(45), nullable=False, default="172.20.254.254", server_default="172.20.254.254"
    )
    dhcp_lease_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=12, server_default="12"
    )
    provisioning_base_url: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default=""
    )
    nat_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    wan_interface: Mapped[str] = mapped_column(
        String(20), nullable=False, default="", server_default=""
    )
    tailscale_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_false()
    )
    # Fernet トークン。Tailscale 無効時は NULL。NEVER store plaintext, NEVER log。
    tailscale_auth_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        attrs = [
            f"{k}={v!r}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and k != "tailscale_auth_key_encrypted"
        ]
        return f"<{self.__class__.__name__}({', '.join(attrs)})>"


class LoginAttempt(Base):
    """ログイン失敗試行の記録（レート制限・ロックアウト用）。

    key + key_type でレート制限の対象を識別する。
    created_at ウィンドウ内のカウントで上限超過を検出する。
    """

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_key_created_at", "key", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # レート制限キー: IP アドレスまたはユーザー名の値
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    # "ip" または "username"
    key_type: Mapped[str] = mapped_column(String(20), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # どのエンドポイントでの失敗か: "login" / "totp"
    action: Mapped[str] = mapped_column(String(30), nullable=False, server_default="login")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Device(Base):
    """物理電話機（IP電話端末）。provision_token は repr/ログに出力しない。"""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 正規化形式: 大文字コロン区切り（AA:BB:CC:DD:EE:FF）
    mac_address: Mapped[str] = mapped_column(String(17), unique=True, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(253), nullable=True)
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extension_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("extensions.id", ondelete="SET NULL"), nullable=True
    )
    provisioned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_false()
    )
    # ワンタイムプロビジョニングトークン。使用後に NULL 化する。NEVER log。
    provision_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        attrs = [
            f"{k}={v!r}"
            for k, v in self.__dict__.items()
            if not k.startswith("_") and k != "provision_token"
        ]
        return f"<{self.__class__.__name__}({', '.join(attrs)})>"
