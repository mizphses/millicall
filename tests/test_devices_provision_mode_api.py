"""デバイス管理 API の配信ゲート関連（issue #85）のテスト。

provision_mode の切替、厳格モードのトークン発行・破棄（監査ログ）、SIP 登録状態、
再取得指示（resync）の構造化結果を検証する。
"""

from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from millicall.models import AuditLog, Device, Extension, NetworkConfig
from millicall.provisioning import service
from millicall.provisioning.service import hash_provision_token

DEVICE_IP = "10.0.0.100"


async def _setup(app, *, provisioned: bool = True, with_nc: bool = True) -> int:
    async with app.state.sessionmaker() as session:
        if with_nc:
            session.add(
                NetworkConfig(
                    id=1,
                    lan_interface="eth0",
                    lan_ip="10.0.0.1",
                    lan_prefix=24,
                    dhcp_range_start="10.0.0.10",
                    dhcp_range_end="10.0.0.200",
                    dhcp_lease_hours=12,
                    provisioning_base_url="",
                    nat_enabled=False,
                    wan_interface="",
                    tailscale_enabled=False,
                )
            )
        ext_id = None
        if provisioned:
            ext = Extension(number="1001", display_name="Alice", sip_password="api_sip_pass_1234")
            session.add(ext)
            await session.flush()
            ext_id = ext.id
        device = Device(
            mac_address="AA:BB:CC:DD:EE:01",
            ip_address=DEVICE_IP,
            provisioned=provisioned,
            active=True,
            extension_id=ext_id,
        )
        session.add(device)
        await session.commit()
        return device.id


async def _get_device(app, device_id: int) -> Device:
    async with app.state.sessionmaker() as session:
        device = await session.get(Device, device_id)
        assert device is not None
        return device


async def _audits(app, action: str) -> list[AuditLog]:
    async with app.state.sessionmaker() as session:
        return list(await session.scalars(select(AuditLog).where(AuditLog.action == action)))


def _phone(app) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, client=(DEVICE_IP, 54321)), base_url="http://test"
    )


