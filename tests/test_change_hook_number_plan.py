"""Change-hook: 番号プラン変更(グループ/トランク着信先)で dialplan が再生成されること。"""

import pytest


@pytest.mark.asyncio
async def test_ring_group_create_writes_default_dialplan(auth_client_with_telephony, app):
    c = auth_client_with_telephony
    e = await c.post("/api/extensions", json={"number": "1001", "display_name": "A"})
    eid = e.json()["id"]
    resp = await c.post(
        "/api/ring-groups",
        json={"number": "200", "name": "営業", "member_extension_ids": [eid]},
    )
    assert resp.status_code == 201, resp.text
    dp = (app.state.settings.fs_config_dir / "dialplan" / "default.xml").read_text()
    assert 'name="ring_group_200"' in dp
    assert "user/1001@" in dp


@pytest.mark.asyncio
async def test_trunk_inbound_extension_writes_public_dialplan(auth_client_with_telephony, app):
    c = auth_client_with_telephony
    await c.post("/api/extensions", json={"number": "1001", "display_name": "A"})
    resp = await c.post(
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
    assert resp.status_code == 201, resp.text
    pub = (app.state.settings.fs_config_dir / "dialplan" / "public.xml").read_text()
    assert 'name="inbound_trunk_hgw"' in pub
    assert '<action application="transfer" data="1001 XML default"/>' in pub


@pytest.mark.asyncio
async def test_agent_number_writes_default_dialplan(auth_client_with_telephony, app):
    c = auth_client_with_telephony

    async def make_provider(name, ptype, kind):
        r = await c.post(
            "/api/providers", json={"name": name, "type": ptype, "kind": kind, "config": {}}
        )
        return r.json()["id"]

    llm = await make_provider("l", "llm", "openai_compatible")
    tts = await make_provider("t", "tts", "voicevox")
    stt = await make_provider("s", "stt", "whisper")
    resp = await c.post(
        "/api/ai-agents",
        json={
            "name": "受付AI",
            "number": "600",
            "llm_provider_id": llm,
            "tts_provider_id": tts,
            "stt_provider_id": stt,
        },
    )
    assert resp.status_code == 201, resp.text
    agent_id = resp.json()["id"]
    dp = (app.state.settings.fs_config_dir / "dialplan" / "default.xml").read_text()
    assert 'name="ai_agent_600"' in dp
    assert f"millicall_ai_agent={agent_id}" in dp
