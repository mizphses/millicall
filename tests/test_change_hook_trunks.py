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


@pytest.mark.asyncio
async def test_trunk_delete_stops_profile(tmp_path) -> None:
    """トランク削除時、external_<name> stop が FreeSWITCH に送られること。

    削除で XML/プロファイルファイルは掃除されるが、restart では旧 in-memory
    プロファイルが残ってゴースト登録が続く。stop で明示破棄する（旧 killgw 相当）。
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
            created = await c.post(
                "/api/trunks",
                json={
                    "name": "hgw",
                    "display_name": "HGW",
                    "host": "192.168.1.1",
                    "username": "0312345678",
                    "password": "pw",
                },
            )
            assert created.status_code == 201
            tid = created.json()["id"]
            received.clear()  # 作成時のコマンドを除外し、削除分だけを見る
            deleted = await c.delete(f"/api/trunks/{tid}")
            assert deleted.status_code == 204
    finally:
        server.close()
        await server.wait_closed()

    reload_idx = next((i for i, cmd in enumerate(received) if "reloadxml" in cmd), None)
    stop_idx = next(
        (i for i, cmd in enumerate(received) if "sofia profile external_hgw stop" in cmd),
        None,
    )
    # 削除対象を restart してはいけない（ゴースト登録の原因）
    restart_sent = any("sofia profile external_hgw restart" in cmd for cmd in received)
    assert reload_idx is not None, f"reloadxml が送信されていない: {received}"
    assert stop_idx is not None, f"external_hgw stop が送信されていない: {received}"
    assert not restart_sent, f"削除トランクに restart が送られている: {received}"
    assert reload_idx < stop_idx


# ---------------------------------------------------------------------------
# issue #113: 未ロードのプロファイルへの restart は `Invalid Profile` を返す（-ERR ではない）。
# その場合は start にフォールバックし、start 応答も失敗判定する。
# ---------------------------------------------------------------------------


class _ScriptedESL:
    """コマンドを記録し、コマンドごとに応答を差し替えられる fake ESL クライアント。"""

    def __init__(self, sink: list[str], replies: dict[str, str]) -> None:
        self._sink = sink
        self._replies = replies

    async def connect(self) -> None:
        return None

    async def reloadxml(self) -> str:
        self._sink.append("reloadxml")
        return "+OK [Success]"

    async def api(self, cmd: str) -> str:
        self._sink.append(cmd)
        return self._replies.get(cmd, "+OK")

    async def close(self) -> None:
        return None


async def _notify_trunk_with_replies(app, replies: dict[str, str]) -> tuple[str | None, list[str]]:
    from millicall.models import Trunk
    from millicall.telephony.service import TelephonyChangeListener

    sm = app.state.sessionmaker
    async with sm() as session:
        session.add(
            Trunk(
                name="hgw",
                display_name="HGW",
                host="192.168.1.1",
                username="0312345678",
                password="pw",
            )
        )
        await session.commit()

    sink: list[str] = []
    listener = TelephonyChangeListener(
        app.state.change_listener._writer,
        lambda: _ScriptedESL(sink, replies),
        esl_timeout=2.0,
    )
    async with sm() as session:
        result = await listener.notify(session, sync_gateway="hgw")
    return result, sink


@pytest.mark.asyncio
async def test_new_trunk_invalid_profile_falls_back_to_start(app) -> None:
    """restart が `Invalid Profile` を返したら start を送り、成功なら None を返す。"""
    result, sink = await _notify_trunk_with_replies(
        app,
        {
            "sofia profile external_hgw restart": "Invalid Profile [external_hgw]",
            "sofia profile external_hgw start": (
                "Reload XML [Success]\nexternal_hgw started successfully\n"
            ),
        },
    )
    assert result is None
    restart_idx = sink.index("sofia profile external_hgw restart")
    start_idx = sink.index("sofia profile external_hgw start")
    assert restart_idx < start_idx


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start_reply",
    ["Invalid Profile [external_hgw]", "-ERR command failed", "Failure starting external_hgw"],
)
async def test_new_trunk_start_failure_returns_error(app, start_reply: str) -> None:
    """start も失敗した場合は反映失敗メッセージを返す。"""
    result, sink = await _notify_trunk_with_replies(
        app,
        {
            "sofia profile external_hgw restart": "Invalid Profile [external_hgw]",
            "sofia profile external_hgw start": start_reply,
        },
    )
    assert "sofia profile external_hgw start" in sink
    assert result is not None
    assert "反映できませんでした" in result


@pytest.mark.asyncio
async def test_existing_trunk_restart_success_does_not_send_start(app) -> None:
    """既存トランクで restart が成功した場合は start を送らない。"""
    result, sink = await _notify_trunk_with_replies(
        app,
        {"sofia profile external_hgw restart": "Reload XML [Success]\nrestarting: external_hgw"},
    )
    assert result is None
    assert "sofia profile external_hgw restart" in sink
    assert "sofia profile external_hgw start" not in sink


@pytest.mark.asyncio
async def test_restart_failure_text_returns_error(app) -> None:
    """restart 応答が -ERR 以外の失敗テキスト（Failure）でも失敗として扱う。"""
    result, sink = await _notify_trunk_with_replies(
        app,
        {"sofia profile external_hgw restart": "Failure restarting external_hgw"},
    )
    assert "sofia profile external_hgw start" not in sink
    assert result is not None
    assert "反映できませんでした" in result
