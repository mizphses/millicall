"""セッションepoch失効のテスト。"""

from millicall.auth.security import issue_session, read_session


def test_issue_read_roundtrip():
    """issue/readのラウンドトリップでuid/epochが正しく返る。"""
    token = issue_session("k" * 40, 42, epoch=3)
    data = read_session("k" * 40, token, 3600)
    assert data is not None
    assert data.uid == 42
    assert data.epoch == 3


def test_legacy_token_treated_as_epoch_zero():
    """epフィールドのないレガシートークンはepoch=0として扱う。"""
    from itsdangerous import URLSafeTimedSerializer

    s = URLSafeTimedSerializer("k" * 40, salt="millicall.session.v1")
    legacy_token = s.dumps({"uid": 5})  # ep なし
    data = read_session("k" * 40, legacy_token, 3600)
    assert data is not None
    assert data.uid == 5
    assert data.epoch == 0


def test_wrong_secret_returns_none():
    token = issue_session("k" * 40, 1, epoch=0)
    assert read_session("other-secret", token, 3600) is None


def test_expired_token_returns_none():
    token = issue_session("k" * 40, 1, epoch=0)
    assert read_session("k" * 40, token, max_age=-1) is None


async def test_mismatched_epoch_returns_401(client, app, user_factory):
    """epochが不一致のトークンは401を返す。"""
    username, password = await user_factory()
    await client.post("/api/auth/login", json={"username": username, "password": password})
    # bump epoch directly in DB
    from sqlalchemy import select

    from millicall.auth.security import bump_session_epoch
    from millicall.models import User

    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        bump_session_epoch(user)
        await s.commit()
    # existing cookie still has old epoch → should 401
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_disabled_user_returns_401(client, app, user_factory):
    """enabled=Falseのユーザーは401を返す。"""
    username, password = await user_factory()
    await client.post("/api/auth/login", json={"username": username, "password": password})
    from sqlalchemy import select

    from millicall.models import User

    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        user.enabled = False
        await s.commit()
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
