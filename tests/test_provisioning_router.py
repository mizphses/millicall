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
# option 66 直下エントリファイル（/provisioning/ 直下）
#
# 電話機は DHCP option 66 の base URL（http://<lan_ip>/provisioning/）に
# ファイル名を直接付けて取得しにくる。ベンダーサブディレクトリ配下ではなく
# /provisioning/ 直下でもエントリファイルを提供できることを検証する。
# ---------------------------------------------------------------------------


async def test_root_yealink_common_boot(app) -> None:
    """/provisioning/y000000000000.boot が既存 boot と同一内容を返す。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/y000000000000.boot")

    assert resp.status_code == 200
    # boot 内の絶対 URL include 行を含む（この URL 経由で /Yealink/ ルートに到達する）
    assert "include:config" in resp.text


async def test_root_yealink_common_boot_lan_blocked(app) -> None:
    """LAN 外からの直下 boot リクエストは 404 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app, client_ip="8.8.8.8") as c:
        resp = await c.get("/provisioning/y000000000000.boot")

    assert resp.status_code == 404


async def test_root_yealink_mac_boot(app) -> None:
    """/provisioning/{mac}.boot（正しい MAC）が 200 で共通 boot 内容を返す。

    Yealink は起動時に <MAC>.boot を先に要求する。共通 boot を返しても
    boot 内容は $MAC 展開されるため同一で良い。
    """
    await _insert_network_config(app)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/805ec0cd8a95.boot")

    assert resp.status_code == 200
    assert "include:config" in resp.text


async def test_root_yealink_mac_boot_invalid_mac(app) -> None:
    """不正な MAC 形式の .boot は 404 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/invalidmac.boot")

    assert resp.status_code == 404


async def test_root_yealink_mac_boot_lan_blocked(app) -> None:
    """LAN 外からの直下 {mac}.boot リクエストは 404 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app, client_ip="8.8.8.8") as c:
        resp = await c.get("/provisioning/805ec0cd8a95.boot")

    assert resp.status_code == 404


async def test_root_panasonic_common_config(app) -> None:
    """/provisioning/ConfigCommon.cfg が Panasonic 共通設定を返す。"""
    await _insert_network_config(app, lan_ip="10.0.0.1")

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/ConfigCommon.cfg")

    assert resp.status_code == 200
    assert "10.0.0.1" in resp.text


async def test_root_panasonic_common_config_lan_blocked(app) -> None:
    """LAN 外からの直下 ConfigCommon.cfg リクエストは 404 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app, client_ip="203.0.113.1") as c:
        resp = await c.get("/provisioning/ConfigCommon.cfg")

    assert resp.status_code == 404


async def test_root_panasonic_device_config_no_token(app) -> None:
    """provision_token=None のデバイスは直下 Config{mac}.cfg をトークンなしで取得できる。"""
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
        resp = await c.get("/provisioning/ConfigAABBCCDDEEFF.cfg")

    assert resp.status_code == 200
    assert "2001" in resp.text
    assert "10.0.0.1" in resp.text


async def test_root_panasonic_device_config_unknown_mac(app) -> None:
    """未 provisioned（未知 MAC）の直下 Config{mac}.cfg は 404 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/ConfigAABBCCDDEEFF.cfg")

    assert resp.status_code == 404


async def test_root_panasonic_device_config_token_wrong(app) -> None:
    """provision_token 設定済みで間違ったトークンの直下 Config{mac}.cfg は 404 を返す。"""
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
        resp = await c.get("/provisioning/ConfigAABBCCDDEEFF.cfg?token=wrongtoken")

    assert resp.status_code == 404


async def test_root_yealink_device_config_no_token(app) -> None:
    """堅牢性: provision_token=None のデバイスは直下 {mac}.cfg をトークンなしで取得できる。

    Yealink は boot 経由（絶対 URL include）で /Yealink/{mac}.cfg を取得するため
    直下 {mac}.cfg が無くても無害だが、堅牢性のため直下でも同一設定を提供する。
    """
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
        resp = await c.get("/provisioning/112233445566.cfg")

    assert resp.status_code == 200
    assert "3001" in resp.text


