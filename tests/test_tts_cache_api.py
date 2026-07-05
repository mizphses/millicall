import pytest


@pytest.mark.asyncio
async def test_synthesize_404_for_missing_provider(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.post("/api/tts-cache/synthesize", json={"provider_id": 999, "text": "案内文"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_synthesize_422_for_non_tts_provider(auth_client_with_telephony):
    c = auth_client_with_telephony
    r = await c.post(
        "/api/providers",
        json={"name": "l", "type": "llm", "kind": "openai_compatible", "config": {}},
    )
    pid = r.json()["id"]
    resp = await c.post("/api/tts-cache/synthesize", json={"provider_id": pid, "text": "x"})
    assert resp.status_code == 422
