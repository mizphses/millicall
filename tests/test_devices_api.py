"""デバイス管理 API のテスト。

/api/devices エンドポイントのデバイス一覧・同期・クイックプロビジョニング・削除を検証する。
"""

from __future__ import annotations

from millicall.models import Device, Extension, NetworkConfig
from millicall.network.client import NetdError

# ---------------------------------------------------------------------------
# フェイク NetdClient
# ---------------------------------------------------------------------------


class FakeNetdClient:
    """テスト用の NetdClient モック。"""

    def __init__(self, leases: list[dict] | None = None, *, raise_error: bool = False) -> None:
        self._leases = leases or []
        self._raise_error = raise_error

    async def get_dhcp_leases(self) -> list[dict]:
        if self._raise_error:
            raise NetdError("テスト用の netd エラー")
        return self._leases


# ---------------------------------------------------------------------------
# ヘルパー: テストデータ挿入
# ---------------------------------------------------------------------------


async def _insert_network_config(app) -> None:
    """テスト用 NetworkConfig（id=1）を挿入する。"""
    sm = app.state.sessionmaker
    async with sm() as session:
        nc = NetworkConfig(
            id=1,
            lan_interface="eth0",
            lan_ip="10.0.0.1",
            lan_prefix=24,
            dhcp_range_start="10.0.0.10",
            dhcp_range_end="10.0.0.200",
            dhcp_lease_hours=12,
            provisioning_base_url="http://10.0.0.1:8000",
            nat_enabled=False,
            wan_interface="",
            tailscale_enabled=False,
        )
        session.add(nc)
        await session.commit()


async def _insert_device(
    app,
    *,
    mac_address: str = "AA:BB:CC:DD:EE:FF",
    ip_address: str | None = "10.0.0.100",
    provisioned: bool = False,
    extension_id: int | None = None,
) -> int:
    """テスト用 Device を挿入し、その id を返す。"""
    sm = app.state.sessionmaker
    async with sm() as session:
        device = Device(
            mac_address=mac_address,
            ip_address=ip_address,
            hostname="test-phone",
            provisioned=provisioned,
            active=True,
            extension_id=extension_id,
        )
        session.add(device)
        await session.commit()
        await session.refresh(device)
        return device.id


# ---------------------------------------------------------------------------
# 認証なしテスト
# ---------------------------------------------------------------------------


async def test_list_devices_requires_auth(client) -> None:
    """デバイス一覧は認証なしで 401 を返す。"""
    resp = await client.get("/api/devices")
    assert resp.status_code == 401


async def test_sync_devices_requires_auth(client) -> None:
    """デバイス同期は認証なしで 401 を返す。"""
    resp = await client.post("/api/devices/sync")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# デバイス一覧テスト
# ---------------------------------------------------------------------------


async def test_list_devices_empty(auth_client_with_telephony) -> None:
    """デバイスが存在しない場合は空リストを返す。"""
    resp = await auth_client_with_telephony.get("/api/devices")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_devices_not_include_provision_token(app, auth_client_with_telephony) -> None:
    """デバイス一覧レスポンスに provision_token フィールドが含まれない。"""
    await _insert_device(app)

    resp = await auth_client_with_telephony.get("/api/devices")
    assert resp.status_code == 200
    devices = resp.json()
    assert len(devices) >= 1
    for device in devices:
        assert "provision_token" not in device


async def test_list_devices_includes_extension_info(app, auth_client_with_telephony) -> None:
    """デバイス一覧に Extension の番号・表示名が含まれる。"""
    sm = app.state.sessionmaker
    async with sm() as session:
        ext = Extension(number="5001", display_name="Test User", sip_password="testpass1234")
        session.add(ext)
        await session.commit()
        await session.refresh(ext)
        ext_id = ext.id

    await _insert_device(app, extension_id=ext_id, provisioned=True)

    resp = await auth_client_with_telephony.get("/api/devices")
    assert resp.status_code == 200
    devices = resp.json()
    assert len(devices) >= 1
    provisioned = [d for d in devices if d["provisioned"]]
    assert any(d["extension_number"] == "5001" for d in provisioned)
    assert any(d["extension_display_name"] == "Test User" for d in provisioned)


