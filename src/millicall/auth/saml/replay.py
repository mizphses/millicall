"""SAML の一回限り消費（アサーション ID / AuthnRequest ID のリプレイ防御）。

消費済み ID は DB（saml_consumed_ids）に保存する。再起動後も、同じ DB を共有する
複数プロセス間でも同じ ID は一度しか受理しない（issue #86）。

原子性: 期限切れ行の DELETE で書き込みロックを取り、続けて PRIMARY KEY への
`INSERT ... ON CONFLICT DO NOTHING` の影響行数で判定する。SQLite の書き込みは
直列化されるため、同時に同じ ID を出した 2 プロセスのうち INSERT できるのは 1 つだけ。
ID は SHA-256 で固定長化して保存する（IdP 由来の任意長文字列をキーにしない）。
"""

import hashlib
import time
from collections.abc import Sequence

from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from millicall.models import SamlConsumedId


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def consume_once(sessionmaker, entries: Sequence[tuple[str, float]]) -> int | None:
    """entries の (key, ttl 秒) をすべて消費済みにする。

    すべて未消費なら記録して None を返す。いずれかが消費済み（有効期限内）なら何も記録せず、
    最初に重複したエントリの index を返す（呼び出し側が理由コードを選ぶ）。
    """
    now = time.time()
    async with sessionmaker() as session:
        await session.execute(delete(SamlConsumedId).where(SamlConsumedId.expires_at <= now))
        for index, (key, ttl) in enumerate(entries):
            result = await session.execute(
                sqlite_insert(SamlConsumedId)
                .values(key_hash=_key_hash(key), expires_at=now + ttl)
                .on_conflict_do_nothing(index_elements=["key_hash"])
            )
            if result.rowcount != 1:
                await session.rollback()
                return index
        await session.commit()
    return None
