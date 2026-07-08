"""resync_phone の資格情報が Settings 由来（引数）で組まれることの回帰テスト。

セキュリティレビュー指摘: 以前は ``admin:adminpass`` をコードにハードコードしていた。
現在は呼び出し元が Settings の値を渡す。ここではその値が Basic 認証ヘッダに反映され、
コード側に定数が残っていないことを検証する。

M4 SSRF ガード追加分:
  * follow_redirects=False が AsyncClient に渡されること。
  * loopback / link-local (メタデータ IP 含む) デバイス IP はリクエストを送らず False を返すこと。
  * LAN プライベート IP (RFC1918) は正常に許可されること。
"""

from __future__ import annotations

import base64

import pytest

from millicall.models import Device
from millicall.provisioning import service


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _RecordingClient:
    """httpx.AsyncClient の差し替え。最初の get の Authorization ヘッダを記録する。"""

    captured_auth: str | None = None
    captured_follow_redirects: bool | None = None
    captured_transport: object = None
    request_count: int = 0

    def __init__(self, *, transport=None, follow_redirects=None, **kwargs):
        _RecordingClient.captured_transport = transport
        _RecordingClient.captured_follow_redirects = follow_redirects

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, *, headers=None, timeout=None):
        _RecordingClient.request_count += 1
        if headers and "Authorization" in headers and _RecordingClient.captured_auth is None:
            _RecordingClient.captured_auth = headers["Authorization"]
        return _FakeResponse(200)


class _FailingClient:
    """全リクエストが失敗する httpx.AsyncClient の差し替え。"""

    def __init__(self, *, transport=None, follow_redirects=None, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, *, headers=None, timeout=None):
        raise OSError("connection refused (test)")


@pytest.mark.asyncio
async def test_resync_uses_supplied_credentials(monkeypatch):
    _RecordingClient.captured_auth = None
    _RecordingClient.captured_follow_redirects = None
    _RecordingClient.captured_transport = None
    _RecordingClient.request_count = 0
    monkeypatch.setattr(service.httpx, "AsyncClient", _RecordingClient)

    device = Device(mac_address="AA:BB:CC:DD:EE:FF", ip_address="172.20.1.50", model="panasonic")
    ok = await service.resync_phone(device, admin_username="siteuser", admin_password="s3cret")

    assert ok is True
    expected = "Basic " + base64.b64encode(b"siteuser:s3cret").decode()
    assert _RecordingClient.captured_auth == expected


@pytest.mark.asyncio
async def test_resync_no_ip_returns_false():
    device = Device(mac_address="AA:BB:CC:DD:EE:FF", ip_address=None, model="panasonic")
    assert await service.resync_phone(device, admin_username="a", admin_password="b") is False


def test_no_hardcoded_admin_credential_literal():
    """service.py のソースに旧ハードコード資格情報が残っていないこと。"""
    import inspect

    src = inspect.getsource(service)
    assert "adminpass" not in src


@pytest.mark.asyncio
async def test_resync_uses_follow_redirects_false(monkeypatch):
    """AsyncClient が follow_redirects=False で構築されること（M4）。"""
    _RecordingClient.captured_auth = None
    _RecordingClient.captured_follow_redirects = None
    _RecordingClient.captured_transport = None
    _RecordingClient.request_count = 0
    monkeypatch.setattr(service.httpx, "AsyncClient", _RecordingClient)

    device = Device(mac_address="AA:BB:CC:DD:EE:FF", ip_address="192.168.1.50", model="yealink")
    await service.resync_phone(device, admin_username="u", admin_password="p")

    assert _RecordingClient.captured_follow_redirects is False


@pytest.mark.asyncio
async def test_resync_uses_pinned_transport(monkeypatch):
    """AsyncClient が _PinnedTransport インスタンスで構築されること（M4）。"""
    _RecordingClient.captured_auth = None
    _RecordingClient.captured_follow_redirects = None
    _RecordingClient.captured_transport = None
    _RecordingClient.request_count = 0
    monkeypatch.setattr(service.httpx, "AsyncClient", _RecordingClient)

    from millicall.net_guard import _PinnedTransport

    device = Device(mac_address="AA:BB:CC:DD:EE:FF", ip_address="10.0.0.100", model="panasonic")
    await service.resync_phone(device, admin_username="u", admin_password="p")

    assert isinstance(_RecordingClient.captured_transport, _PinnedTransport)


@pytest.mark.asyncio
async def test_resync_blocks_loopback(monkeypatch):
    """ループバック IP (127.0.0.1) はリクエストを送らず False を返すこと（M4 SSRF）。"""
    request_sent = []

    class _SpyClient:
        def __init__(self, *, transport=None, follow_redirects=None, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, *, headers=None, timeout=None):
            request_sent.append(url)
            return _FakeResponse(200)

    monkeypatch.setattr(service.httpx, "AsyncClient", _SpyClient)

    device = Device(mac_address="AA:BB:CC:DD:EE:FF", ip_address="127.0.0.1", model="panasonic")
    result = await service.resync_phone(device, admin_username="u", admin_password="p")

    assert result is False
    assert len(request_sent) == 0, "ループバック IP へのリクエストは送出されてはならない"


@pytest.mark.asyncio
async def test_resync_blocks_link_local_metadata_ip(monkeypatch):
    """AWS メタデータ IP (169.254.169.254) はリクエストを送らず False を返すこと（M4 SSRF）。"""
    request_sent = []

    class _SpyClient:
        def __init__(self, *, transport=None, follow_redirects=None, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, *, headers=None, timeout=None):
            request_sent.append(url)
            return _FakeResponse(200)

    monkeypatch.setattr(service.httpx, "AsyncClient", _SpyClient)

    device = Device(
        mac_address="AA:BB:CC:DD:EE:FF", ip_address="169.254.169.254", model="panasonic"
    )
    result = await service.resync_phone(device, admin_username="u", admin_password="p")

    assert result is False
    assert len(request_sent) == 0, "メタデータ IP へのリクエストは送出されてはならない"


@pytest.mark.asyncio
async def test_resync_allows_rfc1918_private_ip(monkeypatch):
    """RFC1918 プライベート IP (192.168.x.x) は許可されリクエストが送出されること（M4）。"""
    _RecordingClient.captured_auth = None
    _RecordingClient.captured_follow_redirects = None
    _RecordingClient.captured_transport = None
    _RecordingClient.request_count = 0
    monkeypatch.setattr(service.httpx, "AsyncClient", _RecordingClient)

    device = Device(mac_address="AA:BB:CC:DD:EE:FF", ip_address="192.168.10.20", model="panasonic")
    result = await service.resync_phone(device, admin_username="u", admin_password="p")

    assert result is True
    assert _RecordingClient.request_count > 0


@pytest.mark.asyncio
async def test_resync_blocks_unspecified_ip(monkeypatch):
    """未指定アドレス (0.0.0.0) はリクエストを送らず False を返すこと（M4 SSRF）。"""
    request_sent = []

    class _SpyClient:
        def __init__(self, *, transport=None, follow_redirects=None, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, *, headers=None, timeout=None):
            request_sent.append(url)
            return _FakeResponse(200)

    monkeypatch.setattr(service.httpx, "AsyncClient", _SpyClient)

    device = Device(mac_address="AA:BB:CC:DD:EE:FF", ip_address="0.0.0.0", model="panasonic")
    result = await service.resync_phone(device, admin_username="u", admin_password="p")

    assert result is False
    assert len(request_sent) == 0
