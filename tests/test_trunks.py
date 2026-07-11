import pytest_asyncio

from millicall.models import Trunk


@pytest_asyncio.fixture
async def admin_client(auth_client_with_telephony):
    return auth_client_with_telephony


def test_trunk_repr_excludes_password():
    """Assert password value does not appear in repr of a Trunk instance."""
    t = Trunk(name="x", display_name="X", host="h", username="u", password="topsecret1")
    assert "topsecret1" not in repr(t)


async def test_create_trunk_masks_password(admin_client):
    resp = await admin_client.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "ひかり電話",
            "host": "192.168.1.1",
            "username": "0312345678",
            "password": "secret-hgw-pw",
            "did_number": "0312345678",
            "caller_id": "0312345678",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "hgw"
    assert body["has_password"] is True
    assert "password" not in body  # write-only: 実値はレスポンスに出さない


async def test_create_trunk_duplicate_name_conflict(admin_client):
    payload = {
        "name": "hgw",
        "display_name": "A",
        "host": "192.168.1.1",
        "username": "u",
        "password": "p",
    }
    r1 = await admin_client.post("/api/trunks", json=payload)
    assert r1.status_code == 201
    r2 = await admin_client.post("/api/trunks", json=payload)
    assert r2.status_code == 409


async def test_list_and_get_trunk(admin_client):
    await admin_client.post(
        "/api/trunks",
        json={"name": "hgw", "display_name": "A", "host": "h", "username": "u", "password": "p"},
    )
    lst = await admin_client.get("/api/trunks")
    assert lst.status_code == 200
    assert len(lst.json()) == 1
    tid = lst.json()[0]["id"]
    one = await admin_client.get(f"/api/trunks/{tid}")
    assert one.status_code == 200
    assert "password" not in one.json()


async def test_patch_trunk_updates_password_but_hides_it(admin_client):
    created = await admin_client.post(
        "/api/trunks",
        json={"name": "hgw", "display_name": "A", "host": "h", "username": "u", "password": "p"},
    )
    tid = created.json()["id"]
    patched = await admin_client.patch(
        f"/api/trunks/{tid}", json={"password": "newpw", "display_name": "B"}
    )
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "B"
    assert "password" not in patched.json()


async def test_update_without_password_keeps_existing(admin_client):
    """Create trunk with password, PATCH only display_name, verify password persists."""
    created = await admin_client.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "Original",
            "host": "h",
            "username": "u",
            "password": "secret",
        },
    )
    assert created.status_code == 201
    tid = created.json()["id"]

    patched = await admin_client.patch(f"/api/trunks/{tid}", json={"display_name": "Renamed"})
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "Renamed"
    assert patched.json()["has_password"] is True

    fetched = await admin_client.get(f"/api/trunks/{tid}")
    assert fetched.status_code == 200
    assert fetched.json()["display_name"] == "Renamed"
    assert fetched.json()["has_password"] is True


async def test_delete_trunk(admin_client):
    created = await admin_client.post(
        "/api/trunks",
        json={"name": "hgw", "display_name": "A", "host": "h", "username": "u", "password": "p"},
    )
    tid = created.json()["id"]
    d = await admin_client.delete(f"/api/trunks/{tid}")
    assert d.status_code == 204
    assert (await admin_client.get(f"/api/trunks/{tid}")).status_code == 404


async def test_trunks_require_auth(client):
    assert (await client.get("/api/trunks")).status_code == 401


# ---------------------------------------------------------------------------
# GET /api/trunks/{id}/status — sofia ゲートウェイ登録状態
# ---------------------------------------------------------------------------


class _FakeESL:
    """trunk_status 用のフェイク ESL クライアント。"""

    def __init__(self, response: str = "", fail_connect: bool = False) -> None:
        self.response = response
        self.fail_connect = fail_connect
        self.commands: list[str] = []

    async def connect(self) -> None:
        if self.fail_connect:
            raise OSError("FreeSWITCH unreachable")

    async def api(self, command: str) -> str:
        self.commands.append(command)
        return self.response

    async def close(self) -> None:
        return None


_SOFIA_REGED = "Name    \thgw\nProfile \texternal\nState   \tREGED\nStatus  \tUP\n"
_SOFIA_FAILED = "Name    \thgw\nProfile \texternal\nState   \tFAIL_WAIT\nStatus  \tDOWN\n"


