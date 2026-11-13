"""secrets.json が無いときの鍵生成ガード（issue #87 L6）。

暗号化済みの値がある DB に対して secrets.json が無い場合、新しい鍵を黙って作ると
既存の暗号文が全て読めなくなり、以後の保存値も誤った鍵で暗号化される。
その場合は鍵を作らず起動を失敗させる。空 DB（新規インストール）では従来どおり生成する。
"""

import sqlite3

import pytest

from millicall.config import Settings
from millicall.db_migrations import upgrade_to_head
from millicall.main import create_app
from millicall.secrets_store import (
    MissingSecretsError,
    database_has_encrypted_data,
    load_or_create_secrets,
)


def _db(tmp_path):
    db = tmp_path / "test.db"
    upgrade_to_head(f"sqlite+aiosqlite:///{db}")
    return db


def test_missing_db_file_is_empty(tmp_path):
    assert database_has_encrypted_data(tmp_path / "nope.db") is False


def test_db_without_tables_is_empty(tmp_path):
    db = tmp_path / "x.db"
    sqlite3.connect(db).close()
    assert database_has_encrypted_data(db) is False


def test_migrated_empty_db_is_empty(tmp_path):
    assert database_has_encrypted_data(_db(tmp_path)) is False


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO extensions (number, display_name, sip_password_encrypted, enabled)"
        " VALUES ('1001', 'A', 'gAAAA-token', 1)",
        "INSERT INTO trunks (name, display_name, host, username, password_encrypted)"
        " VALUES ('t', 'T', 'h', 'u', 'gAAAA-token')",
        "INSERT INTO providers (name, type, kind, config_json, api_key_encrypted, enabled)"
        " VALUES ('p', 'openai', 'llm', '{}', 'gAAAA-token', 1)",
        "INSERT INTO app_settings (key, value) VALUES ('smtp_password', 'gAAAA-token')",
    ],
)
def test_encrypted_rows_are_detected(tmp_path, sql):
    db = _db(tmp_path)
    con = sqlite3.connect(db)
    con.execute(sql)
    con.commit()
    con.close()
    assert database_has_encrypted_data(db) is True


def test_non_secret_settings_and_empty_trunk_password_are_ignored(tmp_path):
    db = _db(tmp_path)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO app_settings (key, value) VALUES ('smtp_host', 'mail.example')")
    con.execute(
        "INSERT INTO trunks (name, display_name, host, username, password_encrypted)"
        " VALUES ('t', 'T', 'h', 'u', '')"
    )
    con.commit()
    con.close()
    assert database_has_encrypted_data(db) is False


def test_load_or_create_refuses_new_key_for_encrypted_db(tmp_path):
    db = _db(tmp_path)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO extensions (number, display_name, sip_password_encrypted, enabled)"
        " VALUES ('1001', 'A', 'gAAAA-token', 1)"
    )
    con.commit()
    con.close()
    with pytest.raises(MissingSecretsError, match="secrets.json をバックアップから"):
        load_or_create_secrets(tmp_path, db_path=db)
    assert not (tmp_path / "secrets.json").exists()


def test_load_or_create_generates_for_empty_db(tmp_path):
    db = _db(tmp_path)
    secrets = load_or_create_secrets(tmp_path, db_path=db)
    assert secrets.master_key
    assert (tmp_path / "secrets.json").exists()


async def test_app_startup_fails_when_secrets_lost(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=0.2,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        from millicall.crypto import SecretBox
        from millicall.models import Extension

        async with app.state.sessionmaker() as session:
            session.add(
                Extension(
                    number="1001",
                    display_name="A",
                    sip_password_encrypted=SecretBox(app.state.secrets.master_key).encrypt("x"),
                )
            )
            await session.commit()
    (tmp_path / "secrets.json").unlink()

    app2 = create_app(settings)
    with pytest.raises(MissingSecretsError):
        async with app2.router.lifespan_context(app2):
            pass
    assert not (tmp_path / "secrets.json").exists()
