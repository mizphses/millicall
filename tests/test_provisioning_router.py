"""LAN 制限・トークンゲート・known-device ゲートの結合テスト。

ASGITransport の client パラメータで request.client.host を制御する。
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from millicall.models import Device, Extension, NetworkConfig

# ---------------------------------------------------------------------------
# ヘルパー: NetworkConfig・Device・Extension をDBに挿入する
# ---------------------------------------------------------------------------


async def _insert_network_config(app, lan_ip: str = "10.0.0.1", lan_prefix: int = 24) -> None:
    """テスト用 NetworkConfig（id=1）を DB に挿入する。"""
    sm = app.state.sessionmaker
    async with sm() as session:
        nc = NetworkConfig(
            id=1,
            lan_interface="eth0",
            lan_ip=lan_ip,
            lan_prefix=lan_prefix,
            dhcp_range_start="10.0.0.10",
            dhcp_range_end="10.0.0.200",
            dhcp_lease_hours=12,
            provisioning_base_url=f"http://{lan_ip}:8000",
            nat_enabled=False,
            wan_interface="",
            tailscale_enabled=False,
        )
        session.add(nc)
        await session.commit()


async def _insert_extension(app, number: str = "1001", display_name: str = "Alice") -> int:
    """テスト用 Extension を DB に挿入し、その id を返す。"""
    sm = app.state.sessionmaker
    async with sm() as session:
        ext = Extension(
            number=number,
            display_name=display_name,
            sip_password="test_sip_pass_1234",
        )
        session.add(ext)
        await session.commit()
        await session.refresh(ext)
        return ext.id


async def _insert_device(
    app,
    *,
    mac_address: str = "AA:BB:CC:DD:EE:FF",
    extension_id: int | None = None,
    provisioned: bool = True,
    provision_token: str | None = None,
    ip_address: str | None = "10.0.0.50",
) -> int:
    """テスト用 Device を DB に挿入し、その id を返す。"""
    sm = app.state.sessionmaker
    async with sm() as session:
        device = Device(
            mac_address=mac_address,
            ip_address=ip_address,
            hostname="phone-test",
            provisioned=provisioned,
            active=True,
            extension_id=extension_id,
            provision_token=provision_token,
        )
        session.add(device)
        await session.commit()
        await session.refresh(device)
        return device.id


# ---------------------------------------------------------------------------
# クライアント IP を指定するヘルパー
# ---------------------------------------------------------------------------


def _make_client(app, client_ip: str = "10.0.0.50") -> AsyncClient:
    """指定した IP からリクエストを送るテストクライアントを返す。"""
    transport = ASGITransport(app=app, client=(client_ip, 54321))
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# LAN 制限テスト（Panasonic ConfigCommon.cfg）
# ---------------------------------------------------------------------------


async def test_lan_ip_allowed(app) -> None:
    """LAN 内 IP からのリクエストは 200 を返す。"""
    await _insert_network_config(app, lan_ip="10.0.0.1", lan_prefix=24)

    async with _make_client(app, client_ip="10.0.0.50") as c:
        resp = await c.get("/provisioning/Panasonic/ConfigCommon.cfg")

    assert resp.status_code == 200


async def test_lan_ip_blocked_outside(app) -> None:
    """LAN 外 IP からのリクエストは 404 を返す（エンドポイント存在を明かさない）。"""
    await _insert_network_config(app, lan_ip="10.0.0.1", lan_prefix=24)

    async with _make_client(app, client_ip="192.168.1.100") as c:
        resp = await c.get("/provisioning/Panasonic/ConfigCommon.cfg")

    assert resp.status_code == 404


async def test_no_network_config_returns_404(app) -> None:
    """NetworkConfig が存在しない場合は 404 を返す。"""
    # NetworkConfig を挿入しない状態でリクエスト
    async with _make_client(app, client_ip="10.0.0.50") as c:
        resp = await c.get("/provisioning/Panasonic/ConfigCommon.cfg")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Panasonic 共通設定エンドポイント
# ---------------------------------------------------------------------------


async def test_panasonic_common_config_content(app) -> None:
    """Panasonic 共通設定に LAN IP と プロビジョニング URL が含まれる。"""
    await _insert_network_config(app, lan_ip="10.0.0.1", lan_prefix=24)

    async with _make_client(app, client_ip="10.0.0.1") as c:
        resp = await c.get("/provisioning/Panasonic/ConfigCommon.cfg")

    assert resp.status_code == 200
    assert "10.0.0.1" in resp.text


# ---------------------------------------------------------------------------
# Panasonic デバイス固有設定エンドポイント
# ---------------------------------------------------------------------------


async def test_panasonic_device_unknown_mac(app) -> None:
    """未知の MAC アドレスは 404 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/Panasonic/ConfigAABBCCDDEEFF.cfg")

    assert resp.status_code == 404


