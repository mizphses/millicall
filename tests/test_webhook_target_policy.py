"""Webhook 送信先ポリシー（net_guard）: loopback・メタデータ・自ホスト・内部サービスを拒否する。"""

import pytest
from sqlalchemy import select

from millicall import net_guard
from millicall.crypto import SecretBox
from millicall.models import WebhookDelivery
from millicall.net_guard import (
    WebhookTargetPolicy,
    build_webhook_policy,
    resolve_webhook_target,
)
from millicall.webhooks.dispatcher import WebhookDispatcher


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:2375/containers/core/restart",
        "http://127.8.9.10/",
        "http://[::1]:8021/",
        "http://[::ffff:127.0.0.1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
    ],
)
def test_webhook_target_rejects_local_and_metadata(url):
    with pytest.raises(ValueError):
        resolve_webhook_target(url)


def test_webhook_target_rejects_own_host_address():
    policy = WebhookTargetPolicy(blocked_ips=frozenset({"192.168.1.2"}))
    with pytest.raises(ValueError, match="自ホスト"):
        resolve_webhook_target("http://192.168.1.2/hook", policy)
    # 自ホスト以外の LAN アドレスは許可
    assert resolve_webhook_target("http://192.168.1.50:8080/hook", policy) == "192.168.1.50"


def test_webhook_target_rejects_internal_endpoint_but_allows_other_port():
    policy = WebhookTargetPolicy(blocked_endpoints=frozenset({("172.18.0.5", 2375)}))
    with pytest.raises(ValueError, match="内部サービス"):
        resolve_webhook_target("http://172.18.0.5:2375/", policy)
    assert resolve_webhook_target("http://172.18.0.5:8080/", policy) == "172.18.0.5"


def test_webhook_target_allows_public_host(monkeypatch):
    def fake_getaddrinfo(host, port, type=0):  # noqa: A002
        assert host == "hooks.example.com"
        return [(2, 1, 6, "", ("93.184.215.14", port))]

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_getaddrinfo)
    assert resolve_webhook_target("https://hooks.example.com/x") == "93.184.215.14"


def test_webhook_target_rejects_hostname_resolving_to_loopback(monkeypatch):
    monkeypatch.setattr(
        net_guard.socket,
        "getaddrinfo",
        lambda host, port, type=0: [(2, 1, 6, "", ("127.0.0.1", port))],
    )
    with pytest.raises(ValueError):
        resolve_webhook_target("https://rebind.example.com/x")


def test_build_webhook_policy_collects_own_addresses_and_endpoints(monkeypatch):
    table = {
        "pbx-host": ["192.168.1.2"],
        "pbx-host.lan": ["192.168.1.2"],
        "docker-proxy": ["172.18.0.5"],
        "127.0.0.1": ["127.0.0.1"],
    }

    def fake_getaddrinfo(host, port, type=0):  # noqa: A002
        if host not in table:
            raise net_guard.socket.gaierror("nx")
        return [(2, 1, 6, "", (ip, port or 0)) for ip in table[host]]

    monkeypatch.setattr(net_guard.socket, "gethostname", lambda: "pbx-host")
    monkeypatch.setattr(net_guard.socket, "getfqdn", lambda: "pbx-host.lan")
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_getaddrinfo)
    policy = build_webhook_policy(
        local_ip_literals=["192.168.1.3", "millicall.local", None],
        internal_endpoints=[("docker-proxy", 2375), ("127.0.0.1", 8021)],
    )
    assert policy.blocked_ips == frozenset({"192.168.1.2", "192.168.1.3"})
    assert ("172.18.0.5", 2375) in policy.blocked_endpoints


async def test_dispatcher_rejects_own_host_via_policy(app):
    from tests.test_webhooks import _add_hook, _channel_create

    hook_id = await _add_hook(app, events=["call.inbound"], url="http://192.168.1.2/x")
    d = WebhookDispatcher(
        app.state.sessionmaker,
        SecretBox(app.state.secrets.master_key),
        retry_delays=(0.0, 0.0),
        target_policy=WebhookTargetPolicy(blocked_ips=frozenset({"192.168.1.2"})),
    )
    await d.handle(_channel_create())
    await d.drain()
    async with app.state.sessionmaker() as s:
        rows = (await s.scalars(select(WebhookDelivery))).all()
    assert len(rows) == 3
    assert all(r.webhook_id == hook_id and r.status_code is None for r in rows)
    assert "自ホスト" in rows[0].error


async def test_dispatcher_default_policy_blocks_loopback(app):
    from tests.test_webhooks import _add_hook, _channel_create

    await _add_hook(app, events=["call.inbound"], url="http://127.0.0.1:2375/x")
    d = WebhookDispatcher(
        app.state.sessionmaker, SecretBox(app.state.secrets.master_key), retry_delays=(0.0, 0.0)
    )
    await d.handle(_channel_create())
    await d.drain()
    async with app.state.sessionmaker() as s:
        rows = (await s.scalars(select(WebhookDelivery))).all()
    assert rows and all(r.status_code is None and "SSRF" in r.error for r in rows)
