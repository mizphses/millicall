import logging
import sqlite3
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_SCRIPT_LOCATION = _REPO_ROOT / "alembic"

logger = logging.getLogger("millicall.migrations")


# alembic/env.py・0027 と共有する Config.attributes のキー。
MASTER_KEY_ATTR = "millicall_master_key"
# マイグレーションが立てるフラグ。env.py がトランザクション終了後に VACUUM する。
VACUUM_FLAG_ATTR = "millicall_vacuum_after_migration"

# 圧縮（VACUUM + WAL 切り詰め）の再試行設定。読み取り中の接続があると
# wal_checkpoint(TRUNCATE) は busy=1 を返して WAL を残すため、短い間隔で数回試す。
_COMPACT_ATTEMPTS = 5
_COMPACT_RETRY_INTERVAL = 0.2
_COMPACT_BUSY_TIMEOUT = 1.0


def sqlite_path(database_url: str) -> Path | None:
    """SQLite のファイルパスを返す。SQLite 以外・インメモリは None。"""
    url = make_url(database_url)
    if not url.get_backend_name().startswith("sqlite"):
        return None
    if not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def compaction_marker(db_path: Path) -> Path:
    """「圧縮未完了」マーカー。旧平文の残骸が消えるまで data/ に残し、起動ごとに再試行する。"""
    return db_path.with_name(db_path.name + ".compact-pending")


def _compact_once(db_path: Path) -> None:
    con = sqlite3.connect(db_path, timeout=_COMPACT_BUSY_TIMEOUT, isolation_level=None)
    try:
        con.execute("VACUUM")
        busy, _log, _checkpointed = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise sqlite3.OperationalError("wal_checkpoint(TRUNCATE) busy")
    finally:
        con.close()


def compact_sqlite(db_path: Path) -> bool:
    """VACUUM と wal_checkpoint(TRUNCATE) で空きページ・WAL の旧データを消す。

    VACUUM でファイルを作り直し、checkpoint で WAL を本体へ書き戻して WAL を 0 バイトに
    する。他の接続が読み取り中だと checkpoint は busy を返すため（返り値の 1 列目）、
    数回再試行する。成功したらマーカーを消して True、だめなら ERROR ログを出し
    マーカーを残して False（次回起動で再試行）。
    """
    marker = compaction_marker(db_path)
    marker.touch(mode=0o600, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(_COMPACT_ATTEMPTS):
        try:
            _compact_once(db_path)
        except sqlite3.Error as exc:
            last_error = exc
            if attempt + 1 < _COMPACT_ATTEMPTS:
                time.sleep(_COMPACT_RETRY_INTERVAL)
            continue
        marker.unlink(missing_ok=True)
        return True
    logger.error(
        "DB の圧縮（VACUUM / wal_checkpoint）を完了できませんでした: %s — "
        "旧平文列の残骸が DB ファイルまたは WAL に残っている可能性があります。"
        "他に DB を開いているプロセスが無い状態で core を再起動すると再試行します（%s）",
        last_error,
        marker.name,
    )
    return False


def _config(database_url: str, master_key: str | None = None) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    cfg.set_main_option("sqlalchemy.url", database_url)
    if master_key:
        # 鍵は ini/環境変数ではなくプロセス内の attributes だけで渡す（ログ・ファイルに残さない）。
        cfg.attributes[MASTER_KEY_ATTR] = master_key
    return cfg


def upgrade_to_head(database_url: str, master_key: str | None = None) -> None:
    """DB を head へ上げる。

    master_key は SIP 認証情報の暗号化マイグレーション（0027）で使う。暗号化すべき行が
    あるのに鍵が無い場合、マイグレーションは失敗する（fail-closed）。
    以前の起動で圧縮が完了していなければ（マーカーが残っていれば）ここで再試行する。
    """
    command.upgrade(_config(database_url, master_key), "head")
    db_path = sqlite_path(database_url)
    if db_path is not None and compaction_marker(db_path).exists():
        compact_sqlite(db_path)