async def _create_hgw(admin_client) -> int:
    resp = await admin_client.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "ひかり電話",
            "host": "192.168.1.1",
            "username": "0312345678",
            "password": "pw",
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_trunk_status_registered(admin_client, app):
    tid = await _create_hgw(admin_client)
    fake = _FakeESL(response=_SOFIA_REGED)
    app.state.esl_factory = lambda: fake
    resp = await admin_client.get(f"/api/trunks/{tid}/status")
    assert resp.status_code == 200
    assert resp.json() == {"registered": True, "state": "REGED"}
    assert fake.commands == ["sofia status gateway hgw"]


async def test_trunk_status_fail_wait(admin_client, app):
    tid = await _create_hgw(admin_client)
    app.state.esl_factory = lambda: _FakeESL(response=_SOFIA_FAILED)
    resp = await admin_client.get(f"/api/trunks/{tid}/status")
    assert resp.json() == {"registered": False, "state": "FAIL_WAIT"}


async def test_trunk_status_gateway_not_loaded(admin_client, app):
    tid = await _create_hgw(admin_client)
    app.state.esl_factory = lambda: _FakeESL(response="Invalid Gateway!\n")
    resp = await admin_client.get(f"/api/trunks/{tid}/status")
    assert resp.json() == {"registered": False, "state": "NOT_LOADED"}


async def test_trunk_status_fs_unreachable_returns_unknown(admin_client, app):
    tid = await _create_hgw(admin_client)
    app.state.esl_factory = lambda: _FakeESL(fail_connect=True)
    resp = await admin_client.get(f"/api/trunks/{tid}/status")
    assert resp.status_code == 200
    assert resp.json() == {"registered": False, "state": "UNKNOWN"}


async def test_trunk_status_missing_trunk_404(admin_client):
    resp = await admin_client.get("/api/trunks/9999/status")
    assert resp.status_code == 404


# --- source_port（トランクごと送信元 SIP ポート） ---


async def test_create_trunk_defaults_source_port_null(admin_client):
    """source_port 未指定なら null（自動採番）で作成される。"""
    resp = await admin_client.post(
        "/api/trunks",
        json={"name": "hgw", "display_name": "A", "host": "h", "username": "u", "password": "p"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["source_port"] is None


async def test_create_trunk_with_explicit_source_port(admin_client):
    resp = await admin_client.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "A",
            "host": "h",
            "username": "u",
            "password": "p",
            "source_port": 5082,
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["source_port"] == 5082


async def test_create_trunk_source_port_conflicts_internal_400(admin_client):
    """internal の sip_port(5060) との衝突は 400。"""
    resp = await admin_client.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "A",
            "host": "h",
            "username": "u",
            "password": "p",
            "source_port": 5060,
        },
    )
    assert resp.status_code == 400, resp.text


async def test_create_trunk_source_port_duplicate_400(admin_client):
    """他トランクと同一の明示ポートは 400。"""
    first = await admin_client.post(
        "/api/trunks",
        json={
            "name": "aaa",
            "display_name": "A",
            "host": "h",
            "username": "u",
            "password": "p",
            "source_port": 5090,
        },
    )
    assert first.status_code == 201
    dup = await admin_client.post(
        "/api/trunks",
        json={
            "name": "bbb",
            "display_name": "B",
            "host": "h",
            "username": "u",
            "password": "p",
            "source_port": 5090,
        },
    )
    assert dup.status_code == 400, dup.text


async def test_create_trunk_source_port_out_of_range_422(admin_client):
    """範囲外(<1024)は pydantic バリデーションで 422。"""
    resp = await admin_client.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "A",
            "host": "h",
            "username": "u",
            "password": "p",
            "source_port": 80,
        },
    )
    assert resp.status_code == 422, resp.text


async def test_patch_trunk_clears_source_port_to_auto(admin_client):
    """明示ポートを null 送信すると自動採番へ戻る。"""
    created = await admin_client.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "A",
            "host": "h",
            "username": "u",
            "password": "p",
            "source_port": 5082,
        },
    )
    tid = created.json()["id"]
    patched = await admin_client.patch(f"/api/trunks/{tid}", json={"source_port": None})
    assert patched.status_code == 200, patched.text
    assert patched.json()["source_port"] is None
