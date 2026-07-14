import json
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
        # list 型フィールド（mcp_allowed_hosts / sip_trusted_cidrs）を env から
        # カンマ区切り文字列で渡せるようにする。無効だと pydantic-settings が
        # 複合型の env 値を先に json.loads しようとし、"a,b" のような値で
        # SettingsError を送出して core が起動時にクラッシュ→unhealthy になる。
        # 両フィールドとも mode="before" バリデータでカンマ分割するため decode は不要。
        enable_decoding=False,
    )

    data_dir: Path = Path("data")
    database_url: str = "sqlite+aiosqlite:///data/millicall.db"
    # core が待受ける HTTP ポート。本番 compose は 80。dev で変える場合は MILLICALL_HTTP_PORT。
    # プロビジョニング URL(option 66)・media_ws・healthcheck の既定はこのポートから導出する。
    # TLS/443 はフロント(Cloudflare Tunnel / Tailscale Serve)に委譲し core は平文のみ配信する。
    http_port: int = 80
    # core が HTTP を待受けるバインドアドレス（デフォルト: 0.0.0.0）。
    # nftables INPUT フィルタ（C2a）が LAN CIDR 限定の第一防衛ラインとなるため、
    # 既定は 0.0.0.0 のまま閉域 LAN からの到達性を維持する。
    # Cloudflare Tunnel / Tailscale Serve 専用デプロイでは 127.0.0.1 に設定すること:
    #   MILLICALL_HTTP_BIND=127.0.0.1
    # これにより LAN/WAN への HTTP 露出を完全に排除し、フロントのみが localhost 経由でアクセスする。
    http_bind: str = "0.0.0.0"
    # SPA（管理 GUI）の配信元。存在するときのみ StaticFiles + SPA fallback を有効化する。
    # core イメージでは Dockerfile が /app/static にビルド済み dist を配置する。
    # 開発時は既定パスが存在しないため無効化され、Vite dev server + proxy を使う。
    static_dir: Path = Path("static")
    fs_config_dir: Path = Path("data/freeswitch")
    # TTS 音声を書き出す共有ディレクトリ（FreeSWITCH コンテナにも同一パスで bind mount）
    tts_cache_dir: Path = Path("data/freeswitch/tts")
    # FreeSWITCH の mod_audio_stream が core の音声受け WS へ接続するベース URL。
    # host ネットワーキング前提。未設定(空文字)なら http_port から ws://127.0.0.1:<port> を導出する。
    # 明示的に値を設定した場合はそれを優先する（パス /media/audio-fork/<uuid> が付与される）。
    media_ws_base_url: str = ""

    sip_domain: str = "millicall.local"
    sip_port: int = 5060
    external_sip_port: int = 5080
    sip_ip: str = "auto"
    rtp_ip: str = "auto"
    sip_bind_ip: str | None = None  # env MILLICALL_SIP_BIND_IP; overrides sip_ip/rtp_ip when set
    # SIP 種別トランク（インターネット越しの SIP プロバイダ）の ext-sip-ip/ext-rtp-ip に使う。
    # env MILLICALL_SIP_EXTERNAL_IP。既定 "auto-nat" は NAT 内でも公開 IP でも機能する。
    # 固定にしたい場合は "stun:stun.freeswitch.org" や明示グローバル IP を指定する。
    sip_external_ip: str = "auto-nat"
    outbound_international_allow: str = (
        ""  # env MILLICALL_OUTBOUND_INTERNATIONAL_ALLOW; comma-separated prefixes
    )

    esl_host: str = "127.0.0.1"
    esl_port: int = 8021
    esl_timeout_seconds: float = 5.0
    event_socket_ip: str = "127.0.0.1"
    # play_file が PLAYBACK_STOP イベントを待つ最大秒数。
    # FreeSWITCH 側で再生が失敗してイベントが来ない場合に無限ブロックするのを防ぐ。
    # タイムアウト後は警告ログを出し、例外を出さずに return して会話ループを継続させる。
    playback_timeout_sec: float = 30.0

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

    # --- SCIM 2.0 プロビジョニング (Phase 6 Task 5) ---
    # True のとき /scim/v2/* エンドポイントが有効になる。
    # False（デフォルト）の場合、全 SCIM エンドポイントは 404 を返す（feature off）。
    scim_enabled: bool = False
    # SCIM グループ displayName → millicall ロールのマッピング。
    # 例: {"millicall-admins": "admin"}。マップ済みグループに属する origin="scim"
    # ユーザーへ最上位ロール（admin > user）を自動付与する。空 {} で機能オフ。
    # 通常は管理画面（app_settings）から編集する。env から渡す場合は JSON 文字列。
    scim_group_role_map: dict[str, str] = {}

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

    # --- VAD（着信 AI 応対の発話区切り / バージイン） ---
    # webrtcvad の積極度 (0-3)。大きいほど非音声を弾く。
    vad_mode: int = 2
    # 発話とみなす最小 RMS（int16 振幅）。webrtcvad が speech と判定しても、フレーム RMS が
    # この値未満なら無音/回線ノイズとして無視する。再生中の誤バージイン（TTS 途切れ）や
    # 空 STT の暴発を抑える。実測では無音ノイズ RMS≈8、実発話は数百〜数千。0 で無効。
    # 現場に合わせて MILLICALL_VAD_MIN_RMS で無停止調整できる。
    vad_min_rms: int = 200

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

    # --- SIP 多層防御 (Phase 6 Task 7) ---
    # FreeSWITCH ACL "millicall_trusted" に登録する信頼 CIDR リスト。
    # カンマ区切り文字列（env MILLICALL_SIP_TRUSTED_CIDRS）または直接 list[str] で指定可。
    # デフォルト: RFC1918 プライベート全帯域 + loopback。
    # HGW 192.168.1.1 は 192.168.0.0/16 に内包されているため、このデフォルトで実機着信を維持する。
    # WAN からの任意送信元は ACL default="deny" により FreeSWITCH レイヤでも拒否される（nftables に次ぐ第二層）。
    sip_trusted_cidrs: list[str] = [
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.1/32",
    ]

    # !! 重要: デフォルト False !!
    # True にすると着信 caller-ID が anonymous/非通知の呼を CALL_REJECTED で拒否する。
    # NTT ひかり電話 HGW 回線は 186 プレフィックス未付与時に caller-ID が非通知（anonymous）になる。
    # このオプションを True にすると実机着信がすべて拒否される。絶対に本番で True にしないこと。
    sip_reject_anonymous: bool = False

    # --- システム管理 / Docker socket-proxy (Phase 6 Task 8) ---
    # Docker HTTP API エンドポイント（Tecnativa socket-proxy 経由）。
    # core は raw docker.sock に一切触れず、このプロキシ URL のみ使用する。
    # 空文字の場合はシステム管理機能が無効化され、全エンドポイントが 503 を返す。
    # core が network_mode: host のため、プロキシが 127.0.0.1:2375 に bind していれば
    # http://127.0.0.1:2375 でアクセスできる（docker-compose.prod.yml 参照）。
    docker_proxy_url: str = ""
    # 再起動を許可するコンテナ名（compose サービス名）のカンマ区切りリスト。
    # この allowlist 外のコンテナを再起動しようとすると 403 を返す。
    # 任意コンテナの再起動を防ぐ最小権限の強制（raw socket 非接触と組み合わせ）。
    system_managed_containers: str = "core,freeswitch,netd,docker-proxy"

    @field_validator("system_managed_containers", mode="before")
    @classmethod
    def _validate_system_managed_containers(cls, v: object) -> object:
        # カンマ区切り文字列として受け取るが、フィールド自体は str のまま保持する。
        # 利用側は split_managed_containers() を使ってリスト化する。
        if isinstance(v, str):
            return v
        # list が渡された場合（テスト等）はカンマ結合して str に戻す。
        if isinstance(v, list):
            return ",".join(str(x).strip() for x in v if str(x).strip())
        return v

    def split_managed_containers(self) -> list[str]:
        """system_managed_containers をカンマ分割して stripped リストで返す。"""
        return [s.strip() for s in self.system_managed_containers.split(",") if s.strip()]

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
    # 電話機の Web 管理者資格情報（HTTP resync 用）。
    # デフォルトは空文字 — 未設定時は resync を実行しないこと。
    # 実サイトでは env MILLICALL_PHONE_ADMIN_USERNAME/PASSWORD で電話機ごとの値を設定する。
    # resync スキップ判定は provisioning/service.py の resync_phone で行う（M4 エージェント担当）。
    phone_admin_username: str = ""
    phone_admin_password: str = ""

    # --- TLS フロント (任意) ---
    # True のとき netd は tailscale up 成功後に `tailscale serve` を張り、tailnet 上で
    # http://localhost:<http_port> を HTTPS 公開する（管理画面/MCP のリモート公開）。
    # 閉域運用ではインターネット非接続のため無効のまま(平文 LAN のみ)。
    tailscale_serve_enabled: bool = False

    @field_validator("tts_cache_dir", mode="after")
    @classmethod
    def _resolve_tts_cache_dir(cls, v: Path) -> Path:
        # FreeSWITCH は相対パスに sound_prefix を前置するため、必ず絶対パスで渡す。
        # 相対パスが渡された場合は cwd（本番: /app）基準で解決する。
        return v.resolve()

    @field_validator("media_ws_base_url", mode="after")
    @classmethod
    def _derive_media_ws(cls, v: str, info) -> str:
        # 空文字なら http_port から ws://127.0.0.1:<port> を導出する。明示値はそのまま使う。
        if v:
            return v
        port = info.data.get("http_port", 80)
        return f"ws://127.0.0.1:{port}"

    @field_validator("mcp_allowed_hosts", mode="before")
    @classmethod
    def _split_allowed_hosts(cls, v: object) -> object:
        # env からはカンマ区切り文字列で渡せるようにする（既存 outbound_* と同系の運用）。
        if isinstance(v, str):
            return [h.strip() for h in v.split(",") if h.strip()]
        return v

    @field_validator("sip_trusted_cidrs", mode="before")
    @classmethod
    def _split_sip_trusted_cidrs(cls, v: object) -> object:
        # env MILLICALL_SIP_TRUSTED_CIDRS はカンマ区切り文字列で渡せる（mcp_allowed_hosts と同系）。
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return v

    @field_validator("scim_group_role_map", mode="before")
    @classmethod
    def _parse_scim_group_role_map(cls, v: object) -> object:
        # enable_decoding=False のため env からの JSON 文字列は自前でデコードする。
        if isinstance(v, str):
            return json.loads(v) if v.strip() else {}
        return v


def http_port_suffix(port: int) -> str:
    """HTTP ポートを URL に付与する接尾辞を返す。標準ポート 80 は省略する。

    例: 80 -> ""（`http://host/...`）、8000 -> ":8000"（`http://host:8000/...`）
    """
    return "" if port == 80 else f":{port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
