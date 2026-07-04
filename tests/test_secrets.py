import pytest

from millicall.gen import generate_password, generate_sip_password
from millicall.secrets_store import load_or_create_secrets


def test_secrets_generated_and_persisted(tmp_path) -> None:
    s1 = load_or_create_secrets(tmp_path)
    assert len(s1.session_secret) >= 32
    assert len(s1.esl_password) >= 16
    assert len(s1.master_key) >= 32
    path = tmp_path / "secrets.json"
    assert path.exists()
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_secrets_are_stable(tmp_path) -> None:
    s1 = load_or_create_secrets(tmp_path)
    s2 = load_or_create_secrets(tmp_path)
    assert s1.session_secret == s2.session_secret
    assert s1.esl_password == s2.esl_password
    assert s1.master_key == s2.master_key


def test_corrupt_secrets_raises_runtime_error(tmp_path) -> None:
    path = tmp_path / "secrets.json"
    path.write_text('{"broken"', encoding="utf-8")
    with pytest.raises(RuntimeError, match=str(path)):
        load_or_create_secrets(tmp_path)


def test_generators_are_random_and_alnum() -> None:
    a = generate_password()
    b = generate_password()
    assert a != b
    assert len(a) == 24
    assert a.isalnum()
    assert generate_sip_password().isalnum()
