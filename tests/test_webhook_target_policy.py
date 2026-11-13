"""Webhook 送信先ポリシー（net_guard）。

loopback・メタデータ・自ホスト・docker/CGNAT レンジ・内部サービスポートを拒否し、
RFC1918 の LAN 受信サーバーとグローバル宛ては許可する。
"""

import httpx
import pytest
from sqlalchemy import select

from millicall import net_guard
from millicall.crypto import SecretBox
from millicall.models import WebhookDelivery
from millicall.net_guard import (
    WEBHOOK_INTERNAL_PORTS,
    LocalAddressCache,
    LocalAddressError,
    WebhookTargetPolicy,
    check_webhook_ip,
    parse_allowed_cidrs,
    resolve_webhook_target,
)
from millicall.webhooks.dispatcher import WebhookDispatcher

OWN_IPS = frozenset({"192.168.1.2", "172.17.0.1", "100.101.102.103", "fe80::1"})


def _policy(allowed: str = "", own=OWN_IPS) -> WebhookTargetPolicy:
    return WebhookTargetPolicy(
        allowed_cidrs=parse_allowed_cidrs(allowed),
        internal_ports=WEBHOOK_INTERNAL_PORTS | {8022},
        internal_endpoints=(("10.9.9.9", 9100), ("172.18.0.5", 2380)),
        local_addresses=lambda: own,
    )


def _fail() -> frozenset[str]:
    raise LocalAddressError("boom")


# ----------------------------------------------------------------- 常に拒否


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:2375/containers/core/restart",
        "http://127.8.9.10:9000/",
        "http://[::1]:9000/",
        "http://[::ffff:127.0.0.1]:9000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0:9000/",
        "http://0.1.2.3:9000/",
        "http://224.0.0.1:9000/",
    ],
)
def test_rejects_loopback_metadata_and_special(url):
    with pytest.raises(ValueError):
        resolve_webhook_target(url, _policy())


def test_own_interface_ip_on_allowed_port_is_blocked_even_if_allowlisted():
    with pytest.raises(ValueError, match="自ホスト"):
        resolve_webhook_target("http://192.168.1.2:8080/hook", _policy())
    with pytest.raises(ValueError, match="自ホスト"):
        resolve_webhook_target("http://192.168.1.2:8080/hook", _policy("192.168.1.0/24"))


def test_enumeration_failure_fails_closed():
    policy = WebhookTargetPolicy(local_addresses=_fail)
    with pytest.raises(ValueError, match="確認できない"):
        resolve_webhook_target("http://192.168.1.50:8080/hook", policy)


# ----------------------------------------------------------------- 内部サービス


def test_lan_internal_port_blocked_other_ports_allowed():
    with pytest.raises(ValueError, match="内部サービス"):
        resolve_webhook_target("http://192.168.1.50:8021/", _policy())
    with pytest.raises(ValueError, match="内部サービス"):
        resolve_webhook_target("http://192.168.1.50:2375/", _policy())
    with pytest.raises(ValueError, match="内部サービス"):
        resolve_webhook_target("http://10.0.0.5:8022/", _policy())  # 設定の ESL ポート
    assert resolve_webhook_target("http://192.168.1.50:8080/hook", _policy()) == "192.168.1.50"


def test_lan_receiver_on_80_and_443_allowed_without_allowlist():
    assert resolve_webhook_target("http://192.168.1.50/hook", _policy()) == "192.168.1.50"
    assert resolve_webhook_target("https://10.0.0.5/hook", _policy()) == "10.0.0.5"
    # 自ホストの 80/443 は拒否（core 自身の API）
    with pytest.raises(ValueError, match="自ホスト"):
        resolve_webhook_target("http://192.168.1.2/hook", _policy())


