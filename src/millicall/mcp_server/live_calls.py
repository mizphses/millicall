"""LiveCallView: SessionRegistry + cdr テーブルから通話状態を組み立てる。

コントローラ裁定 #3:
  - show channels パースは行わない。
  - 対象は millicall 管理チャネル（SessionRegistry 登録中）のみ。
  - CDR は CHANNEL_HANGUP_COMPLETE 時点で書かれるため、進行中通話の CDR は存在しない。
    取れない値は null を返す（契約 §9/§10 準拠）。

返り値のキー:
  - §9 get_call_status: channel_id, state, caller_name, caller_number,
                        connected_name, connected_number, created_at
  - §10 list_active_calls: channel_id, state, caller_number,
                           connected_number, created_at
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from millicall.models import Cdr

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from millicall.media.service import SessionRegistry


class LiveCallView:
    """SessionRegistry + CDR テーブルから MCP ツール用通話状態 dict を組み立てる。

    get_status(uuid) -> dict | None
        uuid が SessionRegistry に存在すれば §9 キー形の dict を返す。
        存在しない場合は None（ツール層が「チャネルが見つかりません」に変換する）。

    list_active() -> list[dict]
        SessionRegistry に登録中のすべてのセッションを §10 の calls 要素形で返す。
    """

    def __init__(
        self,
        session_registry: SessionRegistry,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._registry = session_registry
        self._sessionmaker = sessionmaker

    async def _fetch_cdr(self, uuid: str) -> Cdr | None:
        """CDR テーブルから call_uuid に一致するレコードを 1 件取得する。

        進行中の通話は CDR がまだ書かれていないため None を返すことが多い。
        """
        async with self._sessionmaker() as db:
            result = await db.execute(select(Cdr).where(Cdr.call_uuid == uuid))
            return result.scalar_one_or_none()

    @staticmethod
    def _build_status(uuid: str, cdr: Cdr | None) -> dict:
        """§9 get_call_status の返り値 dict を組み立てる。

        CDR が存在しない（=進行中通話）場合は取得不能なフィールドを None にする。
        """
        return {
            "channel_id": uuid,
            "state": "Up",
            "caller_name": cdr.caller_id_name if cdr else None,
            "caller_number": cdr.src_number if cdr else None,
            "connected_name": None,  # show channels パース不使用のため常に null
            "connected_number": cdr.dst_number if cdr else None,
            "created_at": (cdr.started_at.isoformat() if cdr and cdr.started_at else None),
        }

    @staticmethod
    def _build_active_entry(uuid: str, cdr: Cdr | None) -> dict:
        """§10 list_active_calls の calls 要素 dict を組み立てる。"""
        return {
            "channel_id": uuid,
            "state": "Up",
            "caller_number": cdr.src_number if cdr else None,
            "connected_number": cdr.dst_number if cdr else None,
            "created_at": (cdr.started_at.isoformat() if cdr and cdr.started_at else None),
        }

    async def get_status(self, uuid: str) -> dict | None:
        """指定 uuid のライブ通話状態を返す。

        SessionRegistry に登録されていない uuid は None を返す。
        ツール層は None を受け取ったら
        ``{"error": "チャネルが見つかりません（通話が終了している可能性があります）"}``
        に変換する。
        """
        if self._registry.get(uuid) is None:
            return None
        cdr = await self._fetch_cdr(uuid)
        return self._build_status(uuid, cdr)

    async def list_active(self) -> list[dict]:
        """SessionRegistry 登録中のすべての通話を §10 calls 要素形で返す。

        各エントリの CDR フィールドは進行中通話では None になる。
        """
        uuids = self._registry.all_uuids()
        result: list[dict] = []
        for uuid in uuids:
            cdr = await self._fetch_cdr(uuid)
            result.append(self._build_active_entry(uuid, cdr))
        return result
