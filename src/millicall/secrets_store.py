import json
import os
import sqlite3
import tempfile
from pathlib import Path

from pydantic import BaseModel, ValidationError

from millicall.gen import generate_password


class Secrets(BaseModel):
    session_secret: str
    master_key: str
    esl_password: str


# master_key で暗号化した値を持つ列。どれかに値があれば、その DB は既存の鍵に依存している。
_ENCRYPTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("extensions", "sip_password_encrypted"),
    ("trunks", "password_encrypted"),
    ("providers", "api_key_encrypted"),
    ("network_config", "tailscale_auth_key_encrypted"),
    ("users", "totp_secret"),
)


class MissingSecretsError(RuntimeError):
    """暗号化済みデータがある DB に対して secrets.json が無い（鍵の取り違え・喪失）。"""


def database_has_encrypted_data(db_path: Path) -> bool:
    """DB に master_key で暗号化された値が 1 件でもあるかを、鍵の生成前に直接確認する。

    マイグレーション前に呼ぶため ORM は使わず sqlite3 で読む。テーブル・列が無ければ
    その項目は空として扱う。DB を読めない場合は「ある」とみなす（鍵を作らない側へ倒す）。
    """
    if not db_path.exists():
        return False
    from millicall.app_settings.service import SECRET_KEYS

    try:
        con = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return True
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, column in _ENCRYPTED_COLUMNS:
            if table not in tables:
                continue
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                continue
            row = con.execute(
                f"SELECT 1 FROM {table} WHERE {column} IS NOT NULL AND {column} != '' LIMIT 1"
            ).fetchone()
            if row is not None:
                return True
        if "app_settings" in tables and SECRET_KEYS:
            marks = ",".join("?" for _ in SECRET_KEYS)
            row = con.execute(
                f"SELECT 1 FROM app_settings WHERE key IN ({marks}) AND value != '' LIMIT 1",
                tuple(SECRET_KEYS),
            ).fetchone()
            if row is not None:
                return True
        return False
    except sqlite3.Error:
        return True
    finally:
        con.close()


def load_or_create_secrets(data_dir: Path, *, db_path: Path | None = None) -> Secrets:
    """secrets.json を読む。無ければ生成する。

    db_path を渡した場合、その DB に暗号化済みの値があるのに secrets.json が無ければ
    新しい鍵を作らず MissingSecretsError を送出する。黙って鍵を作ると既存の暗号文が
    全て読めなくなり、その後に保存した値は誤った鍵で暗号化されてしまうため。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    # mkdir(exist_ok=True) does not tighten existing directory permissions.
    os.chmod(data_dir, 0o700)
    path = data_dir / "secrets.json"
    if path.exists():
        os.chmod(path, 0o600)
        try:
            return Secrets.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, json.JSONDecodeError, KeyError) as exc:
            raise RuntimeError(
                f"secrets ファイルが壊れています: {path} — "
                "バックアップから復元するか、全シークレットを作り直す場合は"
                "このファイルを削除して再起動してください"
            ) from exc

    if db_path is not None and database_has_encrypted_data(db_path):
        raise MissingSecretsError(
            f"secrets ファイルがありません: {path} — DB（{db_path}）には既存の master_key で"
            "暗号化された値があるため、新しい鍵は作成しません。secrets.json をバックアップから"
            "戻してください。鍵を失った場合の手順は docs/ops/deployment.md の"
            "「SIP 認証情報の再生成と復旧」を参照してください"
        )

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
        try:
            # Publish without replacing a key another worker has already created.
            os.link(tmp, path)
        except FileExistsError:
            return load_or_create_secrets(data_dir, db_path=db_path)
    finally:
        os.unlink(tmp)

    return secrets
