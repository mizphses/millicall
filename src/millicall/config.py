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

    # --- 認証 (Phase 6) ---
    # True のとき UI は TOTP 登録を強制する（バックエンドはフラグを公開するのみ；
    # 強制の実施は T9 フロントエンド担当）。
    totp_required: bool = False
    # TOTP チャレンジチケットの有効期間（秒）。ブルートフォース窓を狭めるため短めにする
    # （レビュー M-1）。ステートレス署名チケットのため、この窓 = 総当たり可能時間。
    totp_ticket_max_age: int = 120

    # ログイン試行レート制限（Phase 6 Task 3 / レビュー H-1 で IP・ユーザー名しきい値を分離）
    # IP しきい値（一次防御・低め）: 単一 IP からの総当たりを止める。
    login_max_attempts: int = 10
    # ユーザー名しきい値（二次防御・高め）: 分散総当たりに備える。IP しきい値より高くすることで、
    # 単一 IP の攻撃者は自分の IP が先にロックされ、正規アカウントを容易に DoS ロックアウトできない。
    login_username_max_attempts: int = 30
    # ロックアウト期間（秒）。この期間内の失敗数がしきい値を超えると 429 を返す。
    login_lockout_seconds: int = 300

    # CSRF 保護 (Phase 6 Task 3)
    # double-submit cookie に使用する Cookie 名。non-HttpOnly で JS から読み取れる。
    csrf_cookie_name: str = "millicall_csrf"

    # --- SAML 2.0 SP (Phase 6 Task 4) ---
    # True のとき /saml/* エンドポイントが有効になる。
    saml_enabled: bool = False
    # SP の Entity ID（例: https://millicall.example.com/saml/metadata）。
    saml_sp_entity_id: str = ""
    # Assertion Consumer Service URL（POST binding; 例: https://millicall.example.com/saml/acs）。
    saml_sp_acs_url: str = ""
    # IdP の Entity ID（メタデータ EntityDescriptor/@entityID）。
    saml_idp_entity_id: str = ""
    # IdP の SSO URL（HTTP-Redirect binding; 例: https://idp.example.com/sso）。
    saml_idp_sso_url: str = ""
    # IdP の X.509 証明書（PEM 形式; "-----BEGIN CERTIFICATE-----" から始まる）。
    # この証明書のみを信頼する（out-of-band 事前共有）。
    saml_idp_x509_cert: str = ""
    # SAML SSO で新規作成するユーザーに付与するデフォルトロール。
    saml_default_role: str = "user"
    # 許容するクロック スキュー（秒）。NotBefore/NotOnOrAfter に±この値を加算する。
    saml_allowed_clock_skew_seconds: int = 120

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

    # --- Email 通知 (Phase 4b) ---
    # smtp_host が空文字の場合はメール送信が無効化される。
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    # From アドレス。空の場合は smtp_username にフォールバック。
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_timeout: int = 15

    # --- netd / ネットワーク (Phase 5) ---
    # netd UNIX ドメインソケットのパス（core から netd へのコマンド送信に使用）。
    netd_socket_path: str = "/run/millicall/netd.sock"
    # dnsmasq 再起動コマンド（シェルワード文字列; shlex.split で argv リストに変換して使用）。
    # コンテナ環境では MILLICALL_DNSMASQ_RELOAD_CMD=/usr/local/bin/reload-dnsmasq.sh に上書きする。
    dnsmasq_reload_cmd: str = "systemctl restart dnsmasq"
    # dnsmasq 設定ファイルのパス（netd が書き込む）。
    dnsmasq_conf_path: str = "/etc/dnsmasq.d/millicall.conf"
    # dnsmasq DHCP リースファイルのパス（netd が読み込む）。
    dnsmasq_leases_path: str = "/var/lib/misc/dnsmasq.leases"
    # nftables テーブル名（millicall NAT ルールを格納するテーブル）。
    nftables_table: str = "millicall_nat"
    # 電話機の Web 管理者資格情報（HTTP resync 用）。既定は機種の工場出荷値（公開情報）。
    # 実サイトでは env MILLICALL_PHONE_ADMIN_USERNAME/PASSWORD で上書きすること。
    phone_admin_username: str = "admin"
    phone_admin_password: str = "adminpass"

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
