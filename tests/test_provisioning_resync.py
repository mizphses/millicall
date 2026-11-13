"""resync_phone（電話機への再取得指示）のテスト。

セキュリティレビュー指摘: 以前は ``admin:adminpass`` をコードにハードコードしていた。
現在は呼び出し元が Settings の値を渡す。ここではその値が Basic 認証ヘッダに反映され、
コード側に定数が残っていないことを検証する。

M4 SSRF ガード:
  * follow_redirects=False が AsyncClient に渡されること。
  * loopback / link-local (メタデータ IP 含む) デバイス IP はリクエストを送らないこと。
  * LAN プライベート IP (RFC1918) は正常に許可されること。

issue #85:
  * 成功判定は 2xx のみ（3xx のログイン画面リダイレクト等は失敗）。
  * Yealink は Action URI ``servlet?key=AutoP``。
  * 結果は ResyncResult として構造化され、手段ごとの結果が残る。
  * SIP NOTIFY check-sync は端末 IP から登録中の User 宛てに、検証済みの引数でのみ送る。
"""

from __future__ import annotations

import base64

import pytest

from millicall.models import Device
from millicall.provisioning import service
from millicall.telephony.esl import ESLError


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


def _make_client(status_by_path: dict[str, int] | None = None, default_status: int = 200):
    """httpx.AsyncClient の差し替えクラスを作る。送信した (url, headers) を記録する。"""

    class _Client:
        requests: list[tuple[str, dict | None]] = []
        captured_follow_redirects: bool | None = None
        captured_transport: object = None

        def __init__(self, *, transport=None, follow_redirects=None, **kwargs):
            _Client.captured_transport = transport
            _Client.captured_follow_redirects = follow_redirects

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, *, headers=None, timeout=None):
            _Client.requests.append((url, headers))
            for frag, code in (status_by_path or {}).items():
                if frag in url:
                    return _FakeResponse(code)
            return _FakeResponse(default_status)

    _Client.requests = []
    return _Client


class _FakeESL:
    """ESLClient の差し替え。api のコマンドを記録し、コマンドごとの応答を返す。"""

    def __init__(self, responses: dict[str, str], *, fail_connect: bool = False) -> None:
        self.responses = responses
        self.fail_connect = fail_connect
        self.commands: list[str] = []

    async def connect(self) -> None:
        if self.fail_connect:
            raise OSError("FreeSWITCH unreachable")

    async def api(self, command: str) -> str:
        self.commands.append(command)
        for prefix, resp in self.responses.items():
            if command.startswith(prefix):
                return resp
        raise ESLError("unexpected command")

    async def close(self) -> None:
        return None


def _reg_block(user: str, ip: str) -> str:
    return (
        f"Call-ID:    \tabc-{user}\n"
        f"User:       \t{user}\n"
        f'Contact:    \t"" <sip:{user}:5060>\n'
        "Agent:      \tYealink SIP-T46S 66.86.0.15\n"
        "Status:     \tRegistered(UDP)(unknown) EXP(2026-11-10 12:00:00) EXPSECS(290)\n"
        f"IP:         \t{ip}\n"
        "Port:       \t5060\n"
        "\n"
    )


def _reg_output(*blocks: str) -> str:
    return "\nRegistrations:\n" + "=" * 40 + "\n" + "".join(blocks) + "Total items returned: 1\n"


def _device(ip: str | None = "192.168.1.50", model: str | None = None) -> Device:
    return Device(mac_address="AA:BB:CC:DD:EE:FF", ip_address=ip, model=model)


# ---------------------------------------------------------------------------
# 資格情報
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resync_uses_supplied_credentials(monkeypatch):
    client_cls = _make_client()
    monkeypatch.setattr(service.httpx, "AsyncClient", client_cls)

    device = _device(ip="172.20.1.50", model="panasonic")
    result = await service.resync_phone(device, admin_username="siteuser", admin_password="s3cret")

    assert result.requested is True
    expected = "Basic " + base64.b64encode(b"siteuser:s3cret").decode()
    url, headers = client_cls.requests[0]
    assert url.endswith("/admin/resync")
    assert headers == {"Authorization": expected}


@pytest.mark.asyncio
async def test_resync_no_ip_not_requested():
    result = await service.resync_phone(
        _device(ip=None, model="panasonic"), admin_username="a", admin_password="b"
    )
    assert result.requested is False
    assert result.attempts[0].skipped is True


@pytest.mark.asyncio
async def test_resync_skips_http_when_credentials_empty(monkeypatch):
    """M2×M4 連携: phone_admin が空(既定)なら HTTP を一切発行しない。"""
    called = False

    class _NoCall:
        async def __aenter__(self):
            nonlocal called
            called = True
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(service.httpx, "AsyncClient", lambda *a, **k: _NoCall())
    device = _device(model="panasonic")
    r1 = await service.resync_phone(device, admin_username="admin", admin_password="")
    r2 = await service.resync_phone(device, admin_username="", admin_password="pw")
    assert r1.requested is False and r2.requested is False
    assert r1.attempts[-1].method == "http" and r1.attempts[-1].skipped is True
    assert called is False


