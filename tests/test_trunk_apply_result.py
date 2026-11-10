from pathlib import Path

from millicall.telephony.fsconfig import FreeswitchConfigWriter, TrunkConfig

PAYLOAD = {"name": "audit", "display_name": "Audit", "host": "192.168.1.1",
           "username": "audit", "password": "password", "inbound_extension": "201"}


async def test_alphanumeric_username_with_inbound_route_saves(auth_client_with_telephony, app):
    client = auth_client_with_telephony
    assert (await client.post("/api/extensions", json={"number": "201", "display_name": "Audit"})).status_code == 201
    response = await client.post("/api/trunks", json=PAYLOAD)
    assert response.status_code == 201
    assert response.json()["username"] == "audit"
    assert 'expression="^(audit)$"' in (app.state.settings.fs_config_dir / "dialplan/public.xml").read_text()
    # This test environment has no FreeSWITCH: persistence and application are distinct.
    assert response.json()["apply_warning"] is not None


async def test_invalid_configuration_rolls_back_before_file_changes(auth_client_with_telephony, app):
    client = auth_client_with_telephony
    payload = {**PAYLOAD, "inbound_extension": ""}
    before = (app.state.settings.fs_config_dir / "dialplan/public.xml").read_text()
    invalid = await client.post("/api/trunks", json={**payload, "username": "bad\u0001"})
    assert invalid.status_code == 422
    assert (await client.get("/api/trunks")).json() == []
    assert (app.state.settings.fs_config_dir / "dialplan/public.xml").read_text() == before
    created = await client.post("/api/trunks", json=payload)
    trunk_id = created.json()["id"]
    invalid_update = await client.patch(f"/api/trunks/{trunk_id}", json={"username": "bad\u0001"})
    assert invalid_update.status_code == 422
    assert (await client.get(f"/api/trunks/{trunk_id}")).json()["username"] == "audit"


async def test_post_commit_apply_failure_returns_saved_result(auth_client_with_telephony, app, monkeypatch):
    from millicall.telephony.service import TelephonyChangeListener

    async def unavailable(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(TelephonyChangeListener, "notify", unavailable)
    client = auth_client_with_telephony
    response = await client.post("/api/trunks", json={**PAYLOAD, "inbound_extension": ""})
    assert response.status_code == 201
    assert "保存されました" in response.json()["apply_warning"]
    trunk_id = response.json()["id"]
    assert (await client.get(f"/api/trunks/{trunk_id}")).status_code == 200
    updated = await client.patch(f"/api/trunks/{trunk_id}", json={"display_name": "Updated"})
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Updated"
    assert updated.json()["apply_warning"] is not None
    deleted = await client.delete(f"/api/trunks/{trunk_id}")
    assert deleted.status_code == 204
    assert deleted.headers["X-Apply-Warning"] == "telephony-apply-failed"
    assert (await client.get(f"/api/trunks/{trunk_id}")).status_code == 404


def test_username_and_did_are_xml_and_regex_escaped(tmp_path: Path):
    import re
    from xml.etree import ElementTree

    writer = FreeswitchConfigWriter(tmp_path, "localhost", "password")
    username = 'alice.+&"<name>'
    rendered = writer.render_all([], [TrunkConfig("audit", "Audit", "host", username,
        "password", did_number="+81300000000", inbound_extension="201")])
    root = ElementTree.fromstring(rendered["dialplan/public.xml"])
    expression = root.find(".//extension[@name='inbound_trunk_audit']/condition").attrib["expression"]
    assert re.fullmatch(expression, username)
    assert re.fullmatch(expression, "+81300000000")
    assert re.fullmatch(expression, "aliceZjunk") is None
    assert not list(tmp_path.iterdir())  # Preflight performs no filesystem mutation.


async def test_esl_rejection_is_reported_as_unapplied(app):
    from millicall.telephony.service import TelephonyChangeListener

    class RefusingESL:
        async def connect(self):
            pass

        async def reloadxml(self):
            return "+OK"

        async def api(self, command):
            return "-ERR profile restart failed"

        async def close(self):
            pass

    listener = TelephonyChangeListener(app.state.change_listener._writer, RefusingESL)
    async with app.state.sessionmaker() as session:
        warning = await listener.notify(session)
    assert warning is not None
    assert listener._last_internal_bind is None  # Failed restart is retried on the next save.