# ---------------------------------------------------------------------------
# デバイス同期テスト
# ---------------------------------------------------------------------------


async def test_sync_devices_from_leases(app, auth_client_with_telephony) -> None:
    """DHCP リースからデバイスが upsert される。"""
    leases = [
        {"mac": "AA:BB:CC:DD:EE:01", "ip": "10.0.0.101", "hostname": "phone-a"},
        {"mac": "AA:BB:CC:DD:EE:02", "ip": "10.0.0.102", "hostname": "phone-b"},
    ]
    app.state.netd_client = FakeNetdClient(leases=leases)

    resp = await auth_client_with_telephony.post("/api/devices/sync")
    assert resp.status_code == 200
    devices = resp.json()
    assert len(devices) == 2
    macs = {d["mac_address"] for d in devices}
    assert "AA:BB:CC:DD:EE:01" in macs
    assert "AA:BB:CC:DD:EE:02" in macs


async def test_sync_devices_no_provision_token_in_response(app, auth_client_with_telephony) -> None:
    """同期レスポンスに provision_token が含まれない。"""
    leases = [{"mac": "BB:CC:DD:EE:FF:01", "ip": "10.0.0.50", "hostname": "phone-c"}]
    app.state.netd_client = FakeNetdClient(leases=leases)

    resp = await auth_client_with_telephony.post("/api/devices/sync")
    assert resp.status_code == 200
    for device in resp.json():
        assert "provision_token" not in device


async def test_sync_devices_upsert_existing(app, auth_client_with_telephony) -> None:
    """既存デバイスの IP/ホスト名が更新される。"""
    await _insert_device(app, mac_address="CC:DD:EE:FF:00:01", ip_address="10.0.0.201")

    leases = [{"mac": "CC:DD:EE:FF:00:01", "ip": "10.0.0.202", "hostname": "updated-phone"}]
    app.state.netd_client = FakeNetdClient(leases=leases)

    resp = await auth_client_with_telephony.post("/api/devices/sync")
    assert resp.status_code == 200
    devices = resp.json()
    updated = [d for d in devices if d["mac_address"] == "CC:DD:EE:FF:00:01"]
    assert len(updated) == 1
    assert updated[0]["ip_address"] == "10.0.0.202"


async def test_sync_devices_skips_invalid_mac(app, auth_client_with_telephony) -> None:
    """不正な MAC アドレスのリースはスキップされる。"""
    leases = [
        {"mac": "INVALID", "ip": "10.0.0.50", "hostname": "bad"},
        {"mac": "DD:EE:FF:00:01:02", "ip": "10.0.0.51", "hostname": "good"},
    ]
    app.state.netd_client = FakeNetdClient(leases=leases)

    resp = await auth_client_with_telephony.post("/api/devices/sync")
    assert resp.status_code == 200
    devices = resp.json()
    assert len(devices) == 1
    assert devices[0]["mac_address"] == "DD:EE:FF:00:01:02"


async def test_sync_devices_skips_invalid_ip(app, auth_client_with_telephony) -> None:
    """不正な IP アドレスのリースはスキップされる。"""
    leases = [
        {"mac": "EE:FF:00:01:02:03", "ip": "not-an-ip", "hostname": "bad-ip"},
    ]
    app.state.netd_client = FakeNetdClient(leases=leases)

    resp = await auth_client_with_telephony.post("/api/devices/sync")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_sync_devices_invalid_hostname_stored_as_null(
    app, auth_client_with_telephony
) -> None:
    """RFC1123 に反する hostname は NULL として保存される（行自体はスキップしない）。"""
    leases = [
        {"mac": "FF:00:11:22:33:44", "ip": "10.0.0.60", "hostname": "bad_host!name"},
    ]
    app.state.netd_client = FakeNetdClient(leases=leases)

    resp = await auth_client_with_telephony.post("/api/devices/sync")
    assert resp.status_code == 200
    devices = resp.json()
    assert len(devices) == 1
    assert devices[0]["mac_address"] == "FF:00:11:22:33:44"
    assert devices[0]["ip_address"] == "10.0.0.60"
    assert devices[0]["hostname"] is None