def test_no_hardcoded_admin_credential_literal():
    """service.py のソースに旧ハードコード資格情報が残っていないこと。"""
    import inspect

    src = inspect.getsource(service)
    assert "adminpass" not in src


# ---------------------------------------------------------------------------
# 成功判定・ベンダー別の手段
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resync_3xx_is_failure(monkeypatch):
    """3xx（ログイン画面へのリダイレクト等）は成功扱いしない（2xx のみ成功）。"""
    client_cls = _make_client(default_status=302)
    monkeypatch.setattr(service.httpx, "AsyncClient", client_cls)

    result = await service.resync_phone(_device(), admin_username="u", admin_password="p")

    assert result.requested is False
    http = [a for a in result.attempts if not a.skipped]
    assert http and all(a.ok is False for a in http)
    assert all(a.detail == "HTTP 302" for a in http)
    # 全手段を試している（Yealink → Panasonic 2 種）
    assert len(client_cls.requests) == 3


@pytest.mark.asyncio
async def test_resync_yealink_action_uri_format(monkeypatch):
    """Yealink は資料どおり ``/servlet?key=AutoP`` を送り、成功で打ち切る。"""
    client_cls = _make_client()
    monkeypatch.setattr(service.httpx, "AsyncClient", client_cls)

    device = _device(ip="192.168.1.60")
    device.last_config_fetch_user_agent = "Yealink SIP-T46S 66.86.0.15 80:5e:0c:00:00:01"
    result = await service.resync_phone(device, admin_username="u", admin_password="p")

    assert result.requested is True
    assert [u for u, _ in client_cls.requests] == ["http://192.168.1.60/servlet?key=AutoP"]
    assert result.attempts[-1].method == "yealink_action_uri"
    assert result.attempts[-1].verified is True


@pytest.mark.asyncio
async def test_resync_panasonic_unverified_methods(monkeypatch):
    """Panasonic の HTTP 手段は未検証（verified=False）として結果に出る。CGI は認証なし。"""
    client_cls = _make_client(status_by_path={"/admin/resync": 404}, default_status=200)
    monkeypatch.setattr(service.httpx, "AsyncClient", client_cls)

    device = _device()
    device.last_config_fetch_user_agent = "Panasonic_KX-HDV130/01.133 (0080F0000001)"
    result = await service.resync_phone(device, admin_username="u", admin_password="p")

    assert result.requested is True
    assert [a.method for a in result.attempts] == [
        "panasonic_admin_resync",
        "panasonic_cgi_provision",
    ]
    assert all(a.verified is False for a in result.attempts)
    # 認証不要の CGI には資格情報を付けない
    assert client_cls.requests[1][1] is None


@pytest.mark.asyncio
async def test_resync_connection_error_is_failure(monkeypatch):
    class _Failing:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, *, headers=None, timeout=None):
            raise OSError("connection refused (test)")

    monkeypatch.setattr(service.httpx, "AsyncClient", _Failing)
    result = await service.resync_phone(_device(), admin_username="u", admin_password="p")
    assert result.requested is False
    assert all(a.detail == "接続できません" for a in result.attempts)


# ---------------------------------------------------------------------------
# SSRF ガード (M4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resync_uses_follow_redirects_false(monkeypatch):
    """AsyncClient が follow_redirects=False で構築されること（M4）。"""
    client_cls = _make_client()
    monkeypatch.setattr(service.httpx, "AsyncClient", client_cls)

    await service.resync_phone(_device(model="yealink"), admin_username="u", admin_password="p")

    assert client_cls.captured_follow_redirects is False


@pytest.mark.asyncio
async def test_resync_uses_pinned_transport(monkeypatch):
    """AsyncClient が _PinnedTransport インスタンスで構築されること（M4）。"""
    client_cls = _make_client()
    monkeypatch.setattr(service.httpx, "AsyncClient", client_cls)

    from millicall.net_guard import _PinnedTransport

    await service.resync_phone(
        _device(ip="10.0.0.100", model="panasonic"), admin_username="u", admin_password="p"
    )

    assert isinstance(client_cls.captured_transport, _PinnedTransport)


@pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "0.0.0.0"])
@pytest.mark.asyncio
async def test_resync_blocks_non_lan_ip(monkeypatch, ip):
    """ループバック / メタデータ / 未指定アドレスには HTTP も NOTIFY も送らない（M4 SSRF）。"""
    client_cls = _make_client()
    monkeypatch.setattr(service.httpx, "AsyncClient", client_cls)
    esl = _FakeESL({"sofia status": _reg_output(_reg_block("1001@10.0.0.1", ip))})

    result = await service.resync_phone(
        _device(ip=ip, model="panasonic"),
        admin_username="u",
        admin_password="p",
        esl_factory=lambda: esl,
    )

    assert result.requested is False
    assert client_cls.requests == [], "ブロック対象 IP へのリクエストは送出されてはならない"
    assert esl.commands == []


