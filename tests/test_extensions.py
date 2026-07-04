import pytest_asyncio


@pytest_asyncio.fixture
async def auth_client(client, user_factory):
    username, password = await user_factory(username="admin2", password="Adm1nPass")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    return client


async def test_create_requires_auth(client) -> None:
    resp = await client.post("/api/extensions", json={"number": "1001", "display_name": "A"})
    assert resp.status_code == 401


async def test_create_extension_generates_sip_password(auth_client) -> None:
    resp = await auth_client.post(
        "/api/extensions", json={"number": "1001", "display_name": "Alice"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["number"] == "1001"
    assert body["display_name"] == "Alice"
    assert body["enabled"] is True
    assert len(body["sip_password"]) >= 16  # 自動生成


async def test_sip_password_not_client_settable(auth_client) -> None:
    resp = await auth_client.post(
        "/api/extensions",
        json={"number": "1002", "display_name": "Bob", "sip_password": "hacked"},
    )
    assert resp.status_code == 201
    assert resp.json()["sip_password"] != "hacked"


async def test_invalid_number_rejected(auth_client) -> None:
    resp = await auth_client.post("/api/extensions", json={"number": "12ab", "display_name": "X"})
    assert resp.status_code == 422


async def test_number_too_short_rejected(auth_client) -> None:
    resp = await auth_client.post("/api/extensions", json={"number": "1", "display_name": "X"})
    assert resp.status_code == 422


async def test_number_too_long_rejected(auth_client) -> None:
    resp = await auth_client.post(
        "/api/extensions", json={"number": "1234567", "display_name": "X"}
    )
    assert resp.status_code == 422


async def test_fullwidth_digits_rejected(auth_client) -> None:
    # Unicode fullwidth digits must not be accepted (pattern is [0-9], not \d)
    resp = await auth_client.post(
        "/api/extensions", json={"number": "１２３４", "display_name": "X"}
    )
    assert resp.status_code == 422


async def test_duplicate_number_rejected(auth_client) -> None:
    await auth_client.post("/api/extensions", json={"number": "1003", "display_name": "A"})
    dup = await auth_client.post("/api/extensions", json={"number": "1003", "display_name": "B"})
    assert dup.status_code == 409


async def test_list_and_get(auth_client) -> None:
    created = await auth_client.post(
        "/api/extensions", json={"number": "1004", "display_name": "C"}
    )
    ext_id = created.json()["id"]
    lst = await auth_client.get("/api/extensions")
    assert lst.status_code == 200
    assert any(e["number"] == "1004" for e in lst.json())
    one = await auth_client.get(f"/api/extensions/{ext_id}")
    assert one.status_code == 200
    assert one.json()["number"] == "1004"


async def test_update_extension(auth_client) -> None:
    created = await auth_client.post(
        "/api/extensions", json={"number": "1005", "display_name": "old"}
    )
    ext_id = created.json()["id"]
    upd = await auth_client.patch(
        f"/api/extensions/{ext_id}", json={"display_name": "new", "enabled": False}
    )
    assert upd.status_code == 200
    assert upd.json()["display_name"] == "new"
    assert upd.json()["enabled"] is False


async def test_delete_extension(auth_client) -> None:
    created = await auth_client.post(
        "/api/extensions", json={"number": "1006", "display_name": "D"}
    )
    ext_id = created.json()["id"]
    dele = await auth_client.delete(f"/api/extensions/{ext_id}")
    assert dele.status_code == 204
    gone = await auth_client.get(f"/api/extensions/{ext_id}")
    assert gone.status_code == 404
