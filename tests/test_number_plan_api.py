"""統一番号プラン API テスト: ring-groups CRUD / number-plan / 横断一意性。"""

import pytest


async def _make_ext(client, number="1001", name="A"):
    r = await client.post("/api/extensions", json={"number": number, "display_name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_provider(c, name, ptype, kind):
    r = await c.post(
        "/api/providers", json={"name": name, "type": ptype, "kind": kind, "config": {}}
    )
    return r.json()["id"]


async def _make_agent(c, number=None):
    llm = await _make_provider(c, "l", "llm", "openai_compatible")
    tts = await _make_provider(c, "t", "tts", "voicevox")
    stt = await _make_provider(c, "s", "stt", "whisper")
    body = {
        "name": "受付AI",
        "llm_provider_id": llm,
        "tts_provider_id": tts,
        "stt_provider_id": stt,
    }
    if number is not None:
        body["number"] = number
    r = await c.post("/api/ai-agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# ring-groups CRUD
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ring_group_crud(auth_client_with_telephony):
    c = auth_client_with_telephony
    e1 = await _make_ext(c, "1001", "A")
    e2 = await _make_ext(c, "1002", "B")

    resp = await c.post(
        "/api/ring-groups",
        json={"number": "200", "name": "営業", "member_extension_ids": [e1, e2]},
    )
    assert resp.status_code == 201, resp.text
    gid = resp.json()["id"]
    assert resp.json()["member_extension_ids"] == sorted([e1, e2])

    lst = await c.get("/api/ring-groups")
    assert any(g["id"] == gid for g in lst.json())

    upd = await c.patch(
        f"/api/ring-groups/{gid}",
        json={"number": "201", "name": "営業2", "member_extension_ids": [e1]},
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["number"] == "201"
    assert upd.json()["member_extension_ids"] == [e1]

    assert (await c.delete(f"/api/ring-groups/{gid}")).status_code == 204
    assert (await c.get(f"/api/ring-groups/{gid}")).status_code == 404


@pytest.mark.asyncio
async def test_ring_group_rejects_unknown_member(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.post(
        "/api/ring-groups",
        json={"number": "200", "name": "営業", "member_extension_ids": [9999]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ring_group_number_format(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.post(
        "/api/ring-groups",
        json={"number": "abc", "name": "営業", "member_extension_ids": []},
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# 横断一意性
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_number_conflict_extension_vs_group(auth_client_with_telephony):
    c = auth_client_with_telephony
    await _make_ext(c, "1001")
    resp = await c.post(
        "/api/ring-groups", json={"number": "1001", "name": "衝突", "member_extension_ids": []}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_number_conflict_group_vs_extension(auth_client_with_telephony):
    c = auth_client_with_telephony
    r = await c.post(
        "/api/ring-groups", json={"number": "200", "name": "g", "member_extension_ids": []}
    )
    assert r.status_code == 201
    resp = await c.post("/api/extensions", json={"number": "200", "display_name": "X"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_number_conflict_agent_vs_workflow(auth_client_with_telephony):
    c = auth_client_with_telephony
    wf = await c.post(
        "/api/workflows",
        json={
            "name": "wf",
            "number": "300",
            "description": "",
            "definition": {"nodes": [{"id": "s", "type": "start", "config": {}}], "edges": []},
        },
    )
    assert wf.status_code == 201
    agent = await _make_agent(c)
    resp = await c.patch(f"/api/ai-agents/{agent['id']}", json={"number": "300"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_agent_number_assign_and_clear(auth_client_with_telephony):
    c = auth_client_with_telephony
    agent = await _make_agent(c, number="600")
    assert agent["number"] == "600"
    # 自分自身の番号での更新は許容される
    keep = await c.patch(f"/api/ai-agents/{agent['id']}", json={"number": "600"})
    assert keep.status_code == 200
    # "" で番号を外す
    clear = await c.patch(f"/api/ai-agents/{agent['id']}", json={"number": ""})
    assert clear.status_code == 200
    assert clear.json()["number"] is None


# --------------------------------------------------------------------------- #
# number-plan 一覧
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_number_plan_lists_all_kinds(auth_client_with_telephony):
    c = auth_client_with_telephony
    e1 = await _make_ext(c, "1001")
    await c.post(
        "/api/ring-groups", json={"number": "200", "name": "営業", "member_extension_ids": [e1]}
    )
    await _make_agent(c, number="600")
    await c.post(
        "/api/workflows",
        json={
            "name": "wf",
            "number": "300",
            "description": "",
            "definition": {"nodes": [{"id": "s", "type": "start", "config": {}}], "edges": []},
        },
    )
    plan = (await c.get("/api/number-plan")).json()
    kinds = {p["number"]: p["kind"] for p in plan}
    assert kinds["1001"] == "extension"
    assert kinds["200"] == "ring_group"
    assert kinds["600"] == "ai_agent"
    assert kinds["300"] == "workflow"


@pytest.mark.asyncio
async def test_number_plan_shows_inbound_trunks(auth_client_with_telephony):
    c = auth_client_with_telephony
    await _make_ext(c, "1001")
    r = await c.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "HGW",
            "host": "192.168.1.1",
            "username": "30",
            "password": "pw",
            "inbound_extension": "1001",
        },
    )
    assert r.status_code == 201, r.text
    plan = (await c.get("/api/number-plan")).json()
    entry = next(p for p in plan if p["number"] == "1001")
    assert entry["inbound_trunks"] == ["hgw"]


# --------------------------------------------------------------------------- #
# trunk inbound_extension 検証
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_trunk_inbound_extension_must_exist(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "HGW",
            "host": "192.168.1.1",
            "username": "30",
            "password": "pw",
            "inbound_extension": "9999",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_trunk_inbound_extension_accepts_group(auth_client_with_telephony):
    c = auth_client_with_telephony
    e1 = await _make_ext(c, "1001")
    await c.post(
        "/api/ring-groups", json={"number": "200", "name": "営業", "member_extension_ids": [e1]}
    )
    resp = await c.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "HGW",
            "host": "192.168.1.1",
            "username": "30",
            "password": "pw",
            "inbound_extension": "200",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["inbound_extension"] == "200"