@pytest.mark.asyncio
async def test_resync_allows_rfc1918_private_ip(monkeypatch):
    """RFC1918 プライベート IP (192.168.x.x) は許可されリクエストが送出されること（M4）。"""
    client_cls = _make_client()
    monkeypatch.setattr(service.httpx, "AsyncClient", client_cls)

    result = await service.resync_phone(
        _device(ip="192.168.10.20", model="panasonic"), admin_username="u", admin_password="p"
    )

    assert result.requested is True
    assert len(client_cls.requests) > 0


# ---------------------------------------------------------------------------
# SIP NOTIFY check-sync（ESL）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_sync_sent_to_user_registered_from_device_ip(monkeypatch):
    """端末 IP から登録中の User 宛てに check_sync を送る（資格情報なしでも送る）。"""
    esl = _FakeESL(
        {
            "sofia status profile internal reg": _reg_output(
                _reg_block("2001@10.0.0.1", "10.0.0.99"),
                _reg_block("1001@10.0.0.1", "10.0.0.50"),
            ),
            "sofia profile internal check_sync": "+OK syncing all registrations matching",
        }
    )

    result = await service.resync_phone(
        _device(ip="10.0.0.50"),
        admin_username="",
        admin_password="",
        esl_factory=lambda: esl,
    )

    assert result.requested is True
    assert esl.commands[-1] == "sofia profile internal check_sync 1001@10.0.0.1"
    assert result.attempts[0].method == "sip_notify_check_sync"
    assert result.attempts[0].ok is True


@pytest.mark.asyncio
async def test_check_sync_skipped_when_not_registered():
    esl = _FakeESL({"sofia status": _reg_output(_reg_block("1001@10.0.0.1", "10.0.0.99"))})
    result = await service.resync_phone(
        _device(ip="10.0.0.50"), admin_username="", admin_password="", esl_factory=lambda: esl
    )
    notify = result.attempts[0]
    assert notify.method == "sip_notify_check_sync"
    assert notify.ok is False and notify.skipped is True
    assert not any(c.startswith("sofia profile") for c in esl.commands)


@pytest.mark.asyncio
async def test_check_sync_freeswitch_unreachable():
    esl = _FakeESL({}, fail_connect=True)
    result = await service.resync_phone(
        _device(ip="10.0.0.50"), admin_username="", admin_password="", esl_factory=lambda: esl
    )
    assert result.requested is False
    assert result.attempts[0].detail == "FreeSWITCH に接続できません"


@pytest.mark.parametrize(
    "user",
    [
        "1001@10.0.0.1;sofia profile internal restart",
        "1001 x@10.0.0.1",
        "10;01@10.0.0.1",
        "1001@bad host",
        "1001",
        "@10.0.0.1",
    ],
)
@pytest.mark.asyncio
async def test_check_sync_rejects_malformed_target(user):
    """check_sync の引数は厳格に検証し、改行・空白・記号を含む宛先では送らない（注入対策）。"""
    esl = _FakeESL(
        {
            "sofia status": _reg_output(_reg_block(user, "10.0.0.50")),
            "sofia profile internal check_sync": "+OK",
        }
    )
    result = await service.resync_phone(
        _device(ip="10.0.0.50"), admin_username="", admin_password="", esl_factory=lambda: esl
    )
    assert not any(c.startswith("sofia profile") for c in esl.commands)
    assert result.requested is False


@pytest.mark.parametrize(
    ("target", "ok"),
    [
        ("1001@10.0.0.1", True),
        ("1001@millicall.local", True),
        ("user_1.a+b-c@pbx.example.jp", True),
        ("1001@10.0.0.1 extra", False),
        ("1001@", False),
        ("1001@10.0.0.1\r\n", False),
    ],
)
def test_valid_check_sync_target(target, ok):
    assert service.valid_check_sync_target(target) is ok


# ---------------------------------------------------------------------------
# 登録状態の解析
# ---------------------------------------------------------------------------


def test_parse_sofia_registrations_and_device_match():
    out = _reg_output(
        _reg_block("1001@10.0.0.1", "10.0.0.50"),
        _reg_block("1002@10.0.0.1", "10.0.0.51"),
    )
    regs = service.parse_sofia_registrations(out)
    assert [(r.user, r.ip) for r in regs] == [
        ("1001@10.0.0.1", "10.0.0.50"),
        ("1002@10.0.0.1", "10.0.0.51"),
    ]
    dev = _device(ip="10.0.0.50")
    assert service.device_is_registered(dev, "1001", regs) is True
    # 同じ内線でも別 IP（ソフトフォン等）からの登録は「この端末の登録」とみなさない
    assert service.device_is_registered(dev, "1002", regs) is False
    assert service.device_is_registered(dev, None, regs) is False


def test_parse_sofia_registrations_empty():
    assert (
        service.parse_sofia_registrations("\nRegistrations:\n====\nTotal items returned: 0\n") == []
    )
