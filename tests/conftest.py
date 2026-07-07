import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from millicall.auth.security import hash_password
from millicall.config import Settings
from millicall.main import create_app
from millicall.models import User

_CSRF_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CSRF_HEADER = "X-CSRF-Token"
_CSRF_COOKIE = "millicall_csrf"


class CsrfAwareClient(AsyncClient):
    """CSRF トークンを自動注入する AsyncClient。

    状態変更リクエスト（POST/PUT/PATCH/DELETE）を送る前に、クライアントの
    cookie jar から millicall_csrf を読んで X-CSRF-Token ヘッダーに自動設定する。
    ヘッダーが既に設定されている場合はそのまま使う（テストで上書き可能）。

    CSRF ミドルウェア（Phase 6 Task 3）を有効化しても既存テストが壊れないよう
    全テストで使用する共通クライアント。
    """

    async def request(self, method: str, url, **kwargs):
        if method.upper() in _CSRF_METHODS:
            # cookie jar から CSRF トークンを取得してヘッダーに注入
            csrf_token = self.cookies.get(_CSRF_COOKIE)
            if csrf_token:
                headers = dict(kwargs.get("headers") or {})
                if _CSRF_HEADER not in headers:
                    headers[_CSRF_HEADER] = csrf_token
                kwargs["headers"] = headers
        return await super().request(method, url, **kwargs)


@pytest_asyncio.fixture
async def app(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,  # small timeout; ESL is unreachable in CI
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def user_factory(app):
    async def _create(username: str = "tester", password: str = "Passw0rd1", role: str = "admin"):
        sm = app.state.sessionmaker
        async with sm() as session:
            session.add(
                User(
                    username=username,
                    hashed_password=hash_password(password),
                    display_name=username,
                    role=role,
                    origin="local",
                )
            )
            await session.commit()
        return username, password

    return _create


@pytest_asyncio.fixture
async def auth_client(client, user_factory):
    """認証済みクライアント: ログイン後に CSRF ヘッダーを自動付与する。

    CsrfAwareClient が cookie jar から自動注入するため、ログインするだけでよい。
    """
    username, password = await user_factory(username="auth_user", password="Passw0rd1")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    return client


@pytest_asyncio.fixture
async def auth_client_with_telephony(client, user_factory):
    username, password = await user_factory(username="fsadmin", password="Fs4dminPass")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    return client
