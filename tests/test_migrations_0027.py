"""マイグレーション 0027（SIP 認証情報の暗号化保存）のテスト。

- 既存の平文行が SecretBox の暗号文へ移り、旧列が消える
- 移行後の DB ファイル・WAL に平文が残らない（VACUUM + wal_checkpoint(TRUNCATE)）
- 鍵が無ければ（暗号化すべき行がある限り）失敗し、スキーマも変わらない
- downgrade で平文列へ戻る
- alembic CLI 経路（-x secrets_file=...）でも鍵を渡せる
"""

import json
import sqlite3
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config

from millicall.crypto import SecretBox
from millicall.db_migrations import MASTER_KEY_ATTR, upgrade_to_head

_REPO_ROOT = Path(__file__).resolve().parent.parent
_KEY = "k" * 48

# 平文が DB バイト列に残っていないかを探すための、他と衝突しない値。
_EXT_PW = "ExtPlainPw7Q2zLm9Xv4Rt8N"
_TRUNK_PW = "TrunkPlainPw5Hd3Kq0Wz6Yb"


def _cfg(db_path: Path, master_key: str | None = _KEY) -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    if master_key:
        cfg.attributes[MASTER_KEY_ATTR] = master_key
    return cfg


def _seed_0023(db_path: Path) -> None:
    """0023 時点の DB を WAL モードで作り、平文の内線/トランクを入れる。"""
    upgrade(_cfg(db_path, None), "0023")
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        "INSERT INTO extensions (number, display_name, sip_password, enabled)"
        " VALUES ('1001', 'Alice', :p, 1)",
        {"p": _EXT_PW},
    )
    con.execute(
        "INSERT INTO trunks (name, display_name, host, username, password)"
        " VALUES ('hgw', 'HGW', '192.168.1.1', '3', :p)",
        {"p": _TRUNK_PW},
    )
    con.commit()
    con.close()


def _cols(db_path: Path, table: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _raw_bytes(db_path: Path) -> bytes:
    data = db_path.read_bytes()
    wal = db_path.with_name(db_path.name + "-wal")
    if wal.exists():
        data += wal.read_bytes()
    return data


def test_upgrade_encrypts_existing_rows(tmp_path):
    db = tmp_path / "m.db"
    _seed_0023(db)
    upgrade(_cfg(db), "0027")

    assert "sip_password" not in _cols(db, "extensions")
    assert "sip_password_encrypted" in _cols(db, "extensions")
    assert "password" not in _cols(db, "trunks")
    assert "password_encrypted" in _cols(db, "trunks")

    con = sqlite3.connect(db)
    ext_tok = con.execute("SELECT sip_password_encrypted FROM extensions").fetchone()[0]
    trunk_tok = con.execute("SELECT password_encrypted FROM trunks").fetchone()[0]
    con.close()
    box = SecretBox(_KEY)
    assert ext_tok != _EXT_PW
    assert box.decrypt(ext_tok) == _EXT_PW
    assert box.decrypt(trunk_tok) == _TRUNK_PW


def test_upgrade_leaves_no_plaintext_in_db_or_wal(tmp_path):
    db = tmp_path / "m.db"
    _seed_0023(db)
    # 前提確認: 移行前は平文がファイル上に見える（検査方法が有効であること）
    assert _EXT_PW.encode() in _raw_bytes(db)

    upgrade(_cfg(db), "0027")

    raw = _raw_bytes(db)
    assert _EXT_PW.encode() not in raw
    assert _TRUNK_PW.encode() not in raw


def test_upgrade_without_key_fails_and_keeps_schema(tmp_path):
    db = tmp_path / "m.db"
    _seed_0023(db)
    with pytest.raises(RuntimeError, match="master_key"):
        upgrade(_cfg(db, None), "0027")
    # fail-closed: 何も変わっていない（旧列のまま、0023 のまま）
    assert "sip_password" in _cols(db, "extensions")
    assert "sip_password_encrypted" not in _cols(db, "extensions")
    con = sqlite3.connect(db)
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0023"
    con.close()


def test_upgrade_completes_after_interrupted_run(tmp_path):
    """前回の実行が列追加の直後で中断していても（DDL のみ確定済み）、再実行で完走する。"""
    db = tmp_path / "m.db"
    _seed_0023(db)
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE extensions ADD COLUMN sip_password_encrypted TEXT")
    con.execute("ALTER TABLE trunks ADD COLUMN password_encrypted TEXT NOT NULL DEFAULT ''")
    con.commit()
    con.close()

    upgrade(_cfg(db), "0027")
    con = sqlite3.connect(db)
    tok = con.execute("SELECT sip_password_encrypted FROM extensions").fetchone()[0]
    con.close()
    assert SecretBox(_KEY).decrypt(tok) == _EXT_PW
    assert "sip_password" not in _cols(db, "extensions")


def test_upgrade_without_key_on_empty_db_succeeds(tmp_path):
    """暗号化すべき行が無い（新規インストール）なら鍵なしでも進める。"""
    upgrade_to_head(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    assert "sip_password_encrypted" in _cols(tmp_path / "m.db", "extensions")


def test_downgrade_restores_plaintext(tmp_path):
    db = tmp_path / "m.db"
    _seed_0023(db)
    upgrade(_cfg(db), "0027")
    downgrade(_cfg(db), "0023")

    assert "sip_password" in _cols(db, "extensions")
    assert "sip_password_encrypted" not in _cols(db, "extensions")
    con = sqlite3.connect(db)
    assert con.execute("SELECT sip_password FROM extensions").fetchone()[0] == _EXT_PW
    assert con.execute("SELECT password FROM trunks").fetchone()[0] == _TRUNK_PW
    con.close()


def test_downgrade_with_wrong_key_fails(tmp_path):
    db = tmp_path / "m.db"
    _seed_0023(db)
    upgrade(_cfg(db), "0027")
    with pytest.raises(Exception):  # noqa: B017 — InvalidToken（平文列を空で残さない）
        downgrade(_cfg(db, "w" * 48), "0023")
    assert "sip_password_encrypted" in _cols(db, "extensions")


def test_cli_secrets_file_x_argument(tmp_path):
    """alembic CLI 経路: -x secrets_file=... で secrets.json から鍵を読む。"""
    db = tmp_path / "m.db"
    _seed_0023(db)
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(json.dumps({"master_key": _KEY}), encoding="utf-8")
    cfg = _cfg(db, None)
    cfg.cmd_opts = type("Opts", (), {"x": [f"secrets_file={secrets_file}"]})()
    upgrade(cfg, "0027")
    con = sqlite3.connect(db)
    tok = con.execute("SELECT sip_password_encrypted FROM extensions").fetchone()[0]
    con.close()
    assert SecretBox(_KEY).decrypt(tok) == _EXT_PW
