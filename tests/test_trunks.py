import pytest_asyncio


@pytest_asyncio.fixture
async def admin_client(auth_client_with_telephony):
    return auth_client_with_telephony


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