def test_allowlist_never_unlocks_internal_services():
    policy = _policy("172.16.0.0/12, 192.168.1.0/24, 10.0.0.0/8")
    with pytest.raises(ValueError):
        resolve_webhook_target("http://172.17.0.1:2375/", policy)  # docker proxy（自ホスト）
    with pytest.raises(ValueError):
        resolve_webhook_target("http://172.17.0.9:2375/", policy)  # 2375 は常に拒否
    with pytest.raises(ValueError):
        resolve_webhook_target("http://192.168.1.2:8021/", policy)  # ESL（自ホスト）
    with pytest.raises(ValueError):
        resolve_webhook_target("http://192.168.1.60:8021/", policy)
    with pytest.raises(ValueError, match="内部サービスの接続先"):
        resolve_webhook_target("http://10.9.9.9:9100/", policy)  # ESL 接続先そのもの
    with pytest.raises(ValueError, match="内部サービスの接続先"):
        resolve_webhook_target("http://172.18.0.5:2380/", policy)  # docker proxy 接続先
    # 同じホストの別ポートは許可 CIDR 内なら可
    assert resolve_webhook_target("http://172.18.0.5:8080/", policy) == "172.18.0.5"


def test_internal_endpoint_hostname_is_resolved(monkeypatch):
    def fake_getaddrinfo(host, port, type=0):  # noqa: A002
        table = {"docker-proxy": "172.18.0.7", "hooks.lan": "172.18.0.7"}
        return [(2, 1, 6, "", (table[host], port))]

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_getaddrinfo)
    policy = WebhookTargetPolicy(
        allowed_cidrs=parse_allowed_cidrs("172.16.0.0/12"),
        internal_endpoints=(("docker-proxy", 2390),),
        local_addresses=lambda: frozenset(),
    )
    with pytest.raises(ValueError, match="内部サービスの接続先"):
        resolve_webhook_target("http://hooks.lan:2390/", policy)


def test_global_address_on_443_is_allowed():
    check_webhook_ip("93.184.215.14", 443, _policy())


# ----------------------------------------------------------------- docker / CGNAT


@pytest.mark.parametrize("ip", ["172.17.0.1", "172.20.1.5", "172.31.255.1", "100.64.0.9"])
def test_docker_and_cgnat_ranges_blocked_unless_allowlisted(ip):
    with pytest.raises(ValueError):
        check_webhook_ip(ip, 8080, _policy())


def test_docker_and_cgnat_allowed_when_allowlisted():
    policy = _policy("172.20.0.0/16, 100.64.0.0/10")
    check_webhook_ip("172.20.1.5", 8080, policy)
    check_webhook_ip("100.64.0.9", 8080, policy)
    # docker ブリッジのゲートウェイ（自ホスト）は許可 CIDR でも拒否
    with pytest.raises(ValueError, match="自ホスト"):
        check_webhook_ip("172.17.0.1", 8080, _policy("172.16.0.0/12"))


# ----------------------------------------------------------------- DNS


def test_public_hostname_allowed(monkeypatch):
    def fake_getaddrinfo(host, port, type=0):  # noqa: A002
        assert host == "hooks.example.com"
        return [(2, 1, 6, "", ("93.184.215.14", port))]

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_getaddrinfo)
    assert resolve_webhook_target("https://hooks.example.com/x", _policy()) == "93.184.215.14"


def test_hostname_with_any_bad_resolution_is_rejected(monkeypatch):
    monkeypatch.setattr(
        net_guard.socket,
        "getaddrinfo",
        lambda host, port, type=0: [
            (2, 1, 6, "", ("93.184.215.14", port)),
            (2, 1, 6, "", ("127.0.0.1", port)),
        ],
    )
    with pytest.raises(ValueError):
        resolve_webhook_target("https://rebind.example.com/x", _policy())


# ----------------------------------------------------------------- 列挙・キャッシュ


def test_parse_allowed_cidrs():
    assert [str(n) for n in parse_allowed_cidrs("10.1.0.0/16\n100.64.0.0/10,")] == [
        "10.1.0.0/16",
        "100.64.0.0/10",
    ]
    with pytest.raises(ValueError):
        parse_allowed_cidrs("not-a-cidr")