async def test_panasonic_device_invalid_mac(app) -> None:
    """不正な MAC アドレス形式は 404 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/Panasonic/ConfigINVALIDMAC.cfg")

    assert resp.status_code == 404


async def test_panasonic_device_not_provisioned(app) -> None:
    """provisioned=False のデバイスは 404 を返す。"""
    await _insert_network_config(app)
    ext_id = await _insert_extension(app)
    await _insert_device(
        app, mac_address="AA:BB:CC:DD:EE:FF", extension_id=ext_id, provisioned=False
    )

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/Panasonic/ConfigAABBCCDDEEFF.cfg")

    assert resp.status_code == 404


async def test_panasonic_device_no_extension(app) -> None:
    """extension_id=None のデバイスは 404 を返す。"""
    await _insert_network_config(app)
    await _insert_device(app, mac_address="AA:BB:CC:DD:EE:FF", extension_id=None, provisioned=True)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/Panasonic/ConfigAABBCCDDEEFF.cfg")

    assert resp.status_code == 404


async def test_panasonic_device_with_token_missing(app) -> None:
    """provision_token 設定済みでトークンなしのリクエストは 404 を返す。"""
    await _insert_network_config(app)
    ext_id = await _insert_extension(app)
    await _insert_device(
        app,
        mac_address="AA:BB:CC:DD:EE:FF",
        extension_id=ext_id,
        provisioned=True,
        provision_token="mytoken123",
    )

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/Panasonic/ConfigAABBCCDDEEFF.cfg")

    assert resp.status_code == 404


async def test_panasonic_device_with_token_wrong(app) -> None:
    """provision_token 設定済みで間違ったトークンは 404 を返す。"""
    await _insert_network_config(app)
    ext_id = await _insert_extension(app)
    await _insert_device(
        app,
        mac_address="AA:BB:CC:DD:EE:FF",
        extension_id=ext_id,
        provisioned=True,
        provision_token="correcttoken",
    )

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/Panasonic/ConfigAABBCCDDEEFF.cfg?token=wrongtoken")

    assert resp.status_code == 404


async def test_panasonic_device_with_token_correct(app) -> None:
    """正しいトークンで 200 を返し、トークンが消費される（token=None になり以降はトークン不要）。"""
    await _insert_network_config(app)
    ext_id = await _insert_extension(app)
    await _insert_device(
        app,
        mac_address="AA:BB:CC:DD:EE:FF",
        extension_id=ext_id,
        provisioned=True,
        provision_token="validtoken123",
    )

    async with _make_client(app) as c:
        # 正しいトークンで 200
        resp1 = await c.get("/provisioning/Panasonic/ConfigAABBCCDDEEFF.cfg?token=validtoken123")
        assert resp1.status_code == 200

        # トークン消費後: provision_token=None なのでトークンなしでもアクセス可能
        resp2 = await c.get("/provisioning/Panasonic/ConfigAABBCCDDEEFF.cfg")
        assert resp2.status_code == 200

    # DB の provision_token が None になっていることを確認
    sm = app.state.sessionmaker
    async with sm() as session:
        from sqlalchemy import select
        device = await session.scalar(
            select(Device).where(Device.mac_address == "AA:BB:CC:DD:EE:FF")
        )
        assert device is not None
        assert device.provision_token is None


async def test_panasonic_device_no_token_required(app) -> None:
    """provision_token が None のデバイスはトークンなしで 200 を返す。"""
    await _insert_network_config(app)
    ext_id = await _insert_extension(app)
    await _insert_device(
        app,
        mac_address="AA:BB:CC:DD:EE:FF",
        extension_id=ext_id,
        provisioned=True,
        provision_token=None,
    )

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/Panasonic/ConfigAABBCCDDEEFF.cfg")

    assert resp.status_code == 200
    assert "1001" in resp.text  # 内線番号


async def test_panasonic_device_config_content(app) -> None:
    """Panasonic 端末固有設定に内線番号と SIP サーバーアドレスが含まれる。"""
    await _insert_network_config(app, lan_ip="10.0.0.1")
    ext_id = await _insert_extension(app, number="2001")
    await _insert_device(
        app,
        mac_address="AA:BB:CC:DD:EE:FF",
        extension_id=ext_id,
        provisioned=True,
        provision_token=None,
    )

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/Panasonic/ConfigAABBCCDDEEFF.cfg")

    assert resp.status_code == 200
    assert "2001" in resp.text
    assert "10.0.0.1" in resp.text


# ---------------------------------------------------------------------------
# Yealink エンドポイント
# ---------------------------------------------------------------------------


async def test_yealink_boot_lan_allowed(app) -> None:
    """Yealink boot ファイルは LAN 内 IP から取得できる。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/Yealink/y000000000000.boot")

    assert resp.status_code == 200
    assert "include:config" in resp.text


