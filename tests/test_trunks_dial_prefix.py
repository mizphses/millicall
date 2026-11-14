"""発信プレフィクス・発信に使う・既定の発信トランク（issue #139）の API テスト。"""

import pytest
import pytest_asyncio

_BASE = {"display_name": "A", "host": "h", "username": "u", "password": "p"}


@pytest_asyncio.fixture
async def admin_client(auth_client_with_telephony):
    return auth_client_with_telephony


async def _create(client, name, **kw):
    return await client.post("/api/trunks", json={**_BASE, "name": name, **kw})


async def test_defaults_empty_prefix_and_outbound_enabled(admin_client):
    resp = await _create(admin_client, "hgw")
    assert resp.status_code == 201, resp.text
    assert resp.json()["dial_prefix"] == ""
    assert resp.json()["outbound_enabled"] is True


async def test_create_and_patch_dial_prefix(admin_client, app):
    resp = await _create(admin_client, "Flets01", caller_id="0445895782")
    assert resp.status_code == 201, resp.text
    resp = await _create(admin_client, "Flets02", caller_id="0445895784", dial_prefix="92")
    assert resp.status_code == 201, resp.text
    tid = resp.json()["id"]
    assert resp.json()["dial_prefix"] == "92"
    dialplan = app.state.settings.fs_config_dir / "dialplan" / "default.xml"
    text = dialplan.read_text()
    assert 'name="outbound_external_via_Flets02"' in text
    assert "sofia/gateway/Flets02/${destination_number:2}" in text

    patched = await admin_client.patch(f"/api/trunks/{tid}", json={"dial_prefix": "*2"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["dial_prefix"] == "*2"
    assert 'expression="^\\*2((?:18[46])?0\\d+)$"' in dialplan.read_text()

    # 他フィールドだけの PATCH では値を保持する
    kept = await admin_client.patch(f"/api/trunks/{tid}", json={"display_name": "B"})
    assert kept.json()["dial_prefix"] == "*2"

    # "" でプレフィクスなしに戻す
    cleared = await admin_client.patch(f"/api/trunks/{tid}", json={"dial_prefix": ""})
    assert cleared.json()["dial_prefix"] == ""
    assert "outbound_external_via_Flets02" not in dialplan.read_text()


@pytest.mark.parametrize(
    ("bad", "fragment"),
    [
        ("9a", "0-9・*・#"),
        ("123456789", "1〜8 文字"),
        ("9 2", "0-9・*・#"),
        ("09", "0 で始められません"),
        ("186", "184/186 で始まる"),
        ("184", "184/186 で始まる"),
        ("110", "1 で始まる 3 桁"),
    ],
)
async def test_invalid_dial_prefix_422(admin_client, bad, fragment):
    resp = await _create(admin_client, "hgw", dial_prefix=bad)
    assert resp.status_code == 422, resp.text
    assert fragment in resp.json()["detail"]


async def test_invalid_dial_prefix_on_patch_422(admin_client):
    tid = (await _create(admin_client, "hgw")).json()["id"]
    resp = await admin_client.patch(f"/api/trunks/{tid}", json={"dial_prefix": "0"})
    assert resp.status_code == 422
    assert "0 で始められません" in resp.json()["detail"]


async def test_duplicate_and_overlapping_prefix_422(admin_client):
    assert (await _create(admin_client, "a", dial_prefix="92")).status_code == 201
    dup = await _create(admin_client, "b", dial_prefix="92")
    assert dup.status_code == 422
    assert "重複" in dup.json()["detail"]
    overlap = await _create(admin_client, "b", dial_prefix="920")
    assert overlap.status_code == 422
    assert "先頭が重なり" in overlap.json()["detail"]
    shorter = await _create(admin_client, "b", dial_prefix="9")
    assert shorter.status_code == 422
    assert (await _create(admin_client, "b", dial_prefix="93")).status_code == 201


async def test_patch_same_prefix_on_self_is_allowed(admin_client):
    tid = (await _create(admin_client, "a", dial_prefix="92")).json()["id"]
    resp = await admin_client.patch(f"/api/trunks/{tid}", json={"dial_prefix": "92"})
    assert resp.status_code == 200, resp.text


async def test_prefix_conflicting_with_extension_422(admin_client):
    ext = await admin_client.post(
        "/api/extensions", json={"number": "92", "display_name": "内線92"}
    )
    assert ext.status_code == 201, ext.text
    resp = await _create(admin_client, "hgw", dial_prefix="92")
    assert resp.status_code == 422
    assert "番号プラン" in resp.json()["detail"]


async def test_extension_conflicting_with_prefix_409(admin_client):
    assert (await _create(admin_client, "hgw", dial_prefix="93")).status_code == 201
    ext = await admin_client.post(
        "/api/extensions", json={"number": "93", "display_name": "内線93"}
    )
    assert ext.status_code == 409, ext.text


async def test_outbound_disabled_trunk_is_not_default(admin_client, app):
    a = await _create(admin_client, "a", caller_id="0311111111", outbound_enabled=False)
    assert a.status_code == 201, a.text
    assert a.json()["outbound_enabled"] is False
    assert (await _create(admin_client, "b", caller_id="0322222222")).status_code == 201
    text = (app.state.settings.fs_config_dir / "dialplan" / "default.xml").read_text()
    assert "sofia/gateway/b/${destination_number}" in text
    assert "sofia/gateway/a/" not in text


async def test_default_outbound_trunk_setting(admin_client, app):
    await _create(admin_client, "a", caller_id="0311111111")
    b = (await _create(admin_client, "b", caller_id="0322222222")).json()
    dialplan = app.state.settings.fs_config_dir / "dialplan" / "default.xml"
    assert "sofia/gateway/a/${destination_number}" in dialplan.read_text()

    res = await admin_client.put(
        "/api/settings", json={"values": {"default_outbound_trunk_id": b["id"]}}
    )
    assert res.status_code == 200, res.text
    assert res.json()["values"]["default_outbound_trunk_id"] == b["id"]
    text = dialplan.read_text()
    assert "sofia/gateway/b/${destination_number}" in text
    assert "effective_caller_id_number=0322222222" in text

    # 既定トランクを発信に使わなくすると名前順先頭へフォールバックする
    await admin_client.patch(f"/api/trunks/{b['id']}", json={"outbound_enabled": False})
    assert "sofia/gateway/a/${destination_number}" in dialplan.read_text()

    # 未設定に戻す
    res = await admin_client.put("/api/settings", json={"reset": ["default_outbound_trunk_id"]})
    assert res.status_code == 200
    assert res.json()["values"]["default_outbound_trunk_id"] is None


async def test_default_outbound_trunk_unknown_id_400(admin_client):
    res = await admin_client.put(
        "/api/settings", json={"values": {"default_outbound_trunk_id": 9999}}
    )
    assert res.status_code == 400
    assert "見つかりません" in res.json()["detail"]
    res = await admin_client.put("/api/settings", json={"values": {"default_outbound_trunk_id": 0}})
    assert res.status_code == 400
