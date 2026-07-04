from pathlib import Path

from pydantic import BaseModel

from millicall.gen import generate_password


class Secrets(BaseModel):
    session_secret: str
    master_key: str
    esl_password: str


def load_or_create_secrets(data_dir: Path) -> Secrets:
    path = data_dir / "secrets.json"
    if path.exists():
        return Secrets.model_validate_json(path.read_text(encoding="utf-8"))

    data_dir.mkdir(parents=True, exist_ok=True)
    secrets = Secrets(
        session_secret=generate_password(48),
        master_key=generate_password(48),
        esl_password=generate_password(32),
    )
    path.write_text(secrets.model_dump_json(indent=2), encoding="utf-8")
    path.chmod(0o600)
    return secrets
