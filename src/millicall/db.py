from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _apply_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    # 書き込みロック待ちの上限（ms）。pysqlite の既定値（timeout=5.0）に頼らず明示する。
    # OAuth コード交換・SAML ID 消費などの一回限り操作は、並行時にこの範囲で待ってから
    # 負けた側が正しく拒否される（issue #86）。
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def create_db_engine(database_url: str) -> AsyncEngine:
    engine = create_async_engine(database_url, echo=False, future=True)
    event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
    return engine
