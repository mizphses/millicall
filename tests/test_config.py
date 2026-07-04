from pathlib import Path

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
