import pytest


async def _make_ext(client, number="1001"):
    r = await client.post("/api/extensions", json={"number": number, "display_name": "A"})
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_create_route_to_extension(auth_client_with_telephony):
    c = auth_client_with_telephony
    await _make_ext(c, "1001")
    resp = await c.post(
        "/api/routes",
        json={"match_number": "0312345678", "target_type": "extension", "target_value": "1001"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["target_type"] == "extension"


@pytest.mark.asyncio
async def test_route_rejects_unknown_extension(auth_client_with_telephony):
    resp = await auth_client_with_telephony.post(
        "/api/routes",
        json={"match_number": "0312345678", "target_type": "extension", "target_value": "9999"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_route_rejects_unsupported_target_type(auth_client_with_telephony):
    resp = await auth_client_with_telephony.post(
        "/api/routes",
        json={"match_number": "0312345678", "target_type": "workflow", "target_value": "x"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_route_duplicate_match_number_conflict(auth_client_with_telephony):
    c = auth_client_with_telephony
    await _make_ext(c, "1001")
    payload = {"match_number": "0312345678", "target_type": "extension", "target_value": "1001"}
    assert (await c.post("/api/routes", json=payload)).status_code == 201
    assert (await c.post("/api/routes", json=payload)).status_code == 409


@pytest.mark.asyncio
async def test_list_and_delete_route(auth_client_with_telephony):
    c = auth_client_with_telephony
    await _make_ext(c, "1001")
    created = await c.post(
        "/api/routes",
        json={"match_number": "0312345678", "target_type": "extension", "target_value": "1001"},
    )
    rid = created.json()["id"]
    assert len((await c.get("/api/routes")).json()) == 1
    assert (await c.delete(f"/api/routes/{rid}")).status_code == 204


@pytest.mark.asyncio
async def test_routes_require_auth(client):
    assert (await client.get("/api/routes")).status_code == 401


@pytest.mark.asyncio
async def test_patch_route_target_value_nonexistent_extension(auth_client_with_telephony):
    c = auth_client_with_telephony
    await _make_ext(c, "1001")
    created = await c.post(
        "/api/routes",
        json={"match_number": "0312345678", "target_type": "extension", "target_value": "1001"},
    )
    rid = created.json()["id"]
    # PATCH to nonexistent extension → 422
    resp = await c.patch(f"/api/routes/{rid}", json={"target_value": "9999"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_delete_missing_route_id(auth_client_with_telephony):
    c = auth_client_with_telephony
    # GET missing route id → 404
    resp = await c.get("/api/routes/99999")
    assert resp.status_code == 404
    # DELETE missing route id → 404
    resp = await c.delete("/api/routes/99999")
    assert resp.status_code == 404
