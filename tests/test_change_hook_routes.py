"""Change-hook integration tests: routes trigger public dialplan regeneration."""

import pytest


@pytest.mark.asyncio
async def test_create_route_writes_public_dialplan(auth_client_with_telephony, app):
    c = auth_client_with_telephony
    await c.post("/api/extensions", json={"number": "1001", "display_name": "A"})
    resp = await c.post(
        "/api/routes",
        json={"match_number": "0312345678", "target_type": "extension", "target_value": "1001"},
    )
    assert resp.status_code == 201
    pub = (app.state.settings.fs_config_dir / "dialplan" / "public.xml").read_text()
    assert "user/1001@" in pub
    assert 'name="inbound_0312345678"' in pub
