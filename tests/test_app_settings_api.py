"""アプリ設定 API（GET/PUT /api/settings）のテスト。

管理者専用アクセス制御・秘密値のマスク・allowlist 検証・監査ログ記録・
FreeSWITCH 設定再生成トリガを確認する。
"""

from sqlalchemy import select

from millicall.models import AppSetting, AuditLog


async def test_get_settings_requires_auth(client):
    """未認証は 401。"""
    res = await client.get("/api/settings")
    assert res.status_code == 401


async def test_settings_requires_admin_role(client, user_factory):
    """user ロールは 403（GET / PUT とも）。"""
    username, password = await user_factory(username="plain", role="user")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    assert (await client.get("/api/settings")).status_code == 403
    res = await client.put("/api/settings", json={"values": {"saml_enabled": True}})
    assert res.status_code == 403


async def test_get_settings_returns_effective_values_and_masks_secrets(auth_client):
    """GET は実効値を返し、秘密キーは values に含めず「設定済みか」だけ返す。"""
    res = await auth_client.get("/api/settings")
    assert res.status_code == 200
    body = res.json()
    assert body["values"]["saml_enabled"] is False
    assert body["values"]["vad_mode"] == 2
    # 秘密キーは values に露出しない
    assert "smtp_password" not in body["values"]
    assert "phone_admin_password" not in body["values"]
    assert body["secrets"] == {"smtp_password": False, "phone_admin_password": False}
    assert body["overridden"] == []


async def test_put_settings_persists_and_reflects(auth_client, app):
    """PUT で上書きが保存され、GET / 実効 Settings に反映される。"""
    res = await auth_client.put(
        "/api/settings",
        json={"values": {"saml_enabled": True, "login_max_attempts": 5}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["values"]["saml_enabled"] is True
    assert body["values"]["login_max_attempts"] == 5
    assert sorted(body["overridden"]) == ["login_max_attempts", "saml_enabled"]

    eff = await app.state.settings_service.effective()
    assert eff.saml_enabled is True
    assert eff.login_max_attempts == 5


async def test_put_settings_rejects_non_allowlisted_key(auth_client):
    """allowlist 外のキーは 400。"""
    res = await auth_client.put("/api/settings", json={"values": {"database_url": "x"}})
    assert res.status_code == 400
    assert "database_url" in res.json()["detail"]


async def test_put_settings_rejects_invalid_value(auth_client):
    """型不正・レンジ外は 400。"""
    assert (
        await auth_client.put("/api/settings", json={"values": {"vad_mode": 9}})
    ).status_code == 400
    assert (
        await auth_client.put("/api/settings", json={"values": {"smtp_port": "abc"}})
    ).status_code == 400


async def test_put_secret_masks_in_audit_and_encrypts(auth_client, app):
    """秘密値は監査ログに実値を残さず、DB にも平文を置かない。"""
    res = await auth_client.put("/api/settings", json={"values": {"smtp_password": "TopSecret99"}})
    assert res.status_code == 200
    assert res.json()["secrets"]["smtp_password"] is True

    async with app.state.sessionmaker() as session:
        row = await session.get(AppSetting, "smtp_password")
        assert row is not None
        assert "TopSecret99" not in row.value

        logs = (await session.scalars(select(AuditLog))).all()
        settings_logs = [entry for entry in logs if entry.action == "settings.update"]
        assert settings_logs, "settings.update の監査ログが記録されていること"
        for entry in settings_logs:
            assert "TopSecret99" not in (entry.detail or "")
        assert "***" in (settings_logs[-1].detail or "")


async def test_put_settings_records_audit(auth_client, app):
    """変更キーと値（非秘密）が監査ログの detail に記録される。"""
    await auth_client.put("/api/settings", json={"values": {"scim_enabled": True}})
    async with app.state.sessionmaker() as session:
        entry = (
            await session.scalars(select(AuditLog).where(AuditLog.action == "settings.update"))
        ).first()
    assert entry is not None
    assert entry.target_type == "app_settings"
    assert "scim_enabled" in (entry.detail or "")


async def test_put_reset_restores_default(auth_client):
    """reset で上書きを削除すると env デフォルトへ戻る。"""
    await auth_client.put("/api/settings", json={"values": {"vad_min_rms": 999}})
    res = await auth_client.put("/api/settings", json={"reset": ["vad_min_rms"]})
    assert res.status_code == 200
    body = res.json()
    assert body["values"]["vad_min_rms"] == 200  # env デフォルト
    assert "vad_min_rms" not in body["overridden"]


async def test_put_outbound_policy_regenerates_fs_config(auth_client, app):
    """国際発信 allowlist の変更で FreeSWITCH dialplan が再生成される。

    国際発信ゲートは outbound_trunk がある場合のみ展開されるため、トランクを 1 件
    用意してから設定を変更し、allow 拡張が dialplan に現れることを確認する。
    """
    from millicall.models import Trunk

    async with app.state.sessionmaker() as session:
        session.add(
            Trunk(
                name="hgw",
                display_name="HGW",
                host="192.168.1.1",
                username="0312345678",
                password="pw",
                caller_id="0398765432",
            )
        )
        await session.commit()

    res = await auth_client.put(
        "/api/settings", json={"values": {"outbound_international_allow": "01033"}}
    )
    assert res.status_code == 200
    dialplan = app.state.settings.fs_config_dir / "dialplan" / "default.xml"
    assert dialplan.exists()
    assert "outbound_intl_allow_01033" in dialplan.read_text(encoding="utf-8")
