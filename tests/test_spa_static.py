import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from millicall.config import Settings
from millicall.main import create_app


def _settings(tmp_path, static_dir):
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        static_dir=static_dir,
    )


@pytest_asyncio.fixture
async def static_client(tmp_path):
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text(
        "<!doctype html><title>millicall</title><div id=root></div>", encoding="utf-8"
    )
    (static_dir / "assets" / "app-abc123.js").write_text("console.log('x')", encoding="utf-8")

    app = create_app(_settings(tmp_path, static_dir))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest_asyncio.fixture
async def no_static_client(tmp_path):
    # index.html が無いパスを指す -> SPA 無効（開発モード相当）
    app = create_app(_settings(tmp_path, tmp_path / "does-not-exist"))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_root_serves_index_html(static_client):
    resp = await static_client.get("/")
    assert resp.status_code == 200
    assert "id=root" in resp.text


async def test_client_route_falls_back_to_index(static_client):
    # /extensions のような未定義パスは index.html にフォールバック
    resp = await static_client.get("/extensions")
    assert resp.status_code == 200
    assert "id=root" in resp.text


async def test_hashed_assets_served(static_client):
    resp = await static_client.get("/assets/app-abc123.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


async def test_healthz_not_swallowed(static_client):
    resp = await static_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_api_not_swallowed_by_fallback(static_client):
    # 認証必須 API は 401 を返す（index.html を返さない）
    resp = await static_client.get("/api/auth/me")
    assert resp.status_code == 401
    assert "id=root" not in resp.text


async def test_unknown_api_path_returns_404_not_index(static_client):
    resp = await static_client.get("/api/no-such-endpoint")
    assert resp.status_code == 404
    assert "id=root" not in resp.text


async def test_no_static_dir_disables_spa(no_static_client):
    # SPA 無効時は catch-all が無く、未定義パスは 404
    resp = await no_static_client.get("/extensions")
    assert resp.status_code == 404
    # 既存ルートは通常どおり
    health = await no_static_client.get("/healthz")
    assert health.status_code == 200
