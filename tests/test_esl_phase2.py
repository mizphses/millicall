import asyncio

from millicall.telephony.esl import ESLClient


async def _fake_fs_with_bgapi(password="s3cret"):
    async def handle(reader, writer):
        try:
            writer.write(b"Content-Type: auth/request\n\n")
            await writer.drain()
            await reader.readuntil(b"\n\n")
            writer.write(b"Content-Type: command/reply\nReply-Text: +OK accepted\n\n")
            await writer.drain()
            while True:
                try:
                    line = (await reader.readuntil(b"\n\n")).decode()
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                if line.strip().startswith("bgapi"):
                    writer.write(
                        b"Content-Type: command/reply\n"
                        b"Reply-Text: +OK Job-UUID: job-123\n"
                        b"Job-UUID: job-123\n\n"
                    )
                    await writer.drain()
        finally:
            if not writer.is_closing():
                writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_bgapi_returns_job_uuid():
    server, port = await _fake_fs_with_bgapi()
    async with server:
        client = ESLClient("127.0.0.1", port, "s3cret")
        await client.connect()
        job = await client.bgapi("originate user/1001 1002 XML default")
        assert job == "job-123"
        await client.close()


async def test_wait_closed_returns_when_server_disconnects():
    async def handle(reader, writer):
        writer.write(b"Content-Type: auth/request\n\n")
        await writer.drain()
        await reader.readuntil(b"\n\n")
        writer.write(b"Content-Type: command/reply\nReply-Text: +OK accepted\n\n")
        await writer.drain()
        # すぐ切断
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        client = ESLClient("127.0.0.1", port, "s3cret")
        await client.connect()
        # サーバ切断で reader タスクが終了 → wait_closed が返る
        await asyncio.wait_for(client.wait_closed(), timeout=2.0)
        await client.close()
