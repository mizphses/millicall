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
    ext = (app.state.settings.fs_config_dir / "sip_profiles" / "external_hgw.xml").read_text()
    assert 'gateway name="hgw"' in ext
    assert 'name="external_hgw"' in ext


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
    # 無効化で有効トランクが 0 本になるため external_hgw.xml は掃除されて残らない
    assert not (app.state.settings.fs_config_dir / "sip_profiles" / "external_hgw.xml").exists()


# ---------------------------------------------------------------------------
# Wire-level: トランク変更で sofia プロファイル再起動(external_<name> restart)が飛ぶこと
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trunk_create_sends_gateway_sync(tmp_path) -> None:
    """トランク作成時、reloadxml に加えて external_<name> restart が FreeSWITCH に送られること。

    restart によりプロファイル(=ゲートウェイ)が即ロードされ、REGISTER が直ちに試行される
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
    restart_idx = next(
        (i for i, cmd in enumerate(received) if "sofia profile external_hgw restart" in cmd),
        None,
    )
    assert reload_idx is not None, f"reloadxml が送信されていない: {received}"
    assert restart_idx is not None, f"external_hgw restart が送信されていない: {received}"
    # reloadxml → restart の順序(XML 再読込後にプロファイルを再ロード)
    assert reload_idx < restart_idx