async def test_root_yealink_device_config_unknown_mac(app) -> None:
    """未 provisioned の直下 {mac}.cfg は 404 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/112233445566.cfg")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Panasonic プレプロビジョニング入口ファイル（{MODEL}.cfg）
#
# Panasonic KX-HDV は option 66 = http://<lan_ip>/provisioning/（末尾 /）のとき
# 最初に {MODEL}.cfg（例 KX-HDV130N.cfg）を入口ファイルとして GET する。
# この入口ファイルは Config{MAC}.cfg / ConfigCommon.cfg のパスを設定するだけ。
# ---------------------------------------------------------------------------


async def test_panasonic_model_file_content(app) -> None:
    """KX-HDV130N.cfg が 200 で CFG パス 2 行（Config{MAC}.cfg・ConfigCommon.cfg）を含む。"""
    await _insert_network_config(app, lan_ip="10.0.0.1")

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/KX-HDV130N.cfg")

    assert resp.status_code == 200
    assert "CFG_STANDARD_FILE_PATH=" in resp.text
    assert "CFG_MASTER_FILE_PATH=" in resp.text
    assert "Panasonic/Config{MAC}.cfg" in resp.text
    assert "Panasonic/ConfigCommon.cfg" in resp.text


async def test_panasonic_model_file_mac_literal(app) -> None:
    """{MAC} がリテラルで含まれる（Python 展開されていない）。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/KX-HDV130N.cfg")

    assert resp.status_code == 200
    assert "Config{MAC}.cfg" in resp.text


async def test_panasonic_model_file_multiple_models_n(app) -> None:
    """N サフィックスの複数モデルでも 200 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        for model in ("KX-HDV130N", "KX-HDV230N", "KX-HDV330N", "KX-HDV430N"):
            resp = await c.get(f"/provisioning/{model}.cfg")
            assert resp.status_code == 200, model
            assert "CFG_STANDARD_FILE_PATH=" in resp.text


async def test_panasonic_model_file_multiple_models_nb(app) -> None:
    """NB サフィックスの複数モデルでも 200 を返す（正規表現が NB を落とさない）。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        for model in ("KX-HDV130NB", "KX-HDV230NB", "KX-HDV330NB", "KX-HDV430NB"):
            resp = await c.get(f"/provisioning/{model}.cfg")
            assert resp.status_code == 200, model
            assert "CFG_STANDARD_FILE_PATH=" in resp.text
            assert "Config{MAC}.cfg" in resp.text


async def test_panasonic_model_file_lan_blocked(app) -> None:
    """LAN 外からの {MODEL}.cfg リクエストは 404 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app, client_ip="8.8.8.8") as c:
        resp = await c.get("/provisioning/KX-HDV130N.cfg")

    assert resp.status_code == 404


async def test_panasonic_model_file_no_token_required(app) -> None:
    """入口ファイルは SIP 認証情報を含まないためトークン不要で 200 を返す。"""
    await _insert_network_config(app)

    async with _make_client(app) as c:
        resp = await c.get("/provisioning/KX-HDV430NB.cfg")

    assert resp.status_code == 200


async def test_non_panasonic_cfg_falls_to_yealink(app) -> None:
    """非 Panasonic 名の {name}.cfg は Yealink {mac}.cfg ルートに振り分けられる。

    KX-HDV 以外は従来どおり MAC 検証を経て Yealink 端末設定として扱われ、
    provisioned=None のデバイスの MAC なら 200、未知 MAC なら 404 になる。
    """
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
        # 既知 MAC の Yealink 端末設定として取得できる
        resp = await c.get("/provisioning/112233445566.cfg")
        assert resp.status_code == 200
        assert "3001" in resp.text

        # KX-HDV でも MAC でもない名前は 404（不正 MAC 扱い）
        resp2 = await c.get("/provisioning/foo.cfg")
        assert resp2.status_code == 404


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
