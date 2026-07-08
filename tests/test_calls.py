import asyncio
import socket

import pytest_asyncio
from httpx import ASGITransport

from millicall.auth.security import hash_password
from millicall.config import Settings
from millicall.main import create_app
from millicall.models import User
from tests.conftest import CsrfAwareClient


async def _fake_fs_capturing_originate():
    received: list[str] = []

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
                cmd = line.strip()
                received.append(cmd)
                if cmd.startswith("bgapi"):
                    writer.write(
                        b"Content-Type: command/reply\n"
                        b"Reply-Text: +OK Job-UUID: job-x\nJob-UUID: job-x\n\n"
                    )
                    await writer.drain()
                elif cmd.startswith("event plain"):
                    writer.write(b"Content-Type: command/reply\nReply-Text: +OK\n\n")
                    await writer.drain()
        finally:
            if not writer.is_closing():
                writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], received


@pytest_asyncio.fixture
async def call_env(tmp_path):
    server, port, received = await _fake_fs_capturing_originate()
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_host="127.0.0.1",
        esl_port=port,
        esl_timeout_seconds=2.0,
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        sm = application.state.sessionmaker
        async with sm() as s:
            s.add(
                User(
                    username="caller",
                    hashed_password=hash_password("Passw0rd1"),
                    display_name="caller",
                    role="admin",
                    origin="local",
                )
            )
            await s.commit()
        transport = ASGITransport(app=application)
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            await c.post("/api/auth/login", json={"username": "caller", "password": "Passw0rd1"})
            await c.post("/api/extensions", json={"number": "1001", "display_name": "A"})
            yield c, received
    server.close()
    await server.wait_closed()


async def test_originate_returns_uuid_and_sends_command(call_env):
    c, received = call_env
    resp = await c.post("/api/calls", json={"from_extension": "1001", "to": "0312345678"})
    assert resp.status_code == 201, resp.text
    call_uuid = resp.json()["call_uuid"]
    assert call_uuid
    origs = [cmd for cmd in received if cmd.startswith("bgapi originate")]
    assert origs, f"originate must be sent; got {received}"
    assert f"origination_uuid={call_uuid}" in origs[0]
    assert "user/1001@" in origs[0]
    assert origs[0].endswith("0312345678 XML default")


async def test_originate_unknown_extension_400(call_env):
    c, _ = call_env
    resp = await c.post("/api/calls", json={"from_extension": "9999", "to": "0312345678"})
    assert resp.status_code == 400


async def test_calls_require_auth(client):
    assert (
        await client.post("/api/calls", json={"from_extension": "1001", "to": "1002"})
    ).status_code == 401


async def test_originate_rejects_injection_in_to(call_env):
    c, _ = call_env
    # Test injection attempt with braces
    resp = await c.post(
        "/api/calls", json={"from_extension": "1001", "to": "0312345678} {origination_uuid=evil"}
    )
    assert resp.status_code == 422, resp.text

    # Test injection attempt with spaces (should be rejected by pattern)
    resp = await c.post("/api/calls", json={"from_extension": "1001", "to": "031 2345678"})
    assert resp.status_code == 422, resp.text


async def test_originate_esl_down_returns_503(tmp_path):
    # Get an unreachable port by binding and releasing a socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead_port = sock.getsockname()[1]

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_host="127.0.0.1",
        esl_port=dead_port,
        esl_timeout_seconds=0.5,
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        sm = application.state.sessionmaker
        async with sm() as s:
            s.add(
                User(
                    username="caller",
                    hashed_password=hash_password("Passw0rd1"),
                    display_name="caller",
                    role="admin",
                    origin="local",
                )
            )
            await s.commit()
        transport = ASGITransport(app=application)
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            await c.post("/api/auth/login", json={"username": "caller", "password": "Passw0rd1"})
            await c.post("/api/extensions", json={"number": "1001", "display_name": "A"})
            resp = await c.post("/api/calls", json={"from_extension": "1001", "to": "0312345678"})
            assert resp.status_code == 503, resp.text


async def test_originate_disabled_extension_400(call_env):
    c, _ = call_env
    # Get the extension id from the endpoint (by fetching the first extension)
    ext_list = await c.get("/api/extensions")
    assert ext_list.status_code == 200
    ext_id = ext_list.json()[0]["id"]

    # Disable the extension via PATCH
    resp = await c.patch(f"/api/extensions/{ext_id}", json={"enabled": False})
    assert resp.status_code == 200, resp.text

    # Try to originate with disabled extension
    resp = await c.post("/api/calls", json={"from_extension": "1001", "to": "0312345678"})
    assert resp.status_code == 400, resp.text
