import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ValidationError

from millicall.gen import generate_password


class Secrets(BaseModel):
    session_secret: str
    master_key: str
    esl_password: str


def load_or_create_secrets(data_dir: Path) -> Secrets:
    path = data_dir / "secrets.json"
    if path.exists():
        try:
            return Secrets.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, json.JSONDecodeError, KeyError) as exc:
            raise RuntimeError(
                f"secrets ファイルが壊れています: {path} — "
                "バックアップから復元するか、全シークレットを作り直す場合は"
                "このファイルを削除して再起動してください"
            ) from exc

    data_dir.mkdir(parents=True, exist_ok=True)
    secrets = Secrets(
        session_secret=generate_password(48),
        master_key=generate_password(48),
        esl_password=generate_password(32),
    )

    fd, tmp = tempfile.mkstemp(dir=data_dir)
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(secrets.model_dump_json(indent=2))
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise

    return secrets
