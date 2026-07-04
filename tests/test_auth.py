from millicall.auth.security import (
    hash_password,
    issue_session,
    read_session,
    verify_password,
)
from millicall.auth.service import ensure_admin_user


def test_password_hash_roundtrip() -> None:
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"
    assert verify_password(h, "s3cret-pw") is True
    assert verify_password(h, "wrong") is False


def test_session_token_roundtrip() -> None:
    token = issue_session("k" * 40, 42)
    assert read_session("k" * 40, token, 3600) == 42
    assert read_session("other-secret", token, 3600) is None


def test_session_token_expired() -> None:
    token = issue_session("k" * 40, 7)
    assert read_session("k" * 40, token, -1) is None


async def test_ensure_admin_creates_once(app) -> None:
    # lifespan で既に admin が作成済み → 2回目は None
    sm = app.state.sessionmaker
    async with sm() as s:
        assert await ensure_admin_user(s) is None


async def test_me_requires_auth(client) -> None:
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_login_and_me(client, user_factory) -> None:
    username, password = await user_factory(username="alice", password="Wonderland1")
    login = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200
    # HttpOnly cookie が設定される
    assert "millicall_session" in login.cookies

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


async def test_login_wrong_password(client, user_factory) -> None:
    await user_factory(username="bob", password="correct-horse")
    resp = await client.post(
        "/api/auth/login", json={"username": "bob", "password": "nope"}
    )
    assert resp.status_code == 401


async def test_logout_clears_cookie(client, user_factory) -> None:
    username, password = await user_factory()
    await client.post("/api/auth/login", json={"username": username, "password": password})
    out = await client.post("/api/auth/logout")
    assert out.status_code == 200
    me = await client.get("/api/auth/me")
    assert me.status_code == 401


async def test_healthz(client) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
