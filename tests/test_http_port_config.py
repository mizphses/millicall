"""HTTP ポート設定とポート由来 URL 導出のテスト（80 化 + 任意 TLS フロント）。"""

from millicall.config import Settings, http_port_suffix


def test_http_port_suffix_omits_80():
    assert http_port_suffix(80) == ""
    assert http_port_suffix(8000) == ":8000"
    assert http_port_suffix(443) == ":443"


def test_http_port_default_is_80():
    assert Settings().http_port == 80


def test_http_bind_default_is_all_interfaces():
    """http_bind のデフォルトは 0.0.0.0（LAN 到達性維持。nftables で保護）。"""
    assert Settings().http_bind == "0.0.0.0"


def test_http_bind_can_be_overridden_to_loopback():
    """CF-Tunnel / Tailscale 専用デプロイでは 127.0.0.1 に設定できること。"""
    s = Settings(http_bind="127.0.0.1")
    assert s.http_bind == "127.0.0.1"


def test_media_ws_derived_from_http_port_when_empty():
    # 既定(空)→ http_port から導出
    assert Settings().media_ws_base_url == "ws://127.0.0.1:80"
    assert Settings(http_port=8000).media_ws_base_url == "ws://127.0.0.1:8000"


def test_media_ws_explicit_wins():
    assert Settings(media_ws_base_url="ws://custom:9").media_ws_base_url == "ws://custom:9"


def test_tailscale_serve_disabled_by_default():
    assert Settings().tailscale_serve_enabled is False


def test_phone_admin_password_default_is_empty():
    """M2: phone_admin_password のデフォルトは空文字（ハードコード済みデフォルト廃止）。"""
    assert Settings().phone_admin_password == ""


def test_phone_admin_username_default_is_empty():
    """M2: phone_admin_username のデフォルトは空文字。"""
    assert Settings().phone_admin_username == ""
