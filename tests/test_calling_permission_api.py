"""Extension API の calling_permission フィールドに関するテスト（トールフラウド対策 §7）。

- 作成時に各権限ティアを指定できる
- 省略時はデフォルト "domestic"
- 無効値 → 422
- PATCH で更新できる
- GET レスポンスに calling_permission が含まれる
"""

import pytest_asyncio


@pytest_asyncio.fixture
async def auth_client(client, user_factory):
    username, password = await user_factory(username="admin_cp", password="Adm1nPass2")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    return client


# ---- 作成テスト ----


async def test_create_with_domestic_permission(auth_client) -> None:
    resp = await auth_client.post(
        "/api/extensions",
        json={"number": "2001", "display_name": "Domestic", "calling_permission": "domestic"},
    )
    assert resp.status_code == 201
    assert resp.json()["calling_permission"] == "domestic"


async def test_create_with_internal_permission(auth_client) -> None:
    resp = await auth_client.post(
        "/api/extensions",
        json={"number": "2002", "display_name": "InternalOnly", "calling_permission": "internal"},
    )
    assert resp.status_code == 201
    assert resp.json()["calling_permission"] == "internal"


async def test_create_with_international_permission(auth_client) -> None:
    resp = await auth_client.post(
        "/api/extensions",
        json={
            "number": "2003",
            "display_name": "International",
            "calling_permission": "international",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["calling_permission"] == "international"


async def test_create_default_permission_is_domestic(auth_client) -> None:
    """calling_permission を省略すると "domestic" がデフォルト。"""
    resp = await auth_client.post(
        "/api/extensions",
        json={"number": "2004", "display_name": "Default"},
    )
    assert resp.status_code == 201
    assert resp.json()["calling_permission"] == "domestic"


async def test_create_invalid_permission_rejected(auth_client) -> None:
    """無効な calling_permission 値 → 422。"""
    resp = await auth_client.post(
        "/api/extensions",
        json={"number": "2005", "display_name": "Bad", "calling_permission": "all"},
    )
    assert resp.status_code == 422


async def test_create_permission_free_form_rejected(auth_client) -> None:
    """任意文字列も拒否される。"""
    resp = await auth_client.post(
        "/api/extensions",
        json={
            "number": "2006",
            "display_name": "FreeForm",
            "calling_permission": "unrestricted",
        },
    )
    assert resp.status_code == 422


# ---- 読み取りテスト ----


async def test_get_returns_calling_permission(auth_client) -> None:
    created = await auth_client.post(
        "/api/extensions",
        json={"number": "2007", "display_name": "Read", "calling_permission": "internal"},
    )
    ext_id = created.json()["id"]
    resp = await auth_client.get(f"/api/extensions/{ext_id}")
    assert resp.status_code == 200
    assert resp.json()["calling_permission"] == "internal"


async def test_list_includes_calling_permission(auth_client) -> None:
    await auth_client.post(
        "/api/extensions",
        json={"number": "2008", "display_name": "Listed", "calling_permission": "international"},
    )
    resp = await auth_client.get("/api/extensions")
    assert resp.status_code == 200
    entries = {e["number"]: e for e in resp.json()}
    assert "calling_permission" in entries["2008"]
    assert entries["2008"]["calling_permission"] == "international"


# ---- 更新テスト ----


async def test_patch_calling_permission(auth_client) -> None:
    created = await auth_client.post(
        "/api/extensions",
        json={"number": "2009", "display_name": "ToUpdate", "calling_permission": "domestic"},
    )
    ext_id = created.json()["id"]

    upd = await auth_client.patch(
        f"/api/extensions/{ext_id}", json={"calling_permission": "international"}
    )
    assert upd.status_code == 200
    assert upd.json()["calling_permission"] == "international"

    # 再取得でも反映されている
    resp = await auth_client.get(f"/api/extensions/{ext_id}")
    assert resp.json()["calling_permission"] == "international"


async def test_patch_invalid_permission_rejected(auth_client) -> None:
    created = await auth_client.post(
        "/api/extensions",
        json={"number": "2010", "display_name": "ToUpdate2"},
    )
    ext_id = created.json()["id"]
    upd = await auth_client.patch(
        f"/api/extensions/{ext_id}", json={"calling_permission": "god_mode"}
    )
    assert upd.status_code == 422


async def test_patch_calling_permission_domestic_to_internal(auth_client) -> None:
    created = await auth_client.post(
        "/api/extensions",
        json={"number": "2011", "display_name": "Downgrade"},
    )
    ext_id = created.json()["id"]
    upd = await auth_client.patch(
        f"/api/extensions/{ext_id}", json={"calling_permission": "internal"}
    )
    assert upd.status_code == 200
    assert upd.json()["calling_permission"] == "internal"
