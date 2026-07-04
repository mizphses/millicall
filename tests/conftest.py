import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from millicall.auth.security import hash_password
from millicall.config import Settings
from millicall.main import create_app
from millicall.models import User


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
    async with AsyncClient(transport=transport, base_url="http://test") as c:
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
async def auth_client_with_telephony(client, user_factory):
    username, password = await user_factory(username="fsadmin", password="Fs4dminPass")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    return client
