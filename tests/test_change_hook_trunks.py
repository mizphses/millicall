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


# ---------------------------------------------------------------------------
# Wire-level: トランク変更で sofia ゲートウェイ同期(killgw + rescan)が飛ぶこと
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trunk_create_sends_gateway_sync(tmp_path) -> None:
    """トランク作成時、reloadxml に加えて killgw + rescan が FreeSWITCH に送られること。

    rescan によりゲートウェイが即ロードされ、REGISTER が直ちに試行される
    (これが無いと FS 再起動まで HGW への REGISTER が一切飛ばない)。
    """
    from millicall.config import Settings
    from millicall.main import create_app
    from tests.test_change_hook import _make_admin_client, _start_accepting_fake_fs

    server, port, received = await _start_accepting_fake_fs()

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_host="127.0.0.1",
        esl_port=port,
        esl_timeout_seconds=2.0,
    )
    application = create_app(settings)
    try:
        async with (
            application.router.lifespan_context(application),
            _make_admin_client(application) as c,
        ):
            resp = await c.post(
                "/api/trunks",
                json={
                    "name": "hgw",
                    "display_name": "HGW",
                    "host": "192.168.1.1",
                    "username": "0312345678",
                    "password": "pw",
                },
            )
            assert resp.status_code == 201
    finally:
        server.close()
        await server.wait_closed()

    reload_idx = next((i for i, cmd in enumerate(received) if "reloadxml" in cmd), None)
    killgw_idx = next(
        (i for i, cmd in enumerate(received) if "sofia profile external killgw hgw" in cmd), None
    )
    rescan_idx = next(
        (i for i, cmd in enumerate(received) if "sofia profile external rescan" in cmd), None
    )
    assert reload_idx is not None, f"reloadxml が送信されていない: {received}"
    assert killgw_idx is not None, f"killgw が送信されていない: {received}"
    assert rescan_idx is not None, f"rescan が送信されていない: {received}"
    # reloadxml → killgw → rescan の順序(XML 再読込後にゲートウェイを再ロード)
    assert reload_idx < killgw_idx < rescan_idx
