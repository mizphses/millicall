import pytest

pytestmark = pytest.mark.asyncio


async def test_create_and_list_contact(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.post(
        "/api/contacts",
        json={"name": "山田太郎", "phone_number": "09012345678", "company": "ACME",
              "department": "営業", "notes": "重要"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "山田太郎"
    lst = await c.get("/api/contacts")
    assert lst.status_code == 200
    assert len(lst.json()) == 1


async def test_patch_and_delete_contact(auth_client_with_telephony):
    c = auth_client_with_telephony
    created = await c.post("/api/contacts", json={"name": "A", "phone_number": "0311112222"})
    cid = created.json()["id"]
    patched = await c.patch(f"/api/contacts/{cid}", json={"company": "NewCo"})
    assert patched.status_code == 200
    assert patched.json()["company"] == "NewCo"
    assert (await c.delete(f"/api/contacts/{cid}")).status_code == 204
    assert (await c.get(f"/api/contacts/{cid}")).status_code == 404


async def test_contacts_require_auth(client):
    assert (await client.get("/api/contacts")).status_code == 401
