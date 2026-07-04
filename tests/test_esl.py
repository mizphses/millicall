import asyncio
import contextlib

import pytest

from millicall.telephony.esl import ESLAuthError, ESLClient, ESLConnectionClosed


async def _start_fake_fs(password: str):
    """最小の偽 FreeSWITCH ESL inbound サーバ。"""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(b"Content-Type: auth/request\n\n")
            await writer.drain()
            auth = (await reader.readuntil(b"\n\n")).decode()
            if not auth.startswith(f"auth {password}"):
                writer.write(b"Content-Type: command/reply\nReply-Text: -ERR invalid\n\n")
                await writer.drain()
                return
            writer.write(b"Content-Type: command/reply\nReply-Text: +OK accepted\n\n")
            await writer.drain()

            while True:
                try:
                    line = (await reader.readuntil(b"\n\n")).decode()
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                cmd = line.strip()
                if cmd.startswith("api reloadxml"):
                    body = b"+OK [Success]\n"
                    writer.write(
                        b"Content-Type: api/response\nContent-Length: %d\n\n%s" % (len(body), body)
                    )
                    await writer.drain()
                elif cmd.startswith("event plain"):
                    writer.write(
                        b"Content-Type: command/reply\nReply-Text: +OK event listener enabled\n\n"
                    )
                    await writer.drain()
                    ev = b"Event-Name: CHANNEL_CREATE\nChannel-Call-UUID: uuid-123\n\n"
                    writer.write(
                        b"Content-Type: text/event-plain\nContent-Length: %d\n\n%s" % (len(ev), ev)
                    )
                    await writer.drain()
        finally:
            # Python 3.12.1+ wait_closed() properly waits for connection_lost();
            # connection_lost() is only triggered when the transport is closed.
            # eof_received() returns True (non-SSL default) so asyncio keeps the
            # write-end open after the client sends FIN.  We must close explicitly
            # so the server-side transport closes and wait_closed() can complete.
            if not writer.is_closing():
                writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def test_connect_and_reloadxml() -> None:
    server, port = await _start_fake_fs("s3cret")
    async with server:
        client = ESLClient("127.0.0.1", port, "s3cret")
        await client.connect()
        result = await client.reloadxml()
        assert "+OK [Success]" in result
        await client.close()


async def test_auth_failure_raises() -> None:
    server, port = await _start_fake_fs("right-pw")
    async with server:
        client = ESLClient("127.0.0.1", port, "wrong-pw")
        with pytest.raises(ESLAuthError):
            await client.connect()


async def test_event_dispatch() -> None:
    server, port = await _start_fake_fs("s3cret")
    received: list[dict[str, str]] = []
    done = asyncio.Event()

    async def on_event(event: dict[str, str]) -> None:
        received.append(event)
        done.set()

    async with server:
        client = ESLClient("127.0.0.1", port, "s3cret", on_event=on_event)
        await client.connect()
        await client.subscribe(["CHANNEL_CREATE"])
        await asyncio.wait_for(done.wait(), timeout=2.0)
        await client.close()

    assert received[0]["Event-Name"] == "CHANNEL_CREATE"
    assert received[0]["Channel-Call-UUID"] == "uuid-123"


async def test_api_raises_on_disconnect() -> None:
    """Server closes connection while api() is pending → api() raises ESLConnectionClosed."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(b"Content-Type: auth/request\n\n")
            await writer.drain()
            await reader.readuntil(b"\n\n")  # consume auth
            writer.write(b"Content-Type: command/reply\nReply-Text: +OK accepted\n\n")
            await writer.drain()
            await reader.readuntil(b"\n\n")  # consume api command — then drop connection
        finally:
            if not writer.is_closing():
                writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        client = ESLClient("127.0.0.1", port, "s3cret")
        await client.connect()
        with pytest.raises(ESLConnectionClosed):
            await asyncio.wait_for(client.api("status"), timeout=2.0)
        await client.close()


async def test_on_event_exception_does_not_kill_reader() -> None:
    """on_event callback that raises → reader loop survives and delivers the second event."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(b"Content-Type: auth/request\n\n")
            await writer.drain()
            await reader.readuntil(b"\n\n")  # consume auth
            writer.write(b"Content-Type: command/reply\nReply-Text: +OK accepted\n\n")
            await writer.drain()
            await reader.readuntil(b"\n\n")  # consume subscribe
            writer.write(b"Content-Type: command/reply\nReply-Text: +OK event listener enabled\n\n")
            await writer.drain()
            for name in (b"CHANNEL_CREATE", b"CHANNEL_DESTROY"):
                ev = b"Event-Name: " + name + b"\n\n"
                writer.write(
                    b"Content-Type: text/event-plain\nContent-Length: %d\n\n%s" % (len(ev), ev)
                )
                await writer.drain()
            # Stay open until the client disconnects
            with contextlib.suppress(asyncio.IncompleteReadError, ConnectionError):
                await reader.read(1024)
        finally:
            if not writer.is_closing():
                writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    second_event_received = asyncio.Event()
    call_count = 0

    async def on_event(event: dict[str, str]) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("deliberate on_event failure")
        second_event_received.set()

    async with server:
        client = ESLClient("127.0.0.1", port, "s3cret", on_event=on_event)
        await client.connect()
        await client.subscribe(["CHANNEL_CREATE", "CHANNEL_DESTROY"])
        await asyncio.wait_for(second_event_received.wait(), timeout=2.0)
        await client.close()

    assert second_event_received.is_set()


async def test_double_close_no_exception() -> None:
    """close() called twice must not raise."""
    server, port = await _start_fake_fs("s3cret")
    async with server:
        client = ESLClient("127.0.0.1", port, "s3cret")
        await client.connect()
        await client.close()
        await client.close()  # must not raise