async def test_sync_devices_netd_error_returns_502(app, auth_client_with_telephony) -> None:
    """NetdError が発生した場合は 502 を返す。"""
    app.state.netd_client = FakeNetdClient(raise_error=True)

    resp = await auth_client_with_telephony.post("/api/devices/sync")
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# クイックプロビジョニングテスト
# ---------------------------------------------------------------------------


async def test_quick_provision_assigns_extension(app, auth_client_with_telephony) -> None:
    """クイックプロビジョニングでデバイスに内線が割り当てられ provisioned=True になる。"""
    device_id = await _insert_device(app)

    resp = await auth_client_with_telephony.post(
        f"/api/devices/{device_id}/quick-provision",
        json={"extension_number": "6001", "display_name": "Quick User"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provisioned"] is True
    assert body["extension_number"] == "6001"
    assert "provision_token" not in body


async def test_quick_provision_no_provision_token_in_response(app, auth_client_with_telephony) -> None:
    """クイックプロビジョニングレスポンスに provision_token が含まれない。"""
    device_id = await _insert_device(app, mac_address="FF:EE:DD:CC:BB:AA")

    resp = await auth_client_with_telephony.post(
        f"/api/devices/{device_id}/quick-provision",
        json={"extension_number": "6002", "display_name": "No Token User"},
    )
    assert resp.status_code == 200
    assert "provision_token" not in resp.json()


async def test_quick_provision_creates_extension_if_not_exists(app, auth_client_with_telephony) -> None:
    """存在しない内線番号を指定すると新規 Extension が作成される。"""
    device_id = await _insert_device(app, mac_address="11:22:33:44:55:01")

    resp = await auth_client_with_telephony.post(
        f"/api/devices/{device_id}/quick-provision",
        json={"extension_number": "7001", "display_name": "New Ext User"},
    )
    assert resp.status_code == 200
    assert resp.json()["extension_number"] == "7001"


async def test_quick_provision_uses_existing_extension(app, auth_client_with_telephony) -> None:
    """既存の内線番号を指定すると既存 Extension が使われる（重複作成なし）。"""
    sm = app.state.sessionmaker
    async with sm() as session:
        ext = Extension(number="8001", display_name="Existing", sip_password="existpass12345")
        session.add(ext)
        await session.commit()

    device_id = await _insert_device(app, mac_address="11:22:33:44:55:02")

    resp = await auth_client_with_telephony.post(
        f"/api/devices/{device_id}/quick-provision",
        json={"extension_number": "8001", "display_name": "Should Be Ignored"},
    )
    assert resp.status_code == 200
    assert resp.json()["extension_number"] == "8001"

    # Extension が重複していないことを確認
    sm = app.state.sessionmaker
    async with sm() as session:
        from sqlalchemy import select as sa_select
        exts = list(await session.scalars(sa_select(Extension).where(Extension.number == "8001")))
    assert len(exts) == 1


async def test_quick_provision_not_found_device(auth_client_with_telephony) -> None:
    """存在しないデバイス ID は 404 を返す。"""
    resp = await auth_client_with_telephony.post(
        "/api/devices/99999/quick-provision",
        json={"extension_number": "9001", "display_name": "Ghost"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# デバイス削除テスト
# ---------------------------------------------------------------------------


async def test_delete_device(app, auth_client_with_telephony) -> None:
    """デバイスを削除すると 204 を返し、一覧から消える。"""
    device_id = await _insert_device(app, mac_address="DE:AD:BE:EF:00:01")

    resp = await auth_client_with_telephony.delete(f"/api/devices/{device_id}")
    assert resp.status_code == 204

    # 一覧から消えていることを確認
    list_resp = await auth_client_with_telephony.get("/api/devices")
    assert all(d["id"] != device_id for d in list_resp.json())


async def test_delete_device_not_found(auth_client_with_telephony) -> None:
    """存在しないデバイスの削除は 404 を返す。"""
    resp = await auth_client_with_telephony.delete("/api/devices/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 非管理者テスト
# ---------------------------------------------------------------------------


async def test_list_devices_non_admin_forbidden(client, user_factory) -> None:
    """非管理者は /api/devices にアクセスできない（403）。"""
    username, password = await user_factory(username="normaluser", password="NormalPass1", role="viewer")
    await client.post("/api/auth/login", json={"username": username, "password": password})

    resp = await client.get("/api/devices")
    assert resp.status_code == 403