class _FakeESL:
    def __init__(self, output: str | None, check_sync: str = "+OK") -> None:
        self.output = output
        self.check_sync = check_sync
        self.commands: list[str] = []

    async def connect(self) -> None:
        if self.output is None:
            raise OSError("FreeSWITCH unreachable")

    async def api(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("sofia status profile internal reg"):
            return self.output or ""
        return self.check_sync

    async def close(self) -> None:
        return None


def _reg(user: str, ip: str) -> str:
    return f"Call-ID:    \tx-{user}\nUser:       \t{user}\nIP:         \t{ip}\n\n"


# ---------------------------------------------------------------------------
# DeviceRead
# ---------------------------------------------------------------------------


async def test_device_read_has_gate_fields(app, auth_client_with_telephony) -> None:
    await _setup(app)
    body = (await auth_client_with_telephony.get("/api/devices")).json()[0]
    assert body["provision_mode"] == "standard"
    assert body["token_issued"] is False
    assert body["last_config_fetch_at"] is None
    assert body["last_config_fetch_user_agent"] is None
    assert body["last_config_fetch_used_token"] is False
    assert "provision_token" not in body


async def test_fetch_is_visible_in_list(app, auth_client_with_telephony) -> None:
    await _setup(app)
    async with _phone(app) as c:
        resp = await c.get("/provisioning/Yealink/AABBCCDDEE01.cfg", headers={"User-Agent": "UA1"})
        assert resp.status_code == 200
    body = (await auth_client_with_telephony.get("/api/devices")).json()[0]
    assert body["last_config_fetch_at"] is not None
    assert body["last_config_fetch_user_agent"] == "UA1"


async def test_quick_provision_resets_fetch_record(app, auth_client_with_telephony) -> None:
    device_id = await _setup(app)
    async with _phone(app) as c:
        assert (await c.get("/provisioning/Yealink/AABBCCDDEE01.cfg")).status_code == 200
    resp = await auth_client_with_telephony.post(
        f"/api/devices/{device_id}/quick-provision",
        json={"extension_number": "1002", "display_name": "Bob"},
    )
    assert resp.status_code == 200
    assert resp.json()["last_config_fetch_at"] is None


# ---------------------------------------------------------------------------
# provision_mode 切替
# ---------------------------------------------------------------------------


async def test_patch_mode_to_strict_blocks_plain_url(app, auth_client_with_telephony) -> None:
    device_id = await _setup(app)
    resp = await auth_client_with_telephony.patch(
        f"/api/devices/{device_id}", json={"provision_mode": "strict"}
    )
    assert resp.status_code == 200
    assert resp.json()["provision_mode"] == "strict"
    # トークンは自動発行しない
    assert resp.json()["token_issued"] is False
    async with _phone(app) as c:
        assert (await c.get("/provisioning/Yealink/AABBCCDDEE01.cfg")).status_code == 404

    audits = await _audits(app, "device.provision_mode.update")
    assert len(audits) == 1
    detail = json.loads(audits[0].detail)
    assert detail["from"] == "standard" and detail["to"] == "strict"


async def test_patch_mode_back_to_standard_discards_token(app, auth_client_with_telephony) -> None:
    device_id = await _setup(app)
    await auth_client_with_telephony.patch(
        f"/api/devices/{device_id}", json={"provision_mode": "strict"}
    )
    token = (await auth_client_with_telephony.post(f"/api/devices/{device_id}/token")).json()[
        "token"
    ]
    resp = await auth_client_with_telephony.patch(
        f"/api/devices/{device_id}", json={"provision_mode": "standard"}
    )
    assert resp.json()["token_issued"] is False
    assert (await _get_device(app, device_id)).provision_token is None
    async with _phone(app) as c:
        # 破棄済みトークンの URL は 404、通常 URL は 200
        assert (await c.get(f"/provisioning/t/{token}/Yealink/AABBCCDDEE01.cfg")).status_code == 404
        assert (await c.get("/provisioning/Yealink/AABBCCDDEE01.cfg")).status_code == 200
    detail = json.loads((await _audits(app, "device.provision_mode.update"))[-1].detail)
    assert detail["token_discarded"] is True


async def test_patch_same_mode_no_audit(app, auth_client_with_telephony) -> None:
    device_id = await _setup(app)
    resp = await auth_client_with_telephony.patch(
        f"/api/devices/{device_id}", json={"provision_mode": "standard"}
    )
    assert resp.status_code == 200
    assert await _audits(app, "device.provision_mode.update") == []


async def test_patch_invalid_mode_422(app, auth_client_with_telephony) -> None:
    device_id = await _setup(app)
    resp = await auth_client_with_telephony.patch(
        f"/api/devices/{device_id}", json={"provision_mode": "open"}
    )
    assert resp.status_code == 422


async def test_patch_not_found(auth_client_with_telephony) -> None:
    resp = await auth_client_with_telephony.patch(
        "/api/devices/9999", json={"provision_mode": "strict"}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# トークン発行・破棄
# ---------------------------------------------------------------------------


async def test_issue_token_requires_strict(app, auth_client_with_telephony) -> None:
    device_id = await _setup(app)
    resp = await auth_client_with_telephony.post(f"/api/devices/{device_id}/token")
    assert resp.status_code == 409


async def test_issue_token_requires_network_config(app, auth_client_with_telephony) -> None:
    device_id = await _setup(app, with_nc=False)
    await auth_client_with_telephony.patch(
        f"/api/devices/{device_id}", json={"provision_mode": "strict"}
    )
    resp = await auth_client_with_telephony.post(f"/api/devices/{device_id}/token")
    assert resp.status_code == 409


async def test_issue_token_returns_urls_once_and_stores_hash(
    app, auth_client_with_telephony
) -> None:
    device_id = await _setup(app)
    await auth_client_with_telephony.patch(
        f"/api/devices/{device_id}", json={"provision_mode": "strict"}
    )
    resp = await auth_client_with_telephony.post(f"/api/devices/{device_id}/token")
    assert resp.status_code == 200
    body = resp.json()
    token = body["token"]
    root = f"http://10.0.0.1/provisioning/t/{token}"
    assert body["yealink_server_url"] == f"{root}/"
    assert body["panasonic_standard_file_url"] == f"{root}/Panasonic/Config{{MAC}}.cfg"
    assert body["panasonic_master_file_url"] == f"{root}/Panasonic/ConfigCommon.cfg"

    # DB はハッシュのみ。一覧にトークンは出ない
    assert (await _get_device(app, device_id)).provision_token == hash_provision_token(token)
    listed = (await auth_client_with_telephony.get("/api/devices")).json()[0]
    assert listed["token_issued"] is True
    assert token not in json.dumps(listed)

    # 監査ログにトークンを残さない
    audits = await _audits(app, "device.provision_token.issue")
    assert len(audits) == 1
    assert token not in (audits[0].detail or "")
    assert json.loads(audits[0].detail)["rotated"] is False

    # 発行した URL で端末固有 cfg を取得でき、取得記録はトークン使用ありになる
    async with _phone(app) as c:
        cfg = await c.get(f"/provisioning/t/{token}/Yealink/AABBCCDDEE01.cfg")
        assert cfg.status_code == 200
        assert "1001" in cfg.text
    listed = (await auth_client_with_telephony.get("/api/devices")).json()[0]
    assert listed["last_config_fetch_used_token"] is True


async def test_reissue_token_invalidates_old(app, auth_client_with_telephony) -> None:
    device_id = await _setup(app)
    await auth_client_with_telephony.patch(
        f"/api/devices/{device_id}", json={"provision_mode": "strict"}
    )
    old = (await auth_client_with_telephony.post(f"/api/devices/{device_id}/token")).json()["token"]
    async with _phone(app) as c:
        assert (await c.get(f"/provisioning/t/{old}/Yealink/AABBCCDDEE01.cfg")).status_code == 200
    new = (await auth_client_with_telephony.post(f"/api/devices/{device_id}/token")).json()["token"]
    assert new != old
    # 再発行で取得記録はリセット（新しいトークンでの取得待ち）
    listed = (await auth_client_with_telephony.get("/api/devices")).json()[0]
    assert listed["last_config_fetch_at"] is None
    async with _phone(app) as c:
        assert (await c.get(f"/provisioning/t/{old}/Yealink/AABBCCDDEE01.cfg")).status_code == 404
        assert (await c.get(f"/provisioning/t/{new}/Yealink/AABBCCDDEE01.cfg")).status_code == 200
    audits = await _audits(app, "device.provision_token.issue")
    assert json.loads(audits[-1].detail)["rotated"] is True


async def test_revoke_token(app, auth_client_with_telephony) -> None:
    device_id = await _setup(app)
    await auth_client_with_telephony.patch(
        f"/api/devices/{device_id}", json={"provision_mode": "strict"}
    )
    token = (await auth_client_with_telephony.post(f"/api/devices/{device_id}/token")).json()[
        "token"
    ]
    resp = await auth_client_with_telephony.delete(f"/api/devices/{device_id}/token")
    assert resp.status_code == 200
    assert resp.json()["token_issued"] is False
    assert resp.json()["provision_mode"] == "strict"
    async with _phone(app) as c:
        # 厳格モードのまま破棄 → どの URL でも 404（自動で通常 URL に戻さない）
        assert (await c.get(f"/provisioning/t/{token}/Yealink/AABBCCDDEE01.cfg")).status_code == 404
        assert (await c.get("/provisioning/Yealink/AABBCCDDEE01.cfg")).status_code == 404
    assert len(await _audits(app, "device.provision_token.revoke")) == 1


async def test_token_endpoints_admin_only(client, user_factory, app) -> None:
    device_id = await _setup(app)
    username, password = await user_factory(
        username="viewer1", password="ViewerPass1", role="viewer"
    )
    await client.post("/api/auth/login", json={"username": username, "password": password})
    assert (await client.post(f"/api/devices/{device_id}/token")).status_code == 403
    assert (await client.delete(f"/api/devices/{device_id}/token")).status_code == 403
    assert (
        await client.patch(f"/api/devices/{device_id}", json={"provision_mode": "strict"})
    ).status_code == 403
    assert (await client.post(f"/api/devices/{device_id}/resync")).status_code == 403


# ---------------------------------------------------------------------------
# SIP 登録状態
# ---------------------------------------------------------------------------


async def test_registrations_unavailable(app, auth_client_with_telephony) -> None:
    await _setup(app)
    app.state.esl_factory = lambda: _FakeESL(None)
    body = (await auth_client_with_telephony.get("/api/devices/registrations")).json()
    assert body == {"available": False, "registered_device_ids": []}


async def test_registrations_matches_extension_and_ip(app, auth_client_with_telephony) -> None:
    device_id = await _setup(app)
    app.state.esl_factory = lambda: _FakeESL(_reg("1001@10.0.0.1", DEVICE_IP))
    body = (await auth_client_with_telephony.get("/api/devices/registrations")).json()
    assert body == {"available": True, "registered_device_ids": [device_id]}

    # 同じ内線でも別 IP からの登録なら、この端末は登録済みとしない
    app.state.esl_factory = lambda: _FakeESL(_reg("1001@10.0.0.1", "10.0.0.77"))
    body = (await auth_client_with_telephony.get("/api/devices/registrations")).json()
    assert body == {"available": True, "registered_device_ids": []}


# ---------------------------------------------------------------------------
# 再取得指示
# ---------------------------------------------------------------------------


async def test_resync_endpoint_structured_result(app, auth_client_with_telephony) -> None:
    device_id = await _setup(app)
    esl = _FakeESL(_reg("1001@10.0.0.1", DEVICE_IP))
    app.state.esl_factory = lambda: esl
    resp = await auth_client_with_telephony.post(f"/api/devices/{device_id}/resync")
    assert resp.status_code == 200
    body = resp.json()
    assert body["requested"] is True
    methods = [a["method"] for a in body["attempts"]]
    assert methods == ["sip_notify_check_sync", "http"]
    assert body["attempts"][0]["ok"] is True
    # 資格情報未設定なので HTTP は送らない
    assert body["attempts"][1]["skipped"] is True
    assert esl.commands[-1] == "sofia profile internal check_sync 1001@10.0.0.1"


async def test_resync_endpoint_not_requested(app, auth_client_with_telephony, monkeypatch) -> None:
    """どの手段も受理されなければ 200 で requested=False（3xx も失敗扱い）。"""

    class _Redirect:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, *, headers=None, timeout=None):
            class _R:
                status_code = 302

            return _R()

    monkeypatch.setattr(service.httpx, "AsyncClient", _Redirect)
    svc = app.state.settings_service
    async with app.state.sessionmaker() as session:
        await svc.apply_update(
            session, {"phone_admin_username": "admin", "phone_admin_password": "pw-123456"}
        )
        await session.commit()
    svc.invalidate()

    device_id = await _setup(app)
    app.state.esl_factory = lambda: _FakeESL(None)
    body = (await auth_client_with_telephony.post(f"/api/devices/{device_id}/resync")).json()
    assert body["requested"] is False
    http = [a for a in body["attempts"] if a["method"] != "sip_notify_check_sync"]
    assert http and all(a["ok"] is False and a["detail"] == "HTTP 302" for a in http)


async def test_resync_endpoint_not_found(auth_client_with_telephony) -> None:
    assert (await auth_client_with_telephony.post("/api/devices/9999/resync")).status_code == 404
