"""端末固有 cfg の配信ゲート（provision_mode）と取得記録のテスト（issue #85）。

真理値表（端末固有 cfg。前提: LAN 内・provisioned・active・内線割当済み・内線 enabled）:

  | mode     | URL                | トークン状態 | 送信元 IP | 結果 |
  |----------|--------------------|--------------|-----------|------|
  | standard | 通常               | -            | 一致      | 200  |
  | standard | 通常               | -            | 不一致    | 404  |
  | standard | /t/<token>/        | 端末に無し   | 一致      | 404  |
  | strict   | 通常               | 発行済み     | 一致      | 404  |
  | strict   | /t/<token>/        | 未発行       | 一致      | 404  |
  | strict   | /t/<token>/        | 不一致       | 一致      | 404  |
  | strict   | /t/<token>/        | 一致         | 一致      | 200  |
  | strict   | /t/<token>/        | 一致         | 不一致    | 404  |
"""

from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from millicall.models import Device, Extension, NetworkConfig
from millicall.provisioning.router import (
    ProvisioningTokenRedactFilter,
    redact_provisioning_token,
)
from millicall.provisioning.service import hash_provision_token

TOKEN = "Tok3n-for-strict-mode_0123456789abcdefABCD"
OTHER = "0ther-token-for-strict_0123456789abcdefABCD"
DEVICE_IP = "10.0.0.50"
MAC = "AA:BB:CC:DD:EE:FF"
MAC_PATH = "AABBCCDDEEFF"

# 端末固有 cfg の全ルート（Panasonic / Yealink / option66 直下）
DEVICE_ROUTES = (
    f"/Panasonic/Config{MAC_PATH}.cfg",
    f"/Yealink/{MAC_PATH}.cfg",
    f"/Config{MAC_PATH}.cfg",
    f"/{MAC_PATH}.cfg",
)


async def _setup(app, *, mode: str, token: str | None) -> int:
    async with app.state.sessionmaker() as session:
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
        ext = Extension(number="1001", display_name="Alice", sip_password="gate_sip_pass_1234")
        session.add(ext)
        await session.flush()
        device = Device(
            mac_address=MAC,
            ip_address=DEVICE_IP,
            provisioned=True,
            active=True,
            extension_id=ext.id,
            provision_mode=mode,
            provision_token=hash_provision_token(token) if token is not None else None,
        )
        session.add(device)
        await session.commit()
        return device.id


def _client(app, ip: str = DEVICE_IP, *, user_agent: str | None = None) -> AsyncClient:
    headers = {"User-Agent": user_agent} if user_agent else None
    return AsyncClient(
        transport=ASGITransport(app=app, client=(ip, 54321)),
        base_url="http://test",
        headers=headers,
    )


@pytest.mark.parametrize(
    ("mode", "stored", "presented", "client_ip", "expected"),
    [
        ("standard", None, None, DEVICE_IP, 200),
        ("standard", None, None, "10.0.0.51", 404),
        ("standard", None, TOKEN, DEVICE_IP, 404),
        ("strict", TOKEN, None, DEVICE_IP, 404),
        ("strict", None, TOKEN, DEVICE_IP, 404),
        ("strict", TOKEN, OTHER, DEVICE_IP, 404),
        ("strict", TOKEN, TOKEN, DEVICE_IP, 200),
        ("strict", TOKEN, TOKEN, "10.0.0.51", 404),
    ],
)
async def test_gate_truth_table(app, mode, stored, presented, client_ip, expected) -> None:
    await _setup(app, mode=mode, token=stored)
    prefix = "/provisioning" if presented is None else f"/provisioning/t/{presented}"
    async with _client(app, client_ip) as c:
        for route in DEVICE_ROUTES:
            resp = await c.get(prefix + route)
            assert resp.status_code == expected, route
            if expected == 404:
                assert "gate_sip_pass" not in resp.text


async def test_strict_query_token_not_accepted(app) -> None:
    """トークンはクエリでは受け付けない（アクセスログに残るため）。"""
    await _setup(app, mode="strict", token=TOKEN)
    async with _client(app) as c:
        resp = await c.get(f"/provisioning/Yealink/{MAC_PATH}.cfg?token={TOKEN}")
    assert resp.status_code == 404


async def test_malformed_token_path_404(app) -> None:
    await _setup(app, mode="strict", token=TOKEN)
    async with _client(app) as c:
        assert (await c.get(f"/provisioning/t/short/Yealink/{MAC_PATH}.cfg")).status_code == 404
        assert (await c.get("/provisioning/t/short/Yealink/common.cfg")).status_code == 404


# ---------------------------------------------------------------------------
# トークン付き URL の共通ファイル
# ---------------------------------------------------------------------------


