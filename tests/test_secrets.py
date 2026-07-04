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


def test_generators_are_random_and_alnum() -> None:
    a = generate_password()
    b = generate_password()
    assert a != b
    assert len(a) == 24
    assert a.isalnum()
    assert generate_sip_password().isalnum()
