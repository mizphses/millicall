import pytest


async def _make_provider(c, name, ptype, kind):
    r = await c.post(
        "/api/providers", json={"name": name, "type": ptype, "kind": kind, "config": {}}
    )
    return r.json()["id"]


async def _make_agent(c):
    llm = await _make_provider(c, "l", "llm", "openai_compatible")
    tts = await _make_provider(c, "t", "tts", "voicevox")
    stt = await _make_provider(c, "s", "stt", "whisper")
    r = await c.post(
        "/api/ai-agents",
        json={
            "name": "受付",
            "llm_provider_id": llm,
            "tts_provider_id": tts,
            "stt_provider_id": stt,
        },
    )
    return r.json()["id"]


@pytest.mark.asyncio
async def test_create_route_to_ai_agent(auth_client_with_telephony):
    c = auth_client_with_telephony
    agent_id = await _make_agent(c)
    resp = await c.post(
        "/api/routes",
        json={
            "match_number": "0312345678",
            "target_type": "ai_agent",
            "target_value": str(agent_id),
        },
    )
    assert resp.status_code == 201
    assert resp.json()["target_type"] == "ai_agent"


@pytest.mark.asyncio
async def test_reject_ai_agent_route_when_agent_missing(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.post(
        "/api/routes",
        json={"match_number": "0399999999", "target_type": "ai_agent", "target_value": "9999"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_reject_ai_agent_route_with_non_integer_value(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.post(
        "/api/routes",
        json={"match_number": "0388887777", "target_type": "ai_agent", "target_value": "abc"},
    )
    assert resp.status_code == 422
