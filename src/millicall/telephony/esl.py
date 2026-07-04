import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import unquote_plus

logger = logging.getLogger("millicall.telephony.esl")

# Sentinel placed into _replies when the connection closes unexpectedly.
# Any pending _send_command waiter detects it and raises ESLConnectionClosed.
_CLOSED_SENTINEL = object()


class ESLError(Exception):
    pass


class ESLAuthError(ESLError):
    pass


class ESLConnectionClosed(ESLError):  # noqa: N818
    pass


EventHandler = Callable[[dict[str, str]], Awaitable[None]]


class ESLClient:
    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        on_event: EventHandler | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.on_event = on_event
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        # Queue holds (headers, body) tuples OR _CLOSED_SENTINEL on disconnect.
        self._replies: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self._dead = False  # set True when connection dies unexpectedly

    async def _read_frame(self, reader: asyncio.StreamReader) -> tuple[dict[str, str], str]:
        headers: dict[str, str] = {}
        while True:
            raw = await reader.readline()
            if raw == b"":
                raise ESLConnectionClosed("connection closed while reading headers")
            line = raw.decode().rstrip("\r\n")
            if line == "":
                break
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = value.strip()
        body = ""
        length = headers.get("Content-Length")
        if length:
            try:
                n = int(length)
            except ValueError as exc:
                raise ESLError("malformed Content-Length header") from exc
            body = (await reader.readexactly(n)).decode()
        return headers, body

    @staticmethod
    def _parse_event(body: str) -> dict[str, str]:
        event: dict[str, str] = {}
        for line in body.split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                event[key.strip()] = unquote_plus(value.strip())
        return event

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        headers, _ = await self._read_frame(self._reader)
        if headers.get("Content-Type") != "auth/request":
            raise ESLError("expected auth/request")
        self._writer.write(f"auth {self.password}\n\n".encode())
        await self._writer.drain()
        reply, _ = await self._read_frame(self._reader)
        if not reply.get("Reply-Text", "").startswith("+OK"):
            raise ESLAuthError(reply.get("Reply-Text", "auth failed"))
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _reader_loop(self) -> None:
        assert self._reader is not None
        try:
            while not self._closed:
                headers, body = await self._read_frame(self._reader)
                ctype = headers.get("Content-Type", "")
                if ctype == "text/event-plain":
                    event = self._parse_event(body)
                    logger.info(
                        "ESL event %s uuid=%s",
                        event.get("Event-Name"),
                        event.get("Channel-Call-UUID"),
                    )
                    if self.on_event is not None:
                        try:
                            await self.on_event(event)
                        except Exception:
                            logger.exception("on_event callback raised; continuing")
                elif ctype in ("api/response", "command/reply"):
                    await self._replies.put((headers, body))
        except (ESLConnectionClosed, asyncio.IncompleteReadError):
            if not self._closed:
                logger.warning("ESL connection closed unexpectedly")
                self._dead = True
                await self._replies.put(_CLOSED_SENTINEL)
        except asyncio.CancelledError:
            raise

    async def _send_command(self, command: str) -> tuple[dict[str, str], str]:
        if self._writer is None:
            raise ESLError("not connected")
        if self._dead:
            raise ESLConnectionClosed("connection is closed")
        self._writer.write(f"{command}\n\n".encode())
        await self._writer.drain()
        reply = await self._replies.get()
        if reply is _CLOSED_SENTINEL:
            # Re-enqueue so any other concurrent waiters are also unblocked.
            await self._replies.put(_CLOSED_SENTINEL)
            raise ESLConnectionClosed("connection closed while waiting for reply")
        return reply  # type: ignore[return-value]

    async def api(self, command: str) -> str:
        _, body = await self._send_command(f"api {command}")
        return body.strip()

    async def reloadxml(self) -> str:
        return await self.api("reloadxml")

    async def subscribe(self, events: list[str]) -> None:
        await self._send_command("event plain " + " ".join(events))

    async def close(self) -> None:
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await self._reader_task
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await self._writer.wait_closed()
            self._writer = None