async def test_yealink_boot_lan_blocked(app) -> None:
    """LAN 外からの Yealink boot リクエストは 404 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app, client_ip="8.8.8.8") as c:
        resp = await c.get("/provisioning/Yealink/y000000000000.boot")

    assert resp.status_code == 404


async def test_yealink_common_config(app) -> None:
    """Yealink 共通設定は LAN 内 IP から取得できる。"""
    await _insert_network_config(app, lan_ip="10.0.0.1")

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/Yealink/common.cfg")

    assert resp.status_code == 200
    assert "10.0.0.1" in resp.text


async def test_yealink_device_config_no_token(app) -> None:
    """provision_token=None のデバイスはトークンなしで Yealink config を取得できる。"""
    await _insert_network_config(app)
    ext_id = await _insert_extension(app, number="3001")
    await _insert_device(
        app,
        mac_address="11:22:33:44:55:66",
        extension_id=ext_id,
        provisioned=True,
        provision_token=None,
    )

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/Yealink/112233445566.cfg")

    assert resp.status_code == 200
    assert "3001" in resp.text


async def test_yealink_device_token_consumed(app) -> None:
    """Yealink デバイストークンは 1 回使用後に消費される（以降はトークン不要）。"""
    await _insert_network_config(app)
    ext_id = await _insert_extension(app, number="3002")
    await _insert_device(
        app,
        mac_address="11:22:33:44:55:77",
        extension_id=ext_id,
        provisioned=True,
        provision_token="ylnktoken",
    )

    async with _make_client(app) as c:
        # 正しいトークンで 200
        resp1 = await c.get("/provisioning/Yealink/112233445577.cfg?token=ylnktoken")
        assert resp1.status_code == 200

        # トークン消費後: provision_token=None なのでトークンなしでもアクセス可能
        resp2 = await c.get("/provisioning/Yealink/112233445577.cfg")
        assert resp2.status_code == 200

    # DB の provision_token が None になっていることを確認
    sm = app.state.sessionmaker
    async with sm() as session:
        from sqlalchemy import select
        device = await session.scalar(
            select(Device).where(Device.mac_address == "11:22:33:44:55:77")
        )
        assert device is not None
        assert device.provision_token is None


# ---------------------------------------------------------------------------
# 電話帳エンドポイント
# ---------------------------------------------------------------------------


async def test_phonebook_panasonic_xml(app) -> None:
    """Panasonic 電話帳は XML として返される。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/phonebook/panasonic.xml")

    assert resp.status_code == 200
    assert "PhoneDirectory" in resp.text


async def test_phonebook_yealink_xml(app) -> None:
    """Yealink 電話帳は XML として返される。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/phonebook/yealink.xml")

    assert resp.status_code == 200
    assert "YealinkIPPhoneDirectory" in resp.text


async def test_phonebook_lan_blocked(app) -> None:
    """LAN 外からの電話帳リクエストは 404 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app, client_ip="203.0.113.1") as c:
        resp = await c.get("/provisioning/phonebook/panasonic.xml")

    assert resp.status_code == 404
