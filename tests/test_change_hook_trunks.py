"""Change-hook integration tests: trunk gateway config regeneration."""

import pytest


@pytest.mark.asyncio
async def test_create_trunk_writes_external_profile(auth_client_with_telephony, app):
    resp = await auth_client_with_telephony.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "HGW",
            "host": "192.168.1.1",
            "username": "0312345678",
            "password": "pw",
            "caller_id": "0312345678",
        },
    )
    assert resp.status_code == 201
    ext = (app.state.settings.fs_config_dir / "sip_profiles" / "external.xml").read_text()
    assert 'gateway name="hgw"' in ext


@pytest.mark.asyncio
async def test_disabled_trunk_excluded_from_external(auth_client_with_telephony, app):
    created = await auth_client_with_telephony.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "HGW",
            "host": "192.168.1.1",
            "username": "u",
            "password": "pw",
        },
    )
    tid = created.json()["id"]
    await auth_client_with_telephony.patch(f"/api/trunks/{tid}", json={"enabled": False})
    ext = (app.state.settings.fs_config_dir / "sip_profiles" / "external.xml").read_text()
    assert 'gateway name="hgw"' not in ext
