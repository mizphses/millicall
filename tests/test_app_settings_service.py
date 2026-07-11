"""SettingsService（設定マージ層）のテスト。

env の Settings をデフォルトとし、app_settings テーブルの値が優先されること、
秘密値の暗号化保存、キャッシュ無効化、allowlist / 型 / レンジ検証を確認する。
"""

import pytest

from millicall.app_settings.service import (
    EDITABLE_SETTINGS,
    SECRET_KEYS,
    SettingsService,
    SettingValidationError,
    effective_settings,
)
from millicall.crypto import SecretBox
from millicall.models import AppSetting


def _make_service(app) -> SettingsService:
    """app fixture から SettingsService を組み立てる。"""
    return SettingsService(
        app.state.sessionmaker,
        app.state.settings,
        SecretBox(app.state.secrets.master_key),
    )


async def _apply(app, svc: SettingsService, values: dict, reset: list[str] | None = None) -> None:
    """apply_update + commit + invalidate をまとめて行うテストヘルパー。"""
    async with app.state.sessionmaker() as session:
        await svc.apply_update(session, values, reset)
        await session.commit()
    svc.invalidate()


async def test_effective_returns_env_defaults_when_no_overrides(app):
    """DB 上書きが無い場合は env（Settings）のデフォルト値をそのまま返す。"""
    svc = _make_service(app)
    eff = await svc.effective()
    assert eff.saml_enabled is app.state.settings.saml_enabled
    assert eff.vad_mode == app.state.settings.vad_mode
    assert await svc.overridden_keys() == frozenset()


async def test_apply_update_overrides_env_defaults(app):
    """bool / int / str の上書きが実効 Settings に反映される。"""
    svc = _make_service(app)
    await _apply(
        app,
        svc,
        {"saml_enabled": True, "vad_min_rms": 500, "smtp_host": "smtp.example.com"},
    )
    eff = await svc.effective()
    assert eff.saml_enabled is True
    assert eff.vad_min_rms == 500
    assert eff.smtp_host == "smtp.example.com"
    assert await svc.overridden_keys() == frozenset({"saml_enabled", "vad_min_rms", "smtp_host"})
    # 上書きしていないキーは env のまま
    assert eff.scim_enabled is app.state.settings.scim_enabled


async def test_effective_is_cached_until_invalidate(app):
    """effective() はキャッシュされ、invalidate() 後に再読み込みされる。"""
    svc = _make_service(app)
    eff1 = await svc.effective()
    eff2 = await svc.effective()
    assert eff1 is eff2

    # invalidate せずに DB を直接書き換えても古いキャッシュが返る
    async with app.state.sessionmaker() as session:
        await svc.apply_update(session, {"vad_mode": 3})
        await session.commit()
    assert (await svc.effective()).vad_mode == eff1.vad_mode

    svc.invalidate()
    assert (await svc.effective()).vad_mode == 3


async def test_secret_value_is_encrypted_at_rest(app):
    """秘密値は平文で DB に置かれず、実効 Settings では復号済みで読める。"""
    svc = _make_service(app)
    await _apply(app, svc, {"smtp_password": "s3cret-pass"})

    async with app.state.sessionmaker() as session:
        row = await session.get(AppSetting, "smtp_password")
    assert row is not None
    assert "s3cret-pass" not in row.value
    # SecretBox で復号できる（Fernet トークンとして格納されている）
    assert SecretBox(app.state.secrets.master_key).decrypt(row.value) == "s3cret-pass"

    eff = await svc.effective()
    assert eff.smtp_password == "s3cret-pass"


async def test_apply_update_rejects_non_allowlisted_key(app):
    """allowlist 外のキー（インフラ設定）は保存できない。"""
    svc = _make_service(app)
    async with app.state.sessionmaker() as session:
        with pytest.raises(SettingValidationError):
            await svc.apply_update(session, {"database_url": "sqlite://evil"})
        with pytest.raises(SettingValidationError):
            await svc.apply_update(session, {}, reset=["esl_host"])


async def test_apply_update_rejects_invalid_type_and_range(app):
    """型不正・レンジ外・書式不正の値は SettingValidationError になる。"""
    svc = _make_service(app)
    async with app.state.sessionmaker() as session:
        with pytest.raises(SettingValidationError):
            await svc.apply_update(session, {"smtp_port": "not-a-number"})
        with pytest.raises(SettingValidationError):
            await svc.apply_update(session, {"vad_mode": 5})
        with pytest.raises(SettingValidationError):
            await svc.apply_update(session, {"outbound_international_allow": "abc,010"})
        with pytest.raises(SettingValidationError):
            await svc.apply_update(session, {"saml_default_role": "superuser"})
        with pytest.raises(SettingValidationError):
            await svc.apply_update(session, {"session_max_age": 0})
        # 秘密値に文字列以外は不可
        with pytest.raises(SettingValidationError):
            await svc.apply_update(session, {"smtp_password": 12345})


async def test_reset_restores_env_default(app):
    """reset で上書きを削除すると env デフォルトに戻る。"""
    svc = _make_service(app)
    await _apply(app, svc, {"login_max_attempts": 3})
    assert (await svc.effective()).login_max_attempts == 3

    await _apply(app, svc, {}, reset=["login_max_attempts"])
    eff = await svc.effective()
    assert eff.login_max_attempts == app.state.settings.login_max_attempts
    assert await svc.overridden_keys() == frozenset()


async def test_broken_row_falls_back_to_env(app):
    """DB 手動編集等で壊れた行はデコード失敗として無視し、env 値へフォールバックする。"""
    svc = _make_service(app)
    async with app.state.sessionmaker() as session:
        session.add(AppSetting(key="vad_mode", value="not-json{{"))
        await session.commit()
    eff = await svc.effective()
    assert eff.vad_mode == app.state.settings.vad_mode


async def test_effective_settings_helper_falls_back_without_service():
    """settings_service が無いフェイク state では state.settings を返す。"""

    class _State:
        settings = object()

    state = _State()
    assert await effective_settings(state) is state.settings


async def test_effective_settings_helper_uses_service(app):
    """settings_service がある場合は実効 Settings を返す。"""
    svc = _make_service(app)
    await _apply(app, svc, {"vad_min_rms": 42})

    class _State:
        settings = app.state.settings
        settings_service = svc

    eff = await effective_settings(_State())
    assert eff.vad_min_rms == 42


def test_secret_keys_are_subset_of_editable():
    """秘密キー定義が allowlist の部分集合であることを保証する。"""
    assert frozenset(EDITABLE_SETTINGS) >= SECRET_KEYS
    assert frozenset({"smtp_password", "phone_admin_password"}) == SECRET_KEYS
