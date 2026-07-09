from pathlib import Path

import pytest

from millicall.config import Settings, get_settings


def test_defaults() -> None:
    s = Settings()
    assert s.database_url.startswith("sqlite+aiosqlite")
    assert s.session_cookie_name == "millicall_session"
    assert s.cookie_secure is True
    assert s.cookie_samesite == "lax"
    assert s.esl_port == 8021
    assert s.sip_port == 5060


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("MILLICALL_SIP_DOMAIN", "192.168.1.10")
    monkeypatch.setenv("MILLICALL_COOKIE_SECURE", "false")
    s = Settings()
    assert s.sip_domain == "192.168.1.10"
    assert s.cookie_secure is False


def test_get_settings_cached() -> None:
    assert get_settings() is get_settings()


def test_kwargs_override(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path, cookie_secure=False)
    assert s.data_dir == tmp_path


def test_tts_cache_dir_default_is_absolute() -> None:
    """デフォルト値（相対パス）が絶対パスに解決されることを確認する。

    FreeSWITCH に渡すパスは絶対パスでなければならない。相対パスのまま渡すと
    FreeSWITCH が sound_prefix を前置してファイルを見つけられず、TTS が無音になる。
    """
    s = Settings()
    assert s.tts_cache_dir.is_absolute(), (
        f"tts_cache_dir should be absolute, got: {s.tts_cache_dir}"
    )


def test_tts_cache_dir_explicit_relative_is_resolved() -> None:
    """明示的に相対パスを指定しても絶対パスに解決されることを確認する。"""
    s = Settings(tts_cache_dir=Path("some/relative/path"))  # type: ignore[arg-type]
    assert s.tts_cache_dir.is_absolute(), (
        f"tts_cache_dir should be absolute, got: {s.tts_cache_dir}"
    )
    # resolve() した結果なので、パスの末尾は元のパス末尾を含む
    assert s.tts_cache_dir.parts[-1] == "path"


def test_tts_cache_dir_explicit_absolute_unchanged(tmp_path: Path) -> None:
    """絶対パスを指定した場合はそのまま保持されることを確認する。"""
    s = Settings(tts_cache_dir=tmp_path / "tts")  # type: ignore[arg-type]
    assert s.tts_cache_dir == (tmp_path / "tts").resolve()


@pytest.mark.parametrize(
    "relative_path",
    [
        "data/freeswitch/tts",
        "tts",
        "a/b/c/d",
    ],
)
def test_tts_cache_dir_various_relative_paths_resolved(relative_path: str) -> None:
    """様々な相対パスが絶対パスに変換されることを確認する。"""
    s = Settings(tts_cache_dir=Path(relative_path))  # type: ignore[arg-type]
    assert s.tts_cache_dir.is_absolute(), (
        f"tts_cache_dir should be absolute for input {relative_path!r}, got: {s.tts_cache_dir}"
    )
