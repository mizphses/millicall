from millicall.auth.security import (
    hash_password,
    issue_session,
    read_session,
    verify_password,
)
from millicall.auth.service import ensure_admin_user
from millicall.models import User


def test_password_hash_roundtrip() -> None:
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"
    assert verify_password(h, "s3cret-pw") is True
    assert verify_password(h, "wrong") is False


def test_session_token_roundtrip() -> None:
    token = issue_session("k" * 40, 42, epoch=0)
    data = read_session("k" * 40, token, 3600)
    assert data is not None
    assert data.uid == 42
    assert data.epoch == 0
    assert read_session("other-secret", token, 3600) is None


def test_session_token_expired() -> None:
    token = issue_session("k" * 40, 7, epoch=0)
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
    login = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200
    # HttpOnly cookie が設定される
    assert "millicall_session" in login.cookies

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


async def test_login_wrong_password(client, user_factory) -> None:
    await user_factory(username="bob", password="correct-horse")
    resp = await client.post("/api/auth/login", json={"username": "bob", "password": "nope"})
    assert resp.status_code == 401


async def test_logout_clears_cookie(client, user_factory) -> None:
    username, password = await user_factory()
    await client.post("/api/auth/login", json={"username": username, "password": password})
    out = await client.post("/api/auth/logout")
    assert out.status_code == 200
    me = await client.get("/api/auth/me")
    assert me.status_code == 401


async def test_login_nonexistent_user(client) -> None:
    resp = await client.post(
        "/api/auth/login", json={"username": "ghost", "password": "irrelevant"}
    )
    assert resp.status_code == 401


async def test_healthz(client) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# M1: User.role デフォルト値テスト
def test_user_role_default_is_user() -> None:
    """M1: User.role カラムの INSERT default が "user" であることを確認。

    SQLAlchemy の mapped_column の default は ORM flush 時に適用されるカラムレベルの
    デフォルトであり、コンストラクタ呼び出し時には適用されない。
    そのためテーブルカラムの ColumnDefault を直接確認する。
    server_default も同様に "user" であることを確認する。
    """
    col = User.__table__.c["role"]
    # Python-side (ORM) default
    assert col.default is not None, "role column has no default"
    assert col.default.arg == "user", f"expected default 'user', got {col.default.arg!r}"
    # DB server_default（既存 DB への raw INSERT も "user" になる）
    assert col.server_default is not None, "role column has no server_default"
    assert col.server_default.arg == "user", (
        f"expected server_default 'user', got {col.server_default.arg!r}"
    )


# M6: セキュリティヘッダーテスト
async def test_security_headers_present(client) -> None:
    """M6: 全レスポンスにセキュリティヘッダーが付与されることを確認。

    /healthz エンドポイントを使用（認証不要・副作用なし）。
    """
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert "content-security-policy" in resp.headers, "CSP header missing"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    # HSTS は設定しない（core は HTTP で動作; TLS は front の責務）
    assert "strict-transport-security" not in resp.headers
