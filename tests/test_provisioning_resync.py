"""resync_phone の資格情報が Settings 由来（引数）で組まれることの回帰テスト。

セキュリティレビュー指摘: 以前は ``admin:adminpass`` をコードにハードコードしていた。
現在は呼び出し元が Settings の値を渡す。ここではその値が Basic 認証ヘッダに反映され、
コード側に定数が残っていないことを検証する。
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

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, *, headers=None, timeout=None):
        if headers and "Authorization" in headers and _RecordingClient.captured_auth is None:
            _RecordingClient.captured_auth = headers["Authorization"]
        return _FakeResponse(200)


@pytest.mark.asyncio
async def test_resync_uses_supplied_credentials(monkeypatch):
    _RecordingClient.captured_auth = None
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