def test_local_address_cache_ttl_and_failure():
    calls = {"n": 0}
    now = {"t": 0.0}

    def enum():
        calls["n"] += 1
        return frozenset({f"192.168.1.{calls['n']}"})

    cache = LocalAddressCache(enum, ttl=30, clock=lambda: now["t"])
    assert cache.get() == {"192.168.1.1"}
    now["t"] = 10
    assert cache.get() == {"192.168.1.1"}
    now["t"] = 31
    assert cache.get() == {"192.168.1.2"}  # IP 変更を 30 秒以内に反映

    failing = LocalAddressCache(_fail)
    with pytest.raises(LocalAddressError):
        failing.get()


def test_enumerate_local_addresses_includes_loopback():
    # 実ホストの列挙（ifaddr）。どの環境でも loopback は含まれる。
    assert "127.0.0.1" in net_guard.enumerate_local_addresses()


# ----------------------------------------------------------------- dispatcher 経由


async def test_dispatcher_policy_provider_is_evaluated_per_delivery(app):
    from tests.test_webhooks import _add_hook, _channel_create

    hook_id = await _add_hook(app, events=["call.inbound"], url="http://192.168.1.50:8021/x")
    calls = {"n": 0}

    async def provider() -> WebhookTargetPolicy:
        calls["n"] += 1
        return _policy()

    d = WebhookDispatcher(
        app.state.sessionmaker,
        SecretBox(app.state.secrets.master_key),
        retry_delays=(0.0, 0.0),
        policy_provider=provider,
    )
    await d.handle(_channel_create())
    await d.drain()
    async with app.state.sessionmaker() as s:
        rows = (await s.scalars(select(WebhookDelivery))).all()
    assert calls["n"] == 3  # 試行ごとに評価
    assert len(rows) == 3
    assert all(r.webhook_id == hook_id and r.status_code is None for r in rows)
    assert "内部サービス" in rows[0].error


async def test_redirect_to_internal_address_is_never_followed(app):
    from tests.test_webhooks import _add_hook, _channel_create

    await _add_hook(app, events=["call.inbound"], url="http://192.168.1.50:8080/x")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(307, headers={"Location": "http://127.0.0.1:2375/containers/x/kill"})

    async def factory(url: str):
        # 実際の検証を通したうえで MockTransport を使う
        resolve_webhook_target(url, _policy())
        return httpx.MockTransport(handler)

    d = WebhookDispatcher(
        app.state.sessionmaker,
        SecretBox(app.state.secrets.master_key),
        transport_factory=factory,
        retry_delays=(0.0, 0.0),
    )
    await d.handle(_channel_create())
    await d.drain()
    assert seen and all("127.0.0.1" not in u for u in seen)


# ----------------------------------------------------------------- 管理画面設定


async def test_webhook_allowed_cidrs_setting_is_validated(auth_client, app):
    bad = await auth_client.put(
        "/api/settings", json={"values": {"webhook_allowed_cidrs": "172.20.0.0/16, nope"}}
    )
    assert bad.status_code == 400
    ok = await auth_client.put(
        "/api/settings", json={"values": {"webhook_allowed_cidrs": "172.20.0.0/16\n100.64.0.0/10"}}
    )
    assert ok.status_code == 200, ok.text
    effective = await app.state.settings_service.effective()
    assert [str(n) for n in parse_allowed_cidrs(effective.webhook_allowed_cidrs)] == [
        "172.20.0.0/16",
        "100.64.0.0/10",
    ]


async def test_pinned_transport_rejects_port_mismatch():
    import httpx as _httpx

    _ip, transport = net_guard.make_webhook_transport("http://192.168.1.50:8080/x", _policy())
    async with _httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="port"):
            await client.post("http://192.168.1.50:8021/x")
