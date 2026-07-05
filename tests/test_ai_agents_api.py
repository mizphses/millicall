import pytest


async def _make_provider(c, name, ptype, kind):
    r = await c.post(
        "/api/providers", json={"name": name, "type": ptype, "kind": kind, "config": {}}
    )
    return r.json()["id"]


@pytest.mark.asyncio
async def test_create_ai_agent(auth_client_with_telephony):
    c = auth_client_with_telephony
    llm = await _make_provider(c, "l", "llm", "openai_compatible")
    tts = await _make_provider(c, "t", "tts", "voicevox")
    stt = await _make_provider(c, "s", "stt", "whisper")
    resp = await c.post(
        "/api/ai-agents",
        json={
            "name": "受付",
            "system_prompt": "あなたは受付です",
            "greeting": "お電話ありがとうございます",
            "llm_provider_id": llm,
            "tts_provider_id": tts,
            "stt_provider_id": stt,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["silence_end_ms"] == 600
    assert body["max_history"] == 10


@pytest.mark.asyncio
async def test_reject_wrong_provider_type(auth_client_with_telephony):
    c = auth_client_with_telephony
    tts = await _make_provider(c, "t", "tts", "voicevox")
    resp = await c.post(
        "/api/ai-agents",
        json={
            "name": "x",
            "llm_provider_id": tts,  # tts を llm 欄に → 422
            "tts_provider_id": tts,
            "stt_provider_id": tts,
        },
    )
    assert resp.status_code == 422