async def test_token_route_common_files_embed_token(app) -> None:
    """トークン付き URL で取得した boot / common / 入口ファイルは取得先 URL もトークン付き。"""
    await _setup(app, mode="strict", token=TOKEN)
    root = f"http://10.0.0.1/provisioning/t/{TOKEN}"
    async with _client(app) as c:
        boot = await c.get(f"/provisioning/t/{TOKEN}/y000000000000.boot")
        assert boot.status_code == 200
        assert f"include:config <{root}/Yealink/$MAC.cfg>" in boot.text

        common = await c.get(f"/provisioning/t/{TOKEN}/Yealink/common.cfg")
        assert common.status_code == 200
        assert f"static.auto_provision.server.url = {root}/Yealink" in common.text
        assert "static.auto_provision.dhcp_option.enable = 0" in common.text
        # 電話帳はトークンなしの通常 URL
        assert "http://10.0.0.1/provisioning/phonebook/yealink.xml" in common.text

        pana = await c.get(f"/provisioning/t/{TOKEN}/ConfigCommon.cfg")
        assert pana.status_code == 200
        assert f'CFG_STANDARD_FILE_PATH="{root}/Panasonic/Config{{MAC}}.cfg"' in pana.text

        model = await c.get(f"/provisioning/t/{TOKEN}/KX-HDV130N.cfg")
        assert model.status_code == 200
        assert f'CFG_MASTER_FILE_PATH="{root}/Panasonic/ConfigCommon.cfg"' in model.text


async def test_token_route_common_files_unknown_token_404(app) -> None:
    """どの厳格モード端末のトークンでもなければ共通ファイルも 404。"""
    await _setup(app, mode="strict", token=TOKEN)
    async with _client(app) as c:
        for route in ("/y000000000000.boot", "/Yealink/common.cfg", "/ConfigCommon.cfg"):
            assert (await c.get(f"/provisioning/t/{OTHER}{route}")).status_code == 404


async def test_token_route_common_files_lan_only(app) -> None:
    await _setup(app, mode="strict", token=TOKEN)
    async with _client(app, "203.0.113.5") as c:
        assert (await c.get(f"/provisioning/t/{TOKEN}/Yealink/common.cfg")).status_code == 404


async def test_plain_common_does_not_disable_dhcp_option(app) -> None:
    await _setup(app, mode="standard", token=None)
    async with _client(app) as c:
        common = await c.get("/provisioning/Yealink/common.cfg")
    assert "dhcp_option.enable" not in common.text
    assert "static.auto_provision.server.url = http://10.0.0.1/provisioning/Yealink" in common.text


# ---------------------------------------------------------------------------
# 取得記録
# ---------------------------------------------------------------------------


async def _device(app) -> Device:
    async with app.state.sessionmaker() as session:
        device = await session.scalar(select(Device).where(Device.mac_address == MAC))
        assert device is not None
        return device


async def test_fetch_recorded_standard(app) -> None:
    await _setup(app, mode="standard", token=None)
    async with _client(app, user_agent="Yealink SIP-T46S 66.86.0.15 80:5e:0c:00:00:01") as c:
        assert (await c.get(f"/provisioning/Yealink/{MAC_PATH}.cfg")).status_code == 200
    device = await _device(app)
    assert device.last_config_fetch_at is not None
    assert device.last_config_fetch_user_agent == "Yealink SIP-T46S 66.86.0.15 80:5e:0c:00:00:01"
    assert device.last_config_fetch_used_token is False


async def test_fetch_recorded_strict_with_token(app) -> None:
    await _setup(app, mode="strict", token=TOKEN)
    async with _client(app, user_agent="Panasonic_KX-HDV130/01.133") as c:
        resp = await c.get(f"/provisioning/t/{TOKEN}/Panasonic/Config{MAC_PATH}.cfg")
        assert resp.status_code == 200
    device = await _device(app)
    assert device.last_config_fetch_at is not None
    assert device.last_config_fetch_used_token is True


async def test_fetch_not_recorded_on_denied_or_common(app) -> None:
    """404 になった取得・共通ファイルの取得は記録しない（取得済みと偽らない）。"""
    await _setup(app, mode="strict", token=TOKEN)
    async with _client(app) as c:
        assert (await c.get(f"/provisioning/Yealink/{MAC_PATH}.cfg")).status_code == 404
        assert (await c.get(f"/provisioning/t/{TOKEN}/Yealink/common.cfg")).status_code == 200
    device = await _device(app)
    assert device.last_config_fetch_at is None


async def test_fetch_user_agent_sanitized(app) -> None:
    await _setup(app, mode="standard", token=None)
    async with _client(app, user_agent="X" * 400) as c:
        assert (await c.get(f"/provisioning/{MAC_PATH}.cfg")).status_code == 200
    device = await _device(app)
    assert device.last_config_fetch_user_agent == "X" * 255


# ---------------------------------------------------------------------------
# アクセスログのトークン伏字
# ---------------------------------------------------------------------------


def test_redact_provisioning_token() -> None:
    assert (
        redact_provisioning_token(f"/provisioning/t/{TOKEN}/Yealink/common.cfg")
        == "/provisioning/t/***/Yealink/common.cfg"
    )
    assert redact_provisioning_token("/provisioning/Yealink/common.cfg") == (
        "/provisioning/Yealink/common.cfg"
    )


def test_access_log_filter_installed_and_redacts() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, ProvisioningTokenRedactFilter) for f in access_logger.filters)

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.50:5060", "GET", f"/provisioning/t/{TOKEN}/y000000000000.boot", "1.1", 200),
        None,
    )
    assert ProvisioningTokenRedactFilter().filter(record) is True
    message = record.getMessage()
    assert TOKEN not in message
    assert "/provisioning/t/***/y000000000000.boot" in message
