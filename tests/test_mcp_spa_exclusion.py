"""Phase 4a Task 1: SPA catch-all が /mcp・/.well-known を食わないこと。

static (SPA) 有効時に、未定義の /mcp サブパスや well-known が index.html に
フォールバックされず、MCP ルートが優先されることを確認する。
"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from millicall.config import Settings
from millicall.main import _SPA_EXCLUDED_PREFIXES, create_app


@pytest_asyncio.fixture
async def spa_mcp_client(tmp_path):
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text(
        "<!doctype html><title>millicall</title><div id=root></div>", encoding="utf-8"
    )
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        mcp_issuer_url="http://localhost",
        mcp_allowed_hosts=["localhost", "127.0.0.1"],
        static_dir=static_dir,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as c:
            yield c


def test_excluded_prefixes_include_mcp_and_wellknown():
    assert "mcp" in _SPA_EXCLUDED_PREFIXES
    assert ".well-known" in _SPA_EXCLUDED_PREFIXES


async def test_wellknown_not_swallowed_by_spa(spa_mcp_client):
    r = await spa_mcp_client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    assert "id=root" not in r.text


async def test_mcp_subpath_returns_404_not_index(spa_mcp_client):
    # /mcp 配下の未定義パスは index.html ではなく 404（SPA 除外プレフィクス）。
    r = await spa_mcp_client.get("/mcp/does-not-exist")
    assert r.status_code == 404
    assert "id=root" not in r.text


async def test_mcp_get_without_token_not_index(spa_mcp_client):
    # /mcp 自体は MCP ルートが処理し、SPA index を返さない。
    r = await spa_mcp_client.get("/mcp")
    assert r.status_code != 200
    assert "id=root" not in r.text


async def test_spa_route_still_falls_back(spa_mcp_client):
    r = await spa_mcp_client.get("/extensions")
    assert r.status_code == 200
    assert "id=root" in r.text
