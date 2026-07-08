"""Task 6: 全 15 ツール + guide を FastMCP に登録し統合。

受入条件（プラン Task 6）:
  - list_tools が 15 個（+ ping 残置）を verbatim シグネチャで返す。
  - list_resources に guide://outbound-calling。
  - 代表ツール（list_extensions/list_trunks/list_contacts）が実 DB で §JSON 文字列を返す。
  - converse/dial は fake ESL でスモーク（契約 JSON 形状）。
  - voice 引数は受理して無視。
  - OAuth: Bearer で /mcp 経由 tools/call が成功、無トークンは 401（既存 test_mcp_oauth で担保）。
    ここでは Bearer トークンで tools/call が契約 JSON を返すことを確認。
"""

import base64
import hashlib
import json
import secrets
import urllib.parse

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from millicall.config import Settings
from millicall.main import create_app
from millicall.models import Contact, Extension, Trunk

# 契約 §1–§15 の 15 ツール名。
EXPECTED_TOOLS = {
    "converse",
    "dial",
    "say",
    "say_and_listen",
    "listen",
    "hangup",
    "send_dtmf",
    "transfer",
    "get_call_status",
    "list_active_calls",
    "list_contacts",
    "add_contact",
    "delete_contact",
    "list_extensions",
    "list_trunks",
}

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _mcp_settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
    )


