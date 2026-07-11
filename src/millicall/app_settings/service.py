"""管理画面から編集可能なアプリ設定のマージ層（SettingsService）。

env ベースの ``Settings``（``MILLICALL_`` プレフィックス）をデフォルトとし、
``app_settings`` テーブルに保存された値があればそれを優先する読み取り層。

設計方針:
  - 編集可能キーは ``EDITABLE_SETTINGS`` の allowlist に限定する（それ以外は保存不可）。
  - 秘密値（smtp_password / phone_admin_password）は SecretBox（Fernet）で暗号化して保存し、
    読み取り時に復号する。API レスポンス・監査ログには実値を出さない（router 側の責務）。
  - 値は JSON エンコードで保存し、読み取り時に Settings のフィールド型（pydantic）で検証する。
  - VAD 等は WS 接続毎に読まれる高頻度パスのため、実効 Settings をキャッシュし、
    書き込み時に ``invalidate()`` で無効化する。
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from millicall.config import Settings
from millicall.crypto import SecretBox
from millicall.models import AppSetting

logger = logging.getLogger("millicall.app_settings")


@dataclass(frozen=True)
class SettingMeta:
    """編集可能キーのメタデータ（管理画面のセクション分類と秘密値フラグ）。"""

    category: str
    secret: bool = False


# 管理画面から編集可能なキーの allowlist。
# ここに無いキー（database_url / ESL / SIP バインド等のインフラ設定）は .env 専用のまま。
EDITABLE_SETTINGS: dict[str, SettingMeta] = {
    # --- SSO / プロビジョニング ---
    "saml_enabled": SettingMeta("sso"),
    "saml_sp_entity_id": SettingMeta("sso"),
    "saml_sp_acs_url": SettingMeta("sso"),
    "saml_idp_entity_id": SettingMeta("sso"),
    "saml_idp_sso_url": SettingMeta("sso"),
    "saml_idp_x509_cert": SettingMeta("sso"),
    "saml_default_role": SettingMeta("sso"),
    "saml_allowed_clock_skew_seconds": SettingMeta("sso"),
    "scim_enabled": SettingMeta("sso"),
    # --- メール (SMTP) ---
    "smtp_host": SettingMeta("email"),
    "smtp_port": SettingMeta("email"),
    "smtp_username": SettingMeta("email"),
    "smtp_password": SettingMeta("email", secret=True),
    "smtp_from": SettingMeta("email"),
    "smtp_starttls": SettingMeta("email"),
    "smtp_timeout": SettingMeta("email"),
    # --- 認証ポリシー ---
    "totp_required": SettingMeta("auth_policy"),
    "totp_ticket_max_age": SettingMeta("auth_policy"),
    "login_max_attempts": SettingMeta("auth_policy"),
    "login_username_max_attempts": SettingMeta("auth_policy"),
    "login_lockout_seconds": SettingMeta("auth_policy"),
    "session_max_age": SettingMeta("auth_policy"),
    # --- 音声 AI チューニング ---
    "vad_mode": SettingMeta("voice_ai"),
    "vad_min_rms": SettingMeta("voice_ai"),
    "playback_timeout_sec": SettingMeta("voice_ai"),
    # --- 電話運用 ---
    "outbound_international_allow": SettingMeta("telephony"),
    "sip_reject_anonymous": SettingMeta("telephony"),
    "mcp_default_agent_id": SettingMeta("telephony"),
    "phone_admin_username": SettingMeta("telephony"),
    "phone_admin_password": SettingMeta("telephony", secret=True),
    "mcp_enabled": SettingMeta("telephony"),
    # --- ネットワーク（外向き） ---
    "tailscale_serve_enabled": SettingMeta("network"),
}

SECRET_KEYS: frozenset[str] = frozenset(k for k, m in EDITABLE_SETTINGS.items() if m.secret)

# FreeSWITCH 設定の再生成が必要なキー（PUT /api/settings 後に dialplan を再生成する）。
TELEPHONY_REGEN_KEYS: frozenset[str] = frozenset(
    {"outbound_international_allow", "sip_reject_anonymous"}
)

# 国際発信プレフィックスは 2〜8 桁の数字のみ許可（telephony/service.py と同一規則）。
_PREFIX_RE = re.compile(r"^[0-9]{2,8}$")


class SettingValidationError(ValueError):
    """設定値の検証エラー。API 層で 400 に変換する。"""

    def __init__(self, key: str, message: str) -> None:
        super().__init__(f"{key}: {message}")
        self.key = key


def _check_range(key: str, value: object, checker: Callable[[int | float], bool], msg: str) -> None:
    """数値レンジ検証のヘルパー。範囲外なら SettingValidationError を送出する。"""
    if isinstance(value, int | float) and not checker(value):
        raise SettingValidationError(key, msg)


def _validate_extra(key: str, value: object) -> None:
    """型検証に加えたキー固有の追加検証（レンジ・書式）。"""
    if key == "outbound_international_allow" and isinstance(value, str):
        for p in (s.strip() for s in value.split(",") if s.strip()):
            if not _PREFIX_RE.match(p):
                raise SettingValidationError(
                    key, f"無効な国際発信プレフィックスです: '{p}'（2〜8桁の数字のみ許可）"
                )
    elif key == "vad_mode":
        _check_range(key, value, lambda v: 0 <= v <= 3, "0〜3 の整数で指定してください")
    elif key == "saml_default_role":
        if value not in ("user", "admin"):
            raise SettingValidationError(key, "user または admin を指定してください")
    elif key in ("smtp_port",):
        _check_range(key, value, lambda v: 1 <= v <= 65535, "1〜65535 で指定してください")
    elif key in (
        "smtp_timeout",
        "totp_ticket_max_age",
        "login_max_attempts",
        "login_username_max_attempts",
        "login_lockout_seconds",
        "session_max_age",
        "playback_timeout_sec",
    ):
        _check_range(key, value, lambda v: v > 0, "正の値で指定してください")
    elif key in ("vad_min_rms", "saml_allowed_clock_skew_seconds"):
        _check_range(key, value, lambda v: v >= 0, "0 以上の値で指定してください")


def validate_setting_value(key: str, value: object) -> object:
    """allowlist キーの値を Settings のフィールド型で検証し、正規化済みの値を返す。

    Raises:
        SettingValidationError: キーが allowlist 外、または値が不正な場合。
    """
    if key not in EDITABLE_SETTINGS:
        raise SettingValidationError(key, "管理画面から変更できない設定キーです")
    if key in SECRET_KEYS:
        # 秘密値は文字列のみ受け付ける（bool/int 等の誤送信を弾く）。
        if not isinstance(value, str):
            raise SettingValidationError(key, "秘密値は文字列で指定してください")
        return value
    annotation = Settings.model_fields[key].annotation
    try:
        validated = TypeAdapter(annotation).validate_python(value)
    except Exception as exc:  # noqa: BLE001 — pydantic の検証エラーを 1 種類に集約
        raise SettingValidationError(key, f"値の型が不正です（期待: {annotation}）") from exc
    _validate_extra(key, validated)
    return validated


class SettingsService:
    """env の Settings をデフォルトに app_settings の上書きをマージする読み取り層。

    ``effective()`` は実効 Settings（DB 上書き適用済み・秘密値は復号済み）を返す。
    結果はキャッシュされ、``invalidate()``（書き込み後）で破棄される。
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        base: Settings,
        secret_box: SecretBox,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._base = base
        self._box = secret_box
        self._cache: Settings | None = None
        self._overridden: frozenset[str] = frozenset()
        self._lock = asyncio.Lock()

    @property
    def base(self) -> Settings:
        """env 由来のベース Settings（.env 専用キーの参照用）。"""
        return self._base

    def invalidate(self) -> None:
        """キャッシュを破棄する。書き込みコミット後に呼ぶこと。"""
        self._cache = None

    async def effective(self) -> Settings:
        """DB 上書きを適用した実効 Settings を返す（キャッシュあり）。"""
        cached = self._cache
        if cached is not None:
            return cached
        async with self._lock:
            if self._cache is None:
                await self._load()
            assert self._cache is not None
            return self._cache

    async def overridden_keys(self) -> frozenset[str]:
        """DB で上書きされているキー集合を返す。"""
        await self.effective()
        return self._overridden

    async def _load(self) -> None:
        """app_settings から allowlist キーを読み、実効 Settings を組み立てる。"""
        async with self._sessionmaker() as session:
            rows = await session.scalars(
                select(AppSetting).where(AppSetting.key.in_(EDITABLE_SETTINGS.keys()))
            )
            overrides: dict[str, object] = {}
            for row in rows:
                try:
                    overrides[row.key] = self._decode(row.key, row.value)
                except Exception:  # noqa: BLE001 — 手動編集等で壊れた行は env 値へフォールバック
                    logger.warning("app_settings の値をデコードできません（無視）: key=%s", row.key)
        # 書き込み時に検証済みの値のみ格納されているため、model_copy(update=) で十分。
        self._cache = self._base.model_copy(update=overrides)
        self._overridden = frozenset(overrides)

    def _encode(self, key: str, value: object) -> str:
        """DB 格納形式へエンコードする。秘密値は SecretBox 暗号化、それ以外は JSON。"""
        if key in SECRET_KEYS:
            assert isinstance(value, str)
            return self._box.encrypt(value)
        return json.dumps(value, ensure_ascii=False)

    def _decode(self, key: str, raw: str) -> object:
        """DB 格納値をデコードする。読み取り側でも型検証を行う（手動編集への防御）。"""
        if key in SECRET_KEYS:
            return self._box.decrypt(raw)
        value = json.loads(raw)
        validated = TypeAdapter(Settings.model_fields[key].annotation).validate_python(value)
        # 手動編集で書式不正（例: 国際発信プレフィックス）が混入しても起動を止めないよう、
        # 読み取り側でもキー固有検証を行う（失敗時は _load が env 値へフォールバックする）。
        _validate_extra(key, validated)
        return validated

    async def apply_update(
        self,
        session: AsyncSession,
        values: dict[str, object],
        reset: list[str] | None = None,
    ) -> dict[str, object]:
        """設定の上書きを検証して DB に反映する（commit は呼び出し元の責務）。

        Args:
            session: 呼び出し元が管理する AsyncSession（監査ログと同一トランザクション）。
            values: 上書きするキーと値。allowlist 外・型不正は SettingValidationError。
            reset: 上書きを削除して env デフォルトへ戻すキーのリスト。

        Returns:
            検証済みの更新値（key -> validated value）。監査ログ用（秘密値のマスクは
            呼び出し元で行うこと）。

        Note:
            commit 後に必ず ``invalidate()`` を呼んでキャッシュを破棄すること。
        """
        reset = reset or []
        for key in reset:
            if key not in EDITABLE_SETTINGS:
                raise SettingValidationError(key, "管理画面から変更できない設定キーです")
        validated = {key: validate_setting_value(key, value) for key, value in values.items()}

        for key, value in validated.items():
            row = await session.get(AppSetting, key)
            encoded = self._encode(key, value)
            if row is None:
                session.add(AppSetting(key=key, value=encoded, description="管理画面から設定"))
            else:
                row.value = encoded
        for key in reset:
            row = await session.get(AppSetting, key)
            if row is not None:
                await session.delete(row)
        return validated


async def effective_settings(state) -> Settings:
    """app.state から実効 Settings を返すヘルパー。

    settings_service が未設定の場合（フェイク state を使う単体テスト等）は
    ``state.settings`` にフォールバックする。本番経路では lifespan が必ず
    settings_service をセットする。
    """
    svc = getattr(state, "settings_service", None)
    if svc is not None:
        return await svc.effective()
    return state.settings
