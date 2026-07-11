"""設定の管理画面移行 — 利用箇所切替の統合テスト。

PUT /api/settings で変更した値が再起動なしで各機能（SAML / SCIM / ログイン
レート制限 / ワークフローの SMTP・playback_timeout / netd serve_enabled）に
反映されることを確認する。
"""

from millicall.app_settings.service import SettingsService
from millicall.crypto import SecretBox
from millicall.workflows.runner import WorkflowRunner


async def _enable_saml(auth_client) -> None:
    """管理画面 API 経由で SAML を有効化するヘルパー。"""
    res = await auth_client.put(
        "/api/settings",
        json={
            "values": {
                "saml_enabled": True,
                "saml_sp_entity_id": "https://pbx.example.com/saml/metadata",
                "saml_sp_acs_url": "https://pbx.example.com/saml/acs",
            }
        },
    )
    assert res.status_code == 200


async def test_saml_metadata_becomes_available_after_enable(auth_client, client):
    """SAML 無効時は /saml/metadata が 404、管理画面から有効化すると 200 になる。

    再起動不要で反映されることの確認（本タスクの受け入れ条件）。
    """
    res = await client.get("/saml/metadata")
    assert res.status_code == 404

    await _enable_saml(auth_client)

    res = await client.get("/saml/metadata")
    assert res.status_code == 200
    assert "https://pbx.example.com/saml/metadata" in res.text
    assert "https://pbx.example.com/saml/acs" in res.text


async def test_saml_login_endpoint_reflects_db_settings(auth_client, client):
    """/saml/login も DB 設定を参照する（IdP SSO URL 設定後にリダイレクトする）。"""
    assert (await client.get("/saml/login")).status_code == 404

    await _enable_saml(auth_client)
    res = await auth_client.put(
        "/api/settings",
        json={"values": {"saml_idp_sso_url": "https://idp.example.com/sso"}},
    )
    assert res.status_code == 200

    res = await client.get("/saml/login", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"].startswith("https://idp.example.com/sso?SAMLRequest=")


async def test_scim_toggle_via_settings(auth_client, client):
    """SCIM は無効時 404、管理画面から有効化すると認可チェック（401）に進む。"""
    res = await client.get("/scim/v2/Users")
    assert res.status_code == 404

    res = await auth_client.put("/api/settings", json={"values": {"scim_enabled": True}})
    assert res.status_code == 200

    # 有効化後は Bearer 不備として 401（= フラグが読まれている）
    res = await client.get("/scim/v2/Users")
    assert res.status_code == 401


async def test_login_throttle_uses_db_override(auth_client, client, app):
    """login_max_attempts の DB 上書きがログインレート制限に即時反映される。"""
    res = await auth_client.put(
        "/api/settings",
        json={"values": {"login_max_attempts": 2, "login_username_max_attempts": 2}},
    )
    assert res.status_code == 200

    # 2 回失敗 → しきい値到達 → 3 回目は 429（env デフォルト 10 のままなら 401 になる）
    for _ in range(2):
        res = await client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
        assert res.status_code == 401
    res = await client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
    assert res.status_code == 429


async def test_workflow_runner_reads_effective_settings(auth_client, app):
    """WorkflowRunner は settings_service 注入時、SMTP/playback を実効設定から読む。"""
    res = await auth_client.put(
        "/api/settings",
        json={
            "values": {
                "smtp_host": "mail.example.com",
                "smtp_password": "wf-secret",
                "playback_timeout_sec": 12.5,
            }
        },
    )
    assert res.status_code == 200

    runner: WorkflowRunner = app.state.workflow_runner
    eff = await runner._effective_settings()
    assert eff.smtp_host == "mail.example.com"
    assert eff.smtp_password == "wf-secret"  # 復号済みで読める
    assert eff.playback_timeout_sec == 12.5


async def test_workflow_runner_falls_back_without_service(app):
    """settings_service 未注入（単体テスト相当）では env Settings を返す。"""
    runner = WorkflowRunner(
        sessionmaker=app.state.sessionmaker,
        secrets=app.state.secrets,
        esl=None,
        esl_lock=app.state.esl_command_lock,
        esl_reconnect=None,
        session_registry=app.state.session_registry,
        settings=app.state.settings,
        dtmf_collector=app.state.dtmf_collector,
    )
    assert (await runner._effective_settings()) is app.state.settings


async def test_effective_used_for_startup_fs_config(tmp_path):
    """起動時の FreeSWITCH 設定生成も DB 上書き（実効設定）を反映する。

    1 回目の起動で DB に上書きを保存 → 2 回目の起動で dialplan に反映されることを、
    SettingsService を直接使って確認する（lifespan は build_config_writer(effective) を使う）。
    """
    from millicall.config import Settings
    from millicall.main import create_app

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=0.1,
    )
    app1 = create_app(settings)
    async with app1.router.lifespan_context(app1):
        svc: SettingsService = app1.state.settings_service
        async with app1.state.sessionmaker() as session:
            await svc.apply_update(session, {"sip_reject_anonymous": True})
            await session.commit()
        svc.invalidate()

    # 再起動相当: 同じ DB で作り直すと実効設定が dialplan 生成に使われる
    app2 = create_app(settings)
    async with app2.router.lifespan_context(app2):
        eff = await app2.state.settings_service.effective()
        assert eff.sip_reject_anonymous is True


async def test_netd_tailscale_up_payload_overrides_env(monkeypatch):
    """netd の tailscale_up は payload の serve_enabled を env より優先する。"""
    from millicall.netd.commands import dispatch
    from tests.test_netd_commands import FakeSystemOps

    class _Settings:
        tailscale_serve_enabled = False  # netd 側の env は無効
        http_port = 80

    # status(未ログイン) → up 成功 → serve 成功
    ops = FakeSystemOps(run_responses=[(0, '{"BackendState": "NeedsLogin"}', ""), (0, "", "")])
    key = "tskey-auth-k" + "A" * 20
    resp = await dispatch(
        {"cmd": "tailscale_up", "auth_key": key, "serve_enabled": True}, ops, _Settings()
    )
    assert resp["ok"] is True
    # payload の serve_enabled=True が優先され、serve コマンドが発行される
    assert any(c[0][:2] == ["tailscale", "serve"] for c in ops.run_calls)

    # 逆: env=True でも payload=False なら serve しない
    class _ServeSettings:
        tailscale_serve_enabled = True
        http_port = 80

    ops2 = FakeSystemOps(run_responses=[(0, '{"BackendState": "NeedsLogin"}', ""), (0, "", "")])
    resp = await dispatch(
        {"cmd": "tailscale_up", "auth_key": key, "serve_enabled": False}, ops2, _ServeSettings()
    )
    assert resp["ok"] is True
    assert not any(c[0][:2] == ["tailscale", "serve"] for c in ops2.run_calls)


async def test_settings_service_secret_box_roundtrip(app):
    """app.state.settings_service は master_key と紐づく SecretBox で秘密値を扱う。"""
    svc: SettingsService = app.state.settings_service
    async with app.state.sessionmaker() as session:
        await svc.apply_update(session, {"phone_admin_password": "phone-pass"})
        await session.commit()
    svc.invalidate()
    eff = await svc.effective()
    assert eff.phone_admin_password == "phone-pass"
    # 別インスタンス（再起動相当）でも同じ master_key で復号できる
    svc2 = SettingsService(
        app.state.sessionmaker, app.state.settings, SecretBox(app.state.secrets.master_key)
    )
    assert (await svc2.effective()).phone_admin_password == "phone-pass"