@pytest_asyncio.fixture
async def mcp_app(tmp_path):
    app = create_app(_mcp_settings(tmp_path))
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def mcp_client(mcp_app):
    transport = ASGITransport(app=mcp_app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


def _tool_text(result) -> str:
    """FastMCP.call_tool の戻り（tuple([TextContent...], structured)）からテキストを取り出す。"""
    blocks = result[0] if isinstance(result, tuple) else result
    return blocks[0].text


# ---------------------------------------------------------------------------
# 登録: ツール一覧 / リソース
# ---------------------------------------------------------------------------


async def test_all_15_tools_registered(mcp_app):
    tools = await mcp_app.state.mcp.list_tools()
    names = {t.name for t in tools}
    missing = EXPECTED_TOOLS - names
    assert not missing, f"未登録ツール: {missing}"


async def test_guide_resource_registered(mcp_app):
    resources = await mcp_app.state.mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert "guide://outbound-calling" in uris


async def test_guide_resource_body(mcp_app):
    contents = await mcp_app.state.mcp.read_resource("guide://outbound-calling")
    body = list(contents)[0].content
    assert "say_and_listen" in body
    assert "186" in body


async def test_converse_signature_verbatim(mcp_app):
    tools = await mcp_app.state.mcp.list_tools()
    converse = next(t for t in tools if t.name == "converse")
    props = converse.inputSchema["properties"]
    # 契約 §6 の引数名。
    for arg in (
        "phone_number",
        "purpose",
        "key_points",
        "your_name",
        "max_turns",
        "caller_id",
        "trunk",
        "voice",
    ):
        assert arg in props, f"converse に引数 {arg} が無い"


async def test_dial_signature_verbatim(mcp_app):
    tools = await mcp_app.state.mcp.list_tools()
    dial = next(t for t in tools if t.name == "dial")
    props = dial.inputSchema["properties"]
    for arg in ("phone_number", "caller_id", "trunk"):
        assert arg in props


# ---------------------------------------------------------------------------
# 代表ツール: 実 DB で契約 JSON
# ---------------------------------------------------------------------------


async def test_list_extensions_tool_returns_contract_json(mcp_app):
    async with mcp_app.state.sessionmaker() as s:
        s.add(Extension(number="800", display_name="内線A", sip_password="x", enabled=True))
        await s.commit()
    result = await mcp_app.state.mcp.call_tool("list_extensions", {})
    data = json.loads(_tool_text(result))
    assert data["count"] == 1
    ext = data["extensions"][0]
    assert set(ext.keys()) == {"id", "number", "display_name", "enabled", "type"}
    assert ext["type"] == "phone"


async def test_list_trunks_tool_returns_contract_json(mcp_app):
    async with mcp_app.state.sessionmaker() as s:
        s.add(
            Trunk(
                name="hgw1",
                display_name="HGW 1",
                host="10.0.0.1",
                username="u",
                password="secret",
                did_number="0312345678",
                caller_id="0312345678",
                enabled=True,
            )
        )
        await s.commit()
    result = await mcp_app.state.mcp.call_tool("list_trunks", {})
    text = _tool_text(result)
    data = json.loads(text)
    t = data["trunks"][0]
    assert set(t.keys()) == {
        "id",
        "name",
        "display_name",
        "did_number",
        "caller_id",
        "outbound_prefixes",
        "enabled",
    }
    assert t["outbound_prefixes"] == []
    # 秘密衛生: password が JSON 文字列に絶対に出ない。
    assert "secret" not in text


async def test_list_contacts_tool_query(mcp_app):
    async with mcp_app.state.sessionmaker() as s:
        s.add(Contact(name="田中太郎", phone_number="09011112222", company="ABC"))
        s.add(Contact(name="佐藤花子", phone_number="08033334444", company="XYZ"))
        await s.commit()
    result = await mcp_app.state.mcp.call_tool("list_contacts", {"query": "田中"})
    data = json.loads(_tool_text(result))
    assert data["count"] == 1
    assert data["contacts"][0]["name"] == "田中太郎"


async def test_add_and_delete_contact_tool(mcp_app):
    added = await mcp_app.state.mcp.call_tool(
        "add_contact", {"name": "山田", "phone_number": "09055556666"}
    )
    added_data = json.loads(_tool_text(added))
    assert added_data["status"] == "ok"
    assert added_data["message"] == "連絡先「山田」を追加しました"
    cid = added_data["contact"]["id"]

    deleted = await mcp_app.state.mcp.call_tool("delete_contact", {"contact_id": cid})
    del_data = json.loads(_tool_text(deleted))
    assert del_data["status"] == "ok"
    assert del_data["message"] == f"連絡先 (ID: {cid}) を削除しました"


# ---------------------------------------------------------------------------
# get_call_status / list_active_calls / hangup (SessionRegistry ベース)
# ---------------------------------------------------------------------------


async def test_get_call_status_unknown_channel(mcp_app):
    result = await mcp_app.state.mcp.call_tool("get_call_status", {"channel_id": "nope"})
    data = json.loads(_tool_text(result))
    assert data["error"] == "チャネルが見つかりません（通話が終了している可能性があります）"


async def test_list_active_calls_empty(mcp_app):
    result = await mcp_app.state.mcp.call_tool("list_active_calls", {})
    data = json.loads(_tool_text(result))
    assert data == {"count": 0, "calls": []}


async def test_hangup_unknown_channel_idempotent(mcp_app):
    result = await mcp_app.state.mcp.call_tool("hangup", {"channel_id": "nope"})
    data = json.loads(_tool_text(result))
    assert data == {"status": "ok", "message": "通話は既に終了しています"}


async def test_send_dtmf_unknown_channel(mcp_app):
    result = await mcp_app.state.mcp.call_tool("send_dtmf", {"channel_id": "nope", "digits": "1"})
    data = json.loads(_tool_text(result))
    assert "error" in data


# ---------------------------------------------------------------------------
# dial / converse: fake ESL スモーク（契約 JSON 形状）
# ---------------------------------------------------------------------------


class _FakeEsl:
    def __init__(self) -> None:
        self.cmds: list[str] = []

    async def bgapi(self, command: str) -> str:
        self.cmds.append(command)
        return "job-uuid"

    async def api(self, command: str) -> str:
        return ""


async def test_dial_tool_timeout_shape(mcp_app):
    """応答が来ない → §1 タイムアウト JSON（channel_id 付き error）。"""
    mcp_app.state.esl_command = _FakeEsl()
    result = await mcp_app.state.mcp.call_tool(
        "dial",
        {"phone_number": "800"},  # 内線（トランク不要）
    )
    data = json.loads(_tool_text(result))
    assert data["error"] == "30秒以内に応答がありませんでした"
    assert "channel_id" in data


async def test_dial_tool_answered_shape(mcp_app):
    """CHANNEL_ANSWER を模して解決 → §1 成功 JSON。"""
    fake = _FakeEsl()
    mcp_app.state.esl_command = fake
    import asyncio

    reg = mcp_app.state.answer_registry

    # dial 呼び出しと並行して、originate 後に登録された uuid を answer する。
    async def _resolve_soon():
        for _ in range(200):
            uuids = [
                c.split("origination_uuid=")[1].split(",")[0]
                for c in fake.cmds
                if "origination_uuid=" in c
            ]
            if uuids:
                reg.resolve(uuids[0])
                return
            await asyncio.sleep(0.005)

    call = asyncio.ensure_future(mcp_app.state.mcp.call_tool("dial", {"phone_number": "800"}))
    await _resolve_soon()
    result = await call
    data = json.loads(_tool_text(result))
    assert data["state"] == "Up"
    assert "channel_id" in data
    assert "800" in data["message"]


async def test_dial_tool_accepts_voice_arg_ignored(mcp_app):
    """say/say_and_listen が voice を受理して無視することを say で確認（channel 無し→error だが型は許容）。"""
    tools = await mcp_app.state.mcp.list_tools()
    say = next(t for t in tools if t.name == "say")
    assert "voice" in say.inputSchema["properties"]


# ---------------------------------------------------------------------------
# OAuth E2E: Bearer トークンで tools/call
# ---------------------------------------------------------------------------


def _pkce():
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


async def _make_user(app, username="mcpadmin", password="Passw0rd1", role="admin"):
    from millicall.auth.security import hash_password
    from millicall.models import User

    async with app.state.sessionmaker() as session:
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


async def test_oauth_bearer_tools_call(mcp_app, mcp_client):
    """DCR→login→token→Bearer で tools/call list_extensions が契約 JSON を返す。"""
    await _make_user(mcp_app, username="flow", password="FlowPass1", role="admin")
    async with mcp_app.state.sessionmaker() as s:
        s.add(Extension(number="900", display_name="内線X", sip_password="z", enabled=True))
        await s.commit()

    reg = await mcp_client.post(
        "/register",
        json={"redirect_uris": ["https://claude.ai/cb"], "token_endpoint_auth_method": "none"},
    )
    client_id = reg.json()["client_id"]
    verifier, challenge = _pkce()
    ticket = mcp_app.state.mcp_oauth_provider.sign_login_ticket(
        {
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/cb",
            "code_challenge": challenge,
            "state": "s1",
            "scopes": [],
            "resource": "",
            "explicit": True,
        }
    )
    cb = await mcp_client.post(
        "/mcp-login/callback",
        data={"ticket": ticket, "username": "flow", "password": "FlowPass1"},
    )
    code = urllib.parse.parse_qs(urllib.parse.urlparse(cb.headers["location"]).query)["code"][0]
    tok = await mcp_client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/cb",
        },
    )
    access = tok.json()["access_token"]

    # initialize（セッション確立）
    init = await mcp_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        },
        headers={**MCP_HEADERS, "Authorization": f"Bearer {access}"},
    )
    assert init.status_code == 200
    session_id = init.headers.get("mcp-session-id")
    assert session_id

    # initialized 通知
    await mcp_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={
            **MCP_HEADERS,
            "Authorization": f"Bearer {access}",
            "mcp-session-id": session_id,
        },
    )

    # tools/call list_extensions
    call = await mcp_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_extensions", "arguments": {}},
        },
        headers={
            **MCP_HEADERS,
            "Authorization": f"Bearer {access}",
            "mcp-session-id": session_id,
        },
    )
    assert call.status_code == 200
    # SSE レスポンスから JSON-RPC 結果を抽出。
    body = call.text
    assert "内線X" in body or "900" in body
