"""H1: MCP ツール層のロールベース認可テスト。

カバレッジ:
  - _require_admin_subject: AccessToken なし → ValueError
  - _require_admin_subject: subject の User が存在しない → ValueError
  - _require_admin_subject: role="user" → ValueError（権限不足）
  - _require_admin_subject: role="admin" → 成功（username 返却）
  - 実ツール呼び出し: role="user" の subject が admin-only ツールを呼ぶ → エラー JSON
  - 実ツール呼び出し: role="admin" の subject → 権限エラーなし（処理続行）
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from millicall.auth.security import hash_password
from millicall.config import Settings
from millicall.main import create_app
from millicall.models import User

# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

MCP_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _mcp_settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        mcp_issuer_url="http://localhost",
        mcp_allowed_hosts=["localhost", "127.0.0.1"],
        static_dir=tmp_path / "no-static",
    )


@pytest_asyncio.fixture
async def mcp_app(tmp_path):
    app = create_app(_mcp_settings(tmp_path))
    async with app.router.lifespan_context(app):
        yield app


async def _make_user(app, username, password="Passw0rd1", role="admin", enabled=True):
    async with app.state.sessionmaker() as session:
        session.add(
            User(
                username=username,
                hashed_password=hash_password(password),
                display_name=username,
                role=role,
                origin="local",
                enabled=enabled,
            )
        )
        await session.commit()
    return username, password


# ---------------------------------------------------------------------------
# _require_admin_subject 単体テスト（contextvars モック経由）
# ---------------------------------------------------------------------------


async def test_require_admin_no_token(mcp_app):
    """AccessToken が存在しない場合 ValueError を送出する。"""
    from millicall.mcp_server.tools import _require_admin_subject

    state = mcp_app.state
    with (
        patch("millicall.mcp_server.tools.get_access_token", return_value=None),
        pytest.raises(ValueError, match="認証情報が取得できません"),
    ):
        await _require_admin_subject(state)


async def test_require_admin_empty_subject(mcp_app):
    """AccessToken に subject がない場合 ValueError を送出する。"""
    from millicall.mcp_server.tools import _require_admin_subject

    state = mcp_app.state
    mock_token = MagicMock()
    mock_token.subject = ""
    with (
        patch("millicall.mcp_server.tools.get_access_token", return_value=mock_token),
        pytest.raises(ValueError, match="認証情報が取得できません"),
    ):
        await _require_admin_subject(state)


async def test_require_admin_unknown_user(mcp_app):
    """DB に存在しないユーザー名の場合 ValueError を送出する。"""
    from millicall.mcp_server.tools import _require_admin_subject

    state = mcp_app.state
    mock_token = MagicMock()
    mock_token.subject = "ghost_nonexistent"
    with (
        patch("millicall.mcp_server.tools.get_access_token", return_value=mock_token),
        pytest.raises(ValueError, match="ユーザーが見つかりません"),
    ):
        await _require_admin_subject(state)


async def test_require_admin_user_role_denied(mcp_app):
    """role="user" の場合 ValueError を送出する（admin のみ許可）。"""
    from millicall.mcp_server.tools import _require_admin_subject

    await _make_user(mcp_app, username="regusr", role="user")
    state = mcp_app.state
    mock_token = MagicMock()
    mock_token.subject = "regusr"
    with (
        patch("millicall.mcp_server.tools.get_access_token", return_value=mock_token),
        pytest.raises(ValueError, match="管理者権限"),
    ):
        await _require_admin_subject(state)


async def test_require_admin_admin_role_allowed(mcp_app):
    """role="admin" の場合 username を返却する。"""
    from millicall.mcp_server.tools import _require_admin_subject

    await _make_user(mcp_app, username="adminusr", role="admin")
    state = mcp_app.state
    mock_token = MagicMock()
    mock_token.subject = "adminusr"
    with patch(
        "millicall.mcp_server.tools.get_access_token",
        return_value=mock_token,
    ):
        result = await _require_admin_subject(state)
    assert result == "adminusr"


# ---------------------------------------------------------------------------
# ツール経由の認可テスト（mcp.call_tool 相当をモックで確認）
# ---------------------------------------------------------------------------


def _extract_text(result) -> str:
    """call_tool の戻り値から JSON テキストを取り出す。

    FastMCP の call_tool は (list[ContentBlock], dict) のタプルを返す。
    list の先頭要素の .text を取り出す。
    """
    content_blocks, _ = result
    return content_blocks[0].text


async def test_tool_add_contact_denied_for_user_role(mcp_app):
    """role="user" の subject が add_contact を呼ぶとエラー JSON を返す（H1）。"""
    await _make_user(mcp_app, username="noadmin", role="user")

    mock_token = MagicMock()
    mock_token.subject = "noadmin"

    with patch(
        "millicall.mcp_server.tools.get_access_token",
        return_value=mock_token,
    ):
        # FastMCP の tool を直接呼び出す
        tools = {t.name: t for t in await mcp_app.state.mcp.list_tools()}
        assert "add_contact" in tools

        result = await mcp_app.state.mcp.call_tool(
            "add_contact",
            {"name": "Test", "phone_number": "09012345678"},
        )
    data = json.loads(_extract_text(result))
    assert "error" in data
    assert "管理者権限" in data["error"]


async def test_tool_delete_contact_denied_for_user_role(mcp_app):
    """role="user" の subject が delete_contact を呼ぶとエラー JSON を返す（H1）。"""
    await _make_user(mcp_app, username="noadmin2", role="user")

    mock_token = MagicMock()
    mock_token.subject = "noadmin2"

    with patch(
        "millicall.mcp_server.tools.get_access_token",
        return_value=mock_token,
    ):
        result = await mcp_app.state.mcp.call_tool("delete_contact", {"contact_id": 999})
    data = json.loads(_extract_text(result))
    assert "error" in data
    assert "管理者権限" in data["error"]


async def test_tool_dial_denied_for_user_role(mcp_app):
    """role="user" の subject が dial を呼ぶとエラー JSON を返す（H1）。"""
    await _make_user(mcp_app, username="noadmin3", role="user")

    mock_token = MagicMock()
    mock_token.subject = "noadmin3"

    with patch(
        "millicall.mcp_server.tools.get_access_token",
        return_value=mock_token,
    ):
        result = await mcp_app.state.mcp.call_tool(
            "dial", {"phone_number": "09000000000"}
        )
    data = json.loads(_extract_text(result))
    assert "error" in data
    assert "管理者権限" in data["error"]


async def test_tool_add_contact_allowed_for_admin_role(mcp_app):
    """role="admin" の subject が add_contact を呼ぶと権限エラーにならない（処理続行）。"""
    await _make_user(mcp_app, username="adminonly", role="admin")

    mock_token = MagicMock()
    mock_token.subject = "adminonly"

    with patch(
        "millicall.mcp_server.tools.get_access_token",
        return_value=mock_token,
    ):
        result = await mcp_app.state.mcp.call_tool(
            "add_contact",
            {"name": "田中太郎", "phone_number": "09011112222"},
        )
    data = json.loads(_extract_text(result))
    # 権限エラーでないこと（add_contact 自体の成功または DB エラー）
    assert "管理者権限" not in data.get("error", "")


async def test_tool_list_contacts_allowed_for_user_role(mcp_app):
    """role="user" の subject が list_contacts（読み取り専用）を呼べる（H1: 読み取りは許可）。"""
    await _make_user(mcp_app, username="readonly", role="user")

    mock_token = MagicMock()
    mock_token.subject = "readonly"

    with patch(
        "millicall.mcp_server.tools.get_access_token",
        return_value=mock_token,
    ):
        result = await mcp_app.state.mcp.call_tool("list_contacts", {})
    data = json.loads(_extract_text(result))
    # エラーでなく結果リストが返る（管理者権限エラーではないこと）
    assert "管理者権限" not in data.get("error", "")
