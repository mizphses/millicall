import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from millicall.models import Cdr
from millicall.telephony.esl import ESLClient, ESLError

logger = logging.getLogger("millicall.telephony.events")

EventHandler = Callable[[dict[str, str]], Awaitable[None]]
MakeClient = Callable[[EventHandler], ESLClient]


def _epoch_us_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        micros = int(value)
    except ValueError:
        return None
    if micros <= 0:
        return None
    return datetime.fromtimestamp(micros / 1_000_000, tz=timezone.utc).replace(tzinfo=None)  # noqa: UP017


def _int(value: str | None) -> int:
    try:
        return int(value) if value else 0
    except ValueError:
        return 0


def event_to_cdr(event: dict[str, str]) -> Cdr | None:
    if event.get("Event-Name") != "CHANNEL_HANGUP_COMPLETE":
        return None
    call_uuid = event.get("Channel-Call-UUID") or event.get("Unique-ID") or ""
    if not call_uuid:
        return None
    return Cdr(
        call_uuid=call_uuid,
        direction=event.get("Call-Direction", ""),
        src_number=event.get("Caller-Caller-ID-Number", ""),
        dst_number=event.get("Caller-Destination-Number", ""),
        caller_id_name=event.get("Caller-Caller-ID-Name", ""),
        started_at=_epoch_us_to_dt(event.get("Caller-Channel-Created-Time")),
        answered_at=_epoch_us_to_dt(event.get("Caller-Channel-Answered-Time")),
        ended_at=_epoch_us_to_dt(event.get("Caller-Channel-Hangup-Time")),
        duration_seconds=_int(event.get("variable_duration")),
        billsec_seconds=_int(event.get("variable_billsec")),
        hangup_cause=event.get("Hangup-Cause", ""),
    )


class CdrRecorder:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def handle(self, event: dict[str, str]) -> None:
        cdr = event_to_cdr(event)
        if cdr is None:
            return
        async with self._sessionmaker() as session:
            session.add(cdr)
            try:
                await session.commit()
            except IntegrityError:
                # 同一 call_uuid（両レグ / 再送）は最初の1件だけ残す
                await session.rollback()


class EslEventListener:
    """常駐 ESL イベントリスナー。切断時は指数バックオフで再接続する。"""

    def __init__(
        self,
        make_client: MakeClient,
        events: list[str],
        handler: EventHandler,
        *,
        min_backoff: float = 1.0,
        max_backoff: float = 30.0,
    ) -> None:
        self._make_client = make_client
        self._events = events
        self._handler = handler
        self._min_backoff = min_backoff
        self._max_backoff = max_backoff
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._client: ESLClient | None = None

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._client is not None:
            await self._client.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        backoff = self._min_backoff
        while not self._stop.is_set():
            client = self._make_client(self._handler)
            self._client = client
            try:
                await client.connect()
                await client.subscribe(self._events)
                backoff = self._min_backoff  # 接続成功でリセット
                await client.wait_closed()
            except (OSError, ESLError) as exc:
                logger.warning("ESL event listener connection failed: %s", exc)
            finally:
                await client.close()
                self._client = None
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except TimeoutError:
                # 本当のタイムアウト時のみバックオフ倍にする
                backoff = min(backoff * 2, self._max_backoff)
            # stop が発火した場合はループの先頭で break するので、ここには来ない
