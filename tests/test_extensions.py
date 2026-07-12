import pytest_asyncio


@pytest_asyncio.fixture
async def auth_client(client, user_factory):
    username, password = await user_factory(username="admin2", password="Adm1nPass")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    return client


async def test_create_requires_auth(client) -> None:
    resp = await client.post("/api/extensions", json={"number": "1001", "display_name": "A"})
    assert resp.status_code == 401


async def test_create_extension_generates_sip_password(auth_client) -> None:
    resp = await auth_client.post(
        "/api/extensions", json={"number": "1001", "display_name": "Alice"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["number"] == "1001"
    assert body["display_name"] == "Alice"
    assert body["enabled"] is True
    # M3: sip_password は API レスポンスに含まれてはならない（資格情報露出防止）
    assert "sip_password" not in body


async def test_sip_password_not_in_create_response(auth_client) -> None:
    """M3: POST /api/extensions レスポンスに sip_password が含まれないことを確認。"""
    resp = await auth_client.post(
        "/api/extensions",
        json={"number": "1002", "display_name": "Bob", "sip_password": "hacked"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "sip_password" not in body


async def test_sip_password_not_in_list_response(auth_client) -> None:
    """M3: GET /api/extensions リストレスポンスに sip_password が含まれないことを確認。"""
    await auth_client.post("/api/extensions", json={"number": "1009", "display_name": "ListTest"})
    resp = await auth_client.get("/api/extensions")
    assert resp.status_code == 200
    for ext in resp.json():
        assert "sip_password" not in ext


async def test_invalid_number_rejected(auth_client) -> None:
    resp = await auth_client.post("/api/extensions", json={"number": "12ab", "display_name": "X"})
    assert resp.status_code == 422


async def test_number_too_short_rejected(auth_client) -> None:
    resp = await auth_client.post("/api/extensions", json={"number": "1", "display_name": "X"})
    assert resp.status_code == 422


async def test_number_too_long_rejected(auth_client) -> None:
    resp = await auth_client.post(
        "/api/extensions", json={"number": "1234567", "display_name": "X"}
    )
    assert resp.status_code == 422


async def test_fullwidth_digits_rejected(auth_client) -> None:
    # Unicode fullwidth digits must not be accepted (pattern is [0-9], not \d)
    resp = await auth_client.post(
        "/api/extensions", json={"number": "１２３４", "display_name": "X"}
    )
    assert resp.status_code == 422


async def test_duplicate_number_rejected(auth_client) -> None:
    await auth_client.post("/api/extensions", json={"number": "1003", "display_name": "A"})
    dup = await auth_client.post("/api/extensions", json={"number": "1003", "display_name": "B"})
    assert dup.status_code == 409


async def test_list_and_get(auth_client) -> None:
    created = await auth_client.post(
        "/api/extensions", json={"number": "1004", "display_name": "C"}
    )
    ext_id = created.json()["id"]
    lst = await auth_client.get("/api/extensions")
    assert lst.status_code == 200
    assert any(e["number"] == "1004" for e in lst.json())
    one = await auth_client.get(f"/api/extensions/{ext_id}")
    assert one.status_code == 200
    assert one.json()["number"] == "1004"


async def test_update_extension(auth_client) -> None:
    created = await auth_client.post(
        "/api/extensions", json={"number": "1005", "display_name": "old"}
    )
    ext_id = created.json()["id"]
    upd = await auth_client.patch(
        f"/api/extensions/{ext_id}", json={"display_name": "new", "enabled": False}
    )
    assert upd.status_code == 200
    assert upd.json()["display_name"] == "new"
    assert upd.json()["enabled"] is False


async def test_delete_extension(auth_client) -> None:
    created = await auth_client.post(
        "/api/extensions", json={"number": "1006", "display_name": "D"}
    )
    ext_id = created.json()["id"]
    dele = await auth_client.delete(f"/api/extensions/{ext_id}")
    assert dele.status_code == 204
    gone = await auth_client.get(f"/api/extensions/{ext_id}")
    assert gone.status_code == 404


# --- SIP 認証情報エンドポイント（GET /api/extensions/{id}/credentials）---


async def _set_network_config(app, *, lan_ip: str, applied: bool) -> None:
    """テスト用に NetworkConfig(id=1) を設定する。"""
    from millicall.models import NetworkConfig

    sm = app.state.sessionmaker
    async with sm() as session:
        cfg = await session.get(NetworkConfig, 1)
        if cfg is None:
            cfg = NetworkConfig(id=1)
            session.add(cfg)
        cfg.lan_ip = lan_ip
        cfg.applied = applied
        await session.commit()


async def test_credentials_requires_auth(client) -> None:
    """認証情報エンドポイントは未認証で 401。"""
    resp = await client.get("/api/extensions/1/credentials")
    assert resp.status_code == 401


async def test_credentials_requires_admin(client, user_factory) -> None:
    """認証情報エンドポイントは非管理者で 403。"""
    username, password = await user_factory(username="reguser", password="User123!", role="user")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    resp = await client.get("/api/extensions/1/credentials")
    assert resp.status_code == 403


async def test_credentials_returns_values(auth_client) -> None:
    """admin で 200、内線番号・平文パスワード・SIP 情報を返す。"""
    created = await auth_client.post(
        "/api/extensions", json={"number": "1100", "display_name": "Cred"}
    )
    ext_id = created.json()["id"]
    resp = await auth_client.get(f"/api/extensions/{ext_id}/credentials")
    assert resp.status_code == 200
    body = resp.json()
    assert body["number"] == "1100"
    assert body["display_name"] == "Cred"
    assert body["transport"] == "UDP"
    assert body["sip_port"] == 5060
    # 平文パスワードが返る（自動生成 64 文字）
    assert isinstance(body["password"], str) and len(body["password"]) > 0
    assert "sip_server" in body
    assert "domain" in body


async def test_credentials_uses_settings_when_not_applied(auth_client, app) -> None:
    """子LAN 未適用時: domain=sip_domain、sip_server=sip_bind_ip or sip_domain。"""
    settings = app.state.settings
    created = await auth_client.post(
        "/api/extensions", json={"number": "1101", "display_name": "Main"}
    )
    ext_id = created.json()["id"]
    await _set_network_config(app, lan_ip="172.20.0.1", applied=False)
    resp = await auth_client.get(f"/api/extensions/{ext_id}/credentials")
    assert resp.status_code == 200
    body = resp.json()
    assert body["domain"] == settings.sip_domain
    expected_server = settings.sip_bind_ip or settings.sip_domain
    assert body["sip_server"] == expected_server


async def test_credentials_uses_lan_ip_when_applied(auth_client, app) -> None:
    """子LAN 適用時: sip_server / domain = lan_ip。"""
    created = await auth_client.post(
        "/api/extensions", json={"number": "1102", "display_name": "Child"}
    )
    ext_id = created.json()["id"]
    await _set_network_config(app, lan_ip="172.20.0.1", applied=True)
    resp = await auth_client.get(f"/api/extensions/{ext_id}/credentials")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sip_server"] == "172.20.0.1"
    assert body["domain"] == "172.20.0.1"


async def test_credentials_not_found(auth_client) -> None:
    """存在しない内線は 404。"""
    resp = await auth_client.get("/api/extensions/999999/credentials")
    assert resp.status_code == 404


async def test_credentials_records_audit_without_password(auth_client, app) -> None:
    """閲覧が監査ログに記録され、平文パスワードが detail に含まれない。"""
    from sqlalchemy import select

    from millicall.models import AuditLog

    created = await auth_client.post(
        "/api/extensions", json={"number": "1103", "display_name": "Audit"}
    )
    ext_id = created.json()["id"]
    resp = await auth_client.get(f"/api/extensions/{ext_id}/credentials")
    assert resp.status_code == 200
    password = resp.json()["password"]

    sm = app.state.sessionmaker
    async with sm() as s:
        log = await s.scalar(
            select(AuditLog)
            .where(AuditLog.action == "extension.credentials.view")
            .where(AuditLog.target_id == str(ext_id))
        )
    assert log is not None
    assert log.target_type == "extension"
    # パスワードが detail に含まれてはならない（内線番号のみ）
    assert log.detail is not None
    assert password not in log.detail
    assert "1103" in log.detail
