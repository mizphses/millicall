import pytest


def _valid_definition():
    return {
        "nodes": [
            {"id": "s", "type": "start", "config": {}},
            {"id": "e", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "edge1", "source": "s", "target": "e", "sourceHandle": "out"},
        ],
    }


async def _make_provider(c, name, ptype, kind):
    r = await c.post(
        "/api/providers", json={"name": name, "type": ptype, "kind": kind, "config": {}}
    )
    return r.json()["id"]


async def _create_workflow(c, number="0501112222", name="受付フロー", definition=None):
    return await c.post(
        "/api/workflows",
        json={
            "name": name,
            "number": number,
            "description": "テスト",
            "definition": definition if definition is not None else _valid_definition(),
        },
    )


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_workflow_valid(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await _create_workflow(c)
    assert resp.status_code == 201
    body = resp.json()
    assert body["number"] == "0501112222"
    assert body["name"] == "受付フロー"
    assert body["warnings"] == []
    assert body["definition"]["nodes"][0]["type"] == "start"


@pytest.mark.asyncio
async def test_create_workflow_gui_minimal_definition(auth_client_with_telephony):
    """GUI の新規作成が送る最小定義(start ノードのみ・edges 空)で 201 になること。

    WorkflowsPage の createMutation と同じペイロード。空グラフ {nodes: [], edges: []}
    は「start ちょうど1個」制約で 422 になるため、GUI は start 入りで作成する契約。
    """
    c = auth_client_with_telephony
    resp = await _create_workflow(
        c,
        number="0503334444",
        name="新規フロー",
        definition={
            "nodes": [
                {"id": "start", "type": "start", "position": {"x": 80, "y": 80}, "config": {}}
            ],
            "edges": [],
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_list_and_get_workflow(auth_client_with_telephony):
    c = auth_client_with_telephony
    wid = (await _create_workflow(c)).json()["id"]
    lst = await c.get("/api/workflows")
    assert lst.status_code == 200
    assert any(w["id"] == wid for w in lst.json())
    one = await c.get(f"/api/workflows/{wid}")
    assert one.status_code == 200
    assert one.json()["id"] == wid


@pytest.mark.asyncio
async def test_get_missing_workflow_404(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.get("/api/workflows/9999")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Validation (422)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reject_bad_source_handle(auth_client_with_telephony):
    c = auth_client_with_telephony
    definition = _valid_definition()
    definition["edges"][0]["sourceHandle"] = "nope"
    resp = await _create_workflow(c, definition=definition)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_reject_missing_required_config(auth_client_with_telephony):
    c = auth_client_with_telephony
    definition = {
        "nodes": [
            {"id": "s", "type": "start", "config": {}},
            # play_audio requires tts_text
            {"id": "p", "type": "play_audio", "config": {}},
        ],
        "edges": [{"id": "e1", "source": "s", "target": "p", "sourceHandle": "out"}],
    }
    resp = await _create_workflow(c, definition=definition)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_reject_missing_start(auth_client_with_telephony):
    c = auth_client_with_telephony
    definition = {
        "nodes": [{"id": "e", "type": "end", "config": {}}],
        "edges": [],
    }
    resp = await _create_workflow(c, definition=definition)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unreachable_node_returns_warnings(auth_client_with_telephony):
    c = auth_client_with_telephony
    definition = {
        "nodes": [
            {"id": "s", "type": "start", "config": {}},
            {"id": "e", "type": "end", "config": {}},
            {"id": "orphan", "type": "hangup", "config": {}},
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e", "sourceHandle": "out"}],
    }
    resp = await _create_workflow(c, definition=definition)
    assert resp.status_code == 201
    assert any("orphan" in w for w in resp.json()["warnings"])


# --------------------------------------------------------------------------- #
# number uniqueness (409)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_duplicate_number_conflict(auth_client_with_telephony):
    c = auth_client_with_telephony
    assert (await _create_workflow(c, number="0500000001", name="a")).status_code == 201
    dup = await _create_workflow(c, number="0500000001", name="b")
    assert dup.status_code == 409


# --------------------------------------------------------------------------- #
# Route auto-provisioning
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_appears_in_number_plan(auth_client_with_telephony):
    c = auth_client_with_telephony
    wid = (await _create_workflow(c, number="0509998888")).json()["id"]
    plan = (await c.get("/api/number-plan")).json()
    match = [p for p in plan if p["number"] == "0509998888"]
    assert len(match) == 1
    assert match[0]["kind"] == "workflow"
    assert match[0]["id"] == wid


@pytest.mark.asyncio
async def test_update_number_updates_number_plan(auth_client_with_telephony):
    c = auth_client_with_telephony
    wid = (await _create_workflow(c, number="0501010101")).json()["id"]
    resp = await c.put(
        f"/api/workflows/{wid}",
        json={
            "name": "受付フロー",
            "number": "0502020202",
            "definition": _valid_definition(),
        },
    )
    assert resp.status_code == 200
    plan = (await c.get("/api/number-plan")).json()
    numbers = {p["number"] for p in plan if p["kind"] == "workflow"}
    assert numbers == {"0502020202"}


@pytest.mark.asyncio
async def test_delete_removes_from_number_plan(auth_client_with_telephony):
    c = auth_client_with_telephony
    wid = (await _create_workflow(c, number="0503030303")).json()["id"]
    resp = await c.delete(f"/api/workflows/{wid}")
    assert resp.status_code == 204
    assert (await c.get(f"/api/workflows/{wid}")).status_code == 404
    plan = (await c.get("/api/number-plan")).json()
    assert not [p for p in plan if p["number"] == "0503030303"]


@pytest.mark.asyncio
async def test_workflow_number_conflicts_with_extension(auth_client_with_telephony):
    """統一番号プラン: 内線と同じ番号のワークフローは 409。"""
    c = auth_client_with_telephony
    r = await c.post("/api/extensions", json={"number": "1001", "display_name": "A"})
    assert r.status_code == 201
    resp = await _create_workflow(c, number="1001")
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# node-types / handles delivery
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_node_types_endpoint(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.get("/api/workflows/node-types")
    assert resp.status_code == 200
    catalog = resp.json()
    assert len(catalog) == 19
    start = next(n for n in catalog if n["type"] == "start")
    assert start["output_handles"] == ["out"]
    assert isinstance(start["config_schema"], list)


@pytest.mark.asyncio
async def test_handles_endpoint(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.get("/api/workflows/handles")
    assert resp.status_code == 200
    vocab = resp.json()
    assert vocab["condition"] == ["true", "false"]
    assert vocab["api_call"] == ["success", "error"]


# --------------------------------------------------------------------------- #
# AI generation
# --------------------------------------------------------------------------- #


class _FakeLLM:
    def __init__(self, text):
        self._text = text

    async def stream_chat(self, messages):
        yield self._text


@pytest.mark.asyncio
async def test_generate_valid(auth_client_with_telephony, monkeypatch):
    c = auth_client_with_telephony
    await _make_provider(c, "l", "llm", "openai_compatible")
    import json

    payload = json.dumps(_valid_definition())

    def _fake_build(kind, config, api_key):
        return _FakeLLM(f"```json\n{payload}\n```")

    monkeypatch.setattr("millicall.ai.registry.build_llm", _fake_build)
    resp = await c.post("/api/workflows/generate", json={"prompt": "受付を作って"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["definition"]["nodes"][0]["type"] == "start"
    assert body["warnings"] == []


@pytest.mark.asyncio
async def test_generate_invalid_output_rejected(auth_client_with_telephony, monkeypatch):
    c = auth_client_with_telephony
    await _make_provider(c, "l", "llm", "openai_compatible")

    def _fake_build(kind, config, api_key):
        # no start node -> validate_graph errors
        return _FakeLLM('{"nodes": [{"id": "e", "type": "end", "config": {}}], "edges": []}')

    monkeypatch.setattr("millicall.ai.registry.build_llm", _fake_build)
    resp = await c.post("/api/workflows/generate", json={"prompt": "壊れたやつ"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_generate_without_llm_provider(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.post("/api/workflows/generate", json={"prompt": "無理"})
    assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# admin guard
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_requires_auth(client):
    resp = await client.get("/api/workflows")
    assert resp.status_code == 401
