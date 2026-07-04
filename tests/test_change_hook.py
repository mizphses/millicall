"""Change-hook integration tests: FS config regeneration and ESL reloadxml."""

import asyncio
import socket
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

from millicall.auth.security import hash_password
from millicall.config import Settings
from millicall.main import create_app
from millicall.models import User

# ---------------------------------------------------------------------------
# Wire-server helpers
# ---------------------------------------------------------------------------


async def _start_accepting_fake_fs() -> tuple[asyncio.AbstractServer, int, list[str]]:
    """Fake FS that accepts any auth unconditionally and records wire commands."""
    received: list[str] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(b"Content-Type: auth/request\n\n")
            await writer.drain()
            await reader.readuntil(b"\n\n")  # accept any password unconditionally
            writer.write(b"Content-Type: command/reply\nReply-Text: +OK accepted\n\n")
            await writer.drain()
            while True:
                try:
                    line = (await reader.readuntil(b"\n\n")).decode()
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                cmd = line.strip()
                received.append(cmd)
                if cmd.startswith("api reloadxml"):
                    body = b"+OK [Success]\n"
                    writer.write(
                        b"Content-Type: api/response\nContent-Length: %d\n\n%s"
                        % (len(body), body)
                    )
                    await writer.drain()
        finally:
            if not writer.is_closing():
                writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port, received


# ---------------------------------------------------------------------------
# Admin client context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_admin_client(application):
    """Context manager: persist an extra admin user, yield an authenticated AsyncClient.

    Uses username "hooktest" (distinct from "admin") because lifespan's
    ensure_admin_user already inserts the "admin" row on startup.
    """
    sm = application.state.sessionmaker
    async with sm() as session:
        session.add(
            User(
                username="hooktest",
                hashed_password=hash_password("Passw0rd1"),
                display_name="hooktest",
                role="admin",
                origin="local",
            )
        )
        await session.commit()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/auth/login", json={"username": "hooktest", "password": "Passw0rd1"})
        yield c


# ---------------------------------------------------------------------------
# Existing tests
# ---------------------------------------------------------------------------


async def test_create_extension_writes_fs_config(auth_client_with_telephony, app) -> None:
    resp = await auth_client_with_telephony.post(
        "/api/extensions", json={"number": "1001", "display_name": "Alice"}
    )
    assert resp.status_code == 201
    fs_dir = app.state.settings.fs_config_dir
    assert (fs_dir / "directory" / "default" / "1001.xml").exists()
    assert (fs_dir / "sip_profiles" / "internal.xml").exists()


async def test_delete_extension_removes_user_file(auth_client_with_telephony, app) -> None:
    created = await auth_client_with_telephony.post(
        "/api/extensions", json={"number": "1002", "display_name": "Bob"}
    )
    ext_id = created.json()["id"]
    fs_dir = app.state.settings.fs_config_dir
    assert (fs_dir / "directory" / "default" / "1002.xml").exists()
    await auth_client_with_telephony.delete(f"/api/extensions/{ext_id}")
    assert not (fs_dir / "directory" / "default" / "1002.xml").exists()


async def test_initial_config_written_on_startup(app) -> None:
    # lifespan 起動時に内線ゼロでも静的設定が生成される
    fs_dir = app.state.settings.fs_config_dir
    assert (fs_dir / "autoload_configs" / "event_socket.conf.xml").exists()


# ---------------------------------------------------------------------------
# Finding 2: wire-level assert that reloadxml is actually sent to FreeSWITCH
# ---------------------------------------------------------------------------


async def test_reloadxml_wire_on_extension_create(tmp_path) -> None:
    """Wire assert: reloadxml must be sent over TCP to FreeSWITCH when an extension is created."""
    server, port, received = await _start_accepting_fake_fs()

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
    try:
        async with (
            application.router.lifespan_context(application),
            _make_admin_client(application) as c,
        ):
            resp = await c.post(
                "/api/extensions", json={"number": "2001", "display_name": "WireTest"}
            )
            assert resp.status_code == 201
    finally:
        server.close()
        await server.wait_closed()

    assert any("reloadxml" in cmd for cmd in received), (
        f"reloadxml must be sent to FreeSWITCH on extension create; commands seen: {received}"
    )


# ---------------------------------------------------------------------------
# Finding 3: disabled extension is excluded from the generated FS config
# ---------------------------------------------------------------------------


async def test_disabled_extension_excluded_from_config(auth_client_with_telephony, app) -> None:
    """After disabling an extension, its user XML must not appear in the FS directory."""
    fs_dir = app.state.settings.fs_config_dir

    r1 = await auth_client_with_telephony.post(
        "/api/extensions", json={"number": "1101", "display_name": "Alice"}
    )
    r2 = await auth_client_with_telephony.post(
        "/api/extensions", json={"number": "1102", "display_name": "Bob"}
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    ext2_id = r2.json()["id"]

    # Both XMLs must exist right after creation
    assert (fs_dir / "directory" / "default" / "1101.xml").exists()
    assert (fs_dir / "directory" / "default" / "1102.xml").exists()

    patch = await auth_client_with_telephony.patch(
        f"/api/extensions/{ext2_id}", json={"enabled": False}
    )
    assert patch.status_code == 200

    # notify() is called inside PATCH handler; by the time we get the response the
    # config has already been regenerated — only the enabled extension XML should remain.
    assert (fs_dir / "directory" / "default" / "1101.xml").exists()
    assert not (fs_dir / "directory" / "default" / "1102.xml").exists()


# ---------------------------------------------------------------------------
# Finding 4: ESL unreachable must not cause the API request to fail
# ---------------------------------------------------------------------------


async def test_esl_unreachable_does_not_fail_create(tmp_path) -> None:
    # Invariant: the API must succeed (HTTP 201) even when FreeSWITCH is completely unreachable.
    # We bind a socket to get a free port, then release it so nothing listens on that port.
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
        esl_timeout_seconds=0.5,  # fast fail so the test doesn't stall
    )
    application = create_app(settings)
    async with (
        application.router.lifespan_context(application),
        _make_admin_client(application) as c,
    ):
        resp = await c.post(
            "/api/extensions", json={"number": "9001", "display_name": "ESL Dead"}
        )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Finding 1 (timeout path): hanging ESL must not stall the request indefinitely
# ---------------------------------------------------------------------------


async def test_esl_timeout_does_not_fail_create(tmp_path, monkeypatch) -> None:
    # Invariant: asyncio.TimeoutError from a hanging ESL must be swallowed — API returns 201.
    # Monkeypatching connect() avoids a real TCP server and keeps the test fast.
    import millicall.telephony.esl as esl_module

    async def _hanging_connect(self) -> None:  # noqa: ARG001
        await asyncio.sleep(9999)

    monkeypatch.setattr(esl_module.ESLClient, "connect", _hanging_connect)

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=0.1,  # tiny timeout so the test completes quickly
    )
    application = create_app(settings)
    async with (
        application.router.lifespan_context(application),
        _make_admin_client(application) as c,
    ):
        resp = await c.post(
            "/api/extensions", json={"number": "9002", "display_name": "ESL Hang"}
        )
        assert resp.status_code == 201
