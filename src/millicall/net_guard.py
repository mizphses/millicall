"""共有 SSRF ガード — URL 解決・IP 分類・固定トランスポート。

このモジュールは logic.py の api_call ノード用に実装された SSRF 防御を
provisioning/service.py・ai/registry.py 等の外部 HTTP 発信箇所で再利用するために
抽出したものです。

設計判断:
  * ``logic.py`` のガードコードを **ここに移動** し、logic.py 側は本モジュールを
    re-import して後方互換を保つ（``from millicall.net_guard import ...``）。
  * 新規呼び出し元は直接 ``millicall.net_guard`` を参照する。
  * provisioning (M4): IP がリテラルで既に LAN にあるため DNS 再解決は行わず
    ``_check_device_ip`` で loopback/link-local 等を弾き、_PinnedTransport + no-redirect。
  * ai/registry (M5): LLM base_url はプライベート IP を拒否; VOICEVOX engine_url は
    LAN (RFC1918) を許可しつつ loopback/link-local/metadata を拒否 + pin + no-redirect。

イベントループ (issue #81):
  ``_resolve_and_check_ssrf*`` / ``make_pinned_transport`` は ``socket.getaddrinfo`` で
  同期的に DNS 解決するため、async 文脈から直接呼ぶと解決が終わるまでループ（音声 WS・
  ESL イベント処理）が止まる。async 文脈からは ``asyncio.to_thread`` 経由で呼ぶこと
  （workflows/handlers/logic.py・app_settings/saml_metadata.py・ai/registry.py の
  ``build_llm_async`` / ``build_tts_async`` がこの流儀）。
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

# --------------------------------------------------------------------------- #
# IP 正規化・分類
# --------------------------------------------------------------------------- #


def _normalize_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """IPv4-mapped / IPv4-compatible IPv6 を素の IPv4 に畳み込む。

    ``::ffff:127.0.0.1`` のような IPv4-mapped IPv6 は、IPv6 として見ると
    ``is_loopback`` 等のフラグが立たず判定をすり抜ける。素の IPv4 に正規化して
    から分類フラグを見ることでこのバイパスを塞ぐ。
    """
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            return mapped
        # ::a.b.c.d 形式（IPv4-compatible、廃止済みだが安全側で畳み込む）
        if int(ip) != 0 and int(ip) <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(int(ip))
    return ip


def _is_blocked_ip(ip_str: str) -> bool:
    """指定された IP 文字列が **パブリック向け HTTP でのブロック対象** かどうか返す。

    is_private（RFC1918 含む）・loopback・link_local・multicast・reserved・unspecified
    をすべてブロックする。VOICEVOX などの LAN 向けプロバイダは本関数を使わず、
    より緩い :func:`_is_blocked_ip_lan_allowed` を使うこと。
    """
    try:
        ip = _normalize_ip(ipaddress.ip_address(ip_str))
    except ValueError:
        return True  # パース不能 → 安全側でブロック
    return (
        not ip.is_global
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _is_blocked_ip_lan_allowed(ip_str: str) -> bool:
    """自ホスト/LAN 型エンジン向けに loopback + RFC1918 を許可する変種。

    VOICEVOX は既定で同梱コンテナ（localhost:50021）または LAN 上で稼働するため、
    loopback と RFC1918 プライベートアドレスは許可する。以下はブロック:
      * link-local (169.254.0.0/16, fe80::/10)  ← クラウドメタデータ 169.254.169.254 等
      * multicast
      * reserved (旧 IETF 特殊用途)
      * unspecified (0.0.0.0, ::)

    残存リスク: loopback/同 LAN 上の任意ホスト/ポートへのリクエストは防げないため、
    管理者が engine_url を設定する際に適切なホストを指定する運用責任が必要
    （engine_url は admin 設定のため defense-in-depth 位置づけ）。
    """
    try:
        ip = _normalize_ip(ipaddress.ip_address(ip_str))
    except ValueError:
        return True  # パース不能 → 安全側でブロック
    return ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


# --------------------------------------------------------------------------- #
# SSRF 解決・検証
# --------------------------------------------------------------------------- #


def _resolve_and_check_ssrf(url: str) -> str:
    """URL を解決・検証し、接続に固定すべき検証済み IP を返す。

    ホスト名を ``socket.getaddrinfo`` で一度だけ解決し、全ての解決済み IP を
    :func:`_is_blocked_ip` で検査する。いずれかが内部到達可能なら ``ValueError``。
    返した IP を実際の接続先に固定する（呼び出し側の _PinnedTransport）ことで、
    検証後に再解決される DNS リバインディング (TOCTOU) を防ぐ。
    DNS 解決失敗も ValueError として扱う。
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL からホスト名を解析できません: {url!r}")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"DNS 解決失敗 ({host!r}): {exc}") from exc

    resolved_ips = [sockaddr[0] for *_rest, sockaddr in results]
    if not resolved_ips:
        raise ValueError(f"DNS 解決結果が空です ({host!r})")

    for ip_str in resolved_ips:
        if _is_blocked_ip(ip_str):
            raise ValueError(
                f"SSRF ブロック: {host!r} が {ip_str!r} に解決され、"
                "プライベート/ループバック/リンクローカル等の内部アドレスへの"
                "アクセスは拒否されます"
            )

    return resolved_ips[0]


def _resolve_and_check_ssrf_lan_allowed(url: str) -> str:
    """LAN (RFC1918) を許可しつつ危険アドレスをブロックする SSRF 解決・検証。

    VOICEVOX など LAN 上の自ホスト型エンジン向け。ロジックは
    :func:`_resolve_and_check_ssrf` と同じだが :func:`_is_blocked_ip_lan_allowed`
    を使って RFC1918 プライベートアドレスへのアクセスを許可する。
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL からホスト名を解析できません: {url!r}")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"DNS 解決失敗 ({host!r}): {exc}") from exc

    resolved_ips = [sockaddr[0] for *_rest, sockaddr in results]
    if not resolved_ips:
        raise ValueError(f"DNS 解決結果が空です ({host!r})")

    for ip_str in resolved_ips:
        if _is_blocked_ip_lan_allowed(ip_str):
            raise ValueError(
                f"SSRF ブロック: {host!r} が {ip_str!r} に解決され、"
                "リンクローカル/マルチキャスト等の危険アドレスへのアクセスは拒否されます"
            )

    return resolved_ips[0]


def _check_device_ip(ip_str: str) -> None:
    """電話機デバイス IP を検証する（M4 用）。

    電話機は RFC1918 LAN 上にいるため、プライベート IP そのものは許可する。
    ただし、loopback / link-local / multicast / reserved / unspecified は
    電話機 IP として不正であり、SSRF 踏み台となりうるためブロックする。

    link-local (169.254.0.0/16) に EC2 メタデータ (169.254.169.254) が含まれる。
    ループバック (127.x.x.x) への認証付きリクエスト送出もブロックする。

    残存リスク: 同 LAN 上の任意ホストを対象にした SSRF は構造上防げない。
    攻撃者が ARP スプーフィング等で同 LAN IP を詐称すれば認証情報が届く可能性がある。
    これは LAN プロビジョニングプロトコルの本質的制約として文書化する。

    Raises:
        ValueError: IP がブロック対象の場合。
    """
    try:
        ip = _normalize_ip(ipaddress.ip_address(ip_str))
    except ValueError as exc:
        raise ValueError(f"無効な IP アドレス: {ip_str!r}") from exc

    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise ValueError(
            f"デバイス IP {ip_str!r} はループバック/リンクローカル/マルチキャスト/"
            "予約済み/未指定アドレスのため拒否されます"
        )


# --------------------------------------------------------------------------- #
# 固定 IP トランスポート
# --------------------------------------------------------------------------- #


class _PinnedTransport(httpx.AsyncHTTPTransport):
    """検証済み IP に接続先を固定する httpx トランスポート。

    ``_resolve_and_check_ssrf`` が返した IP に接続を固定し、httpx 側の再解決を
    封じる（TOCTOU/DNS リバインディング対策）。Host ヘッダと TLS SNI/証明書検証は
    元のホスト名を保持するため、正当な仮想ホスト/証明書もそのまま機能する。
    """

    def __init__(self, pinned_host: str, pinned_ip: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._pinned_host = pinned_host
        self._pinned_ip = pinned_ip

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != self._pinned_host:
            raise ValueError("Request host does not match validated SSRF destination")
        if request.url.host == self._pinned_host:
            request.extensions = {
                **request.extensions,
                "sni_hostname": self._pinned_host,
            }
            request.url = request.url.copy_with(host=self._pinned_ip)
        return await super().handle_async_request(request)


def make_pinned_transport(url: str, *, lan_allowed: bool = False) -> tuple[str, _PinnedTransport]:
    """URL を解決・検証し、固定トランスポートを生成して返す。

    Args:
        url: 検証する URL。
        lan_allowed: True のとき RFC1918 プライベート IP を許可する（VOICEVOX 等 LAN エンジン用）。

    Returns:
        (pinned_ip, transport) のタプル。

    Raises:
        ValueError: SSRF ブロック対象または DNS 解決失敗の場合。
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if lan_allowed:
        pinned_ip = _resolve_and_check_ssrf_lan_allowed(url)
    else:
        pinned_ip = _resolve_and_check_ssrf(url)
    return pinned_ip, _PinnedTransport(host, pinned_ip)




# --------------------------------------------------------------------------- #
# Webhook 送信先ポリシー（issue #121）
# --------------------------------------------------------------------------- #
#
# Webhook の送信先は管理者が任意に設定でき、core は network_mode: host で動くため、
# ローカル専用サービス（docker socket-proxy :2375、ESL :8021、core 自身の API 等）への
# 踏み台にならないよう次の規則で検証する（送信のたびに評価する）:
#
#   1. 常に拒否: loopback（127/8, ::1）・0.0.0.0/8・link-local（メタデータ含む）・
#      multicast・reserved・unspecified
#   2. 常に拒否: 自ホストの全インターフェースのアドレス（ifaddr で列挙。LAN・docker
#      ブリッジ・tailscale0 等）。列挙自体に失敗したら fail-closed で拒否する。
#   3. docker 既定レンジ 172.16.0.0/12 と CGNAT 100.64.0.0/10（tailnet）は、
#      管理者が webhook_allowed_cidrs で明示許可したときだけ許可する。
#   4. 非グローバルアドレス上の内部サービスポート（2375/2376/8021/80/443/core の
#      http_port/ESL ポート）は、webhook_allowed_cidrs に含まれるアドレスのときだけ許可する。
#   5. それ以外の RFC1918（192.168/16・10/8）とグローバルアドレスは許可する。

_WEBHOOK_LOCAL_TTL = 30.0
_DOCKER_NET = ipaddress.ip_network("172.16.0.0/12")
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
WEBHOOK_INTERNAL_PORTS: frozenset[int] = frozenset({80, 443, 2375, 2376, 8021})

_logger = logging.getLogger("millicall.net_guard")


class LocalAddressError(RuntimeError):
    """自ホストのアドレス列挙に失敗した（fail-closed の判断に使う）。"""


def enumerate_local_addresses() -> frozenset[str]:
    """自ホストの全インターフェースの IP アドレス（正規化済み文字列）を返す。"""
    import ifaddr

    try:
        adapters = ifaddr.get_adapters()
    except Exception as exc:  # noqa: BLE001 — 列挙失敗はすべて fail-closed
        raise LocalAddressError(str(exc)) from exc
    out: set[str] = set()
    for adapter in adapters:
        for ip in adapter.ips:
            raw = ip.ip[0] if isinstance(ip.ip, tuple) else ip.ip
            norm = _normalized_str(str(raw))
            if norm is not None:
                out.add(norm)
    return frozenset(out)


class LocalAddressCache:
    """enumerate_local_addresses の結果を短時間（既定 30 秒）キャッシュする。

    IP 変更（DHCP 更新・tailscale 接続・docker ネットワーク追加）を 30 秒以内に反映する。
    列挙に失敗した場合はキャッシュせず LocalAddressError を送出する。
    """

    def __init__(
        self,
        enumerate_fn: Callable[[], frozenset[str]] = enumerate_local_addresses,
        ttl: float = _WEBHOOK_LOCAL_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._enumerate = enumerate_fn
        self._ttl = ttl
        self._clock = clock
        self._value: frozenset[str] | None = None
        self._at = 0.0

    def get(self) -> frozenset[str]:
        now = self._clock()
        if self._value is not None and now - self._at < self._ttl:
            return self._value
        try:
            value = frozenset(self._enumerate())
        except LocalAddressError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LocalAddressError(str(exc)) from exc
        self._value, self._at = value, now
        return value


_DEFAULT_LOCAL_CACHE = LocalAddressCache()


def _normalized_str(ip_str: str) -> str | None:
    try:
        return str(_normalize_ip(ipaddress.ip_address(ip_str.split("%", 1)[0])))
    except ValueError:
        return None


def parse_allowed_cidrs(
    raw: str | list[str] | None,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """webhook_allowed_cidrs（カンマ/改行区切り）を解析する。不正な要素は ValueError。"""
    if not raw:
        return ()
    items = raw if isinstance(raw, list) else re.split(r"[,\s]+", raw)
    nets = []
    for item in items:
        item = item.strip()
        if item:
            nets.append(ipaddress.ip_network(item, strict=False))
    return tuple(nets)


@dataclass(frozen=True)
class WebhookTargetPolicy:
    """Webhook 送信先の検証ポリシー。

    allowed_cidrs: 管理者が明示許可した宛先（docker/CGNAT レンジの解除・内部ポートの解除）。
    internal_ports: 非グローバルアドレス上で拒否するポート（WEBHOOK_INTERNAL_PORTS + 設定値）。
    local_addresses: 自ホストのアドレス列挙（既定は 30 秒キャッシュ付きの ifaddr 列挙）。
    """

    allowed_cidrs: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    internal_ports: frozenset[int] = WEBHOOK_INTERNAL_PORTS
    local_addresses: Callable[[], frozenset[str]] = field(
        default=_DEFAULT_LOCAL_CACHE.get, compare=False
    )


def _is_blocked_ip_webhook(ip_str: str) -> bool:
    """規則 1（常に拒否するアドレス種別）に当たるか。"""
    try:
        raw = ipaddress.ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return True
    # ::1 は IPv4-compatible の範囲に入り _normalize_ip で 0.0.0.1 に畳まれるため、
    # 正規化前にも loopback を判定する。
    if raw.is_loopback:
        return True
    ip = _normalize_ip(raw)
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        # 0.0.0.0/8（"this network"）は多くの OS で自ホスト宛として扱われる
        or (ip.version == 4 and int(ip) >> 24 == 0)
    )


def check_webhook_ip(ip_str: str, port: int, policy: WebhookTargetPolicy) -> None:
    """解決済み IP:port が Webhook 送信先として許可されるか検証する（拒否は ValueError）。"""
    if _is_blocked_ip_webhook(ip_str):
        raise ValueError(
            f"SSRF ブロック: {ip_str} はループバック/リンクローカル等のため送信できません"
        )
    norm = _normalized_str(ip_str)
    if norm is None:
        raise ValueError(f"SSRF ブロック: 不正なアドレス {ip_str!r}")
    try:
        local = policy.local_addresses()
    except Exception as exc:  # noqa: BLE001 — 列挙失敗は fail-closed
        _logger.error("自ホストのアドレスを列挙できないため Webhook 送信を拒否します: %s", exc)
        raise ValueError(
            "SSRF ブロック: 自ホストのアドレスを確認できないため送信を拒否しました"
        ) from exc
    if norm in local:
        raise ValueError(f"SSRF ブロック: {ip_str} は自ホストのアドレスです")
    ip = ipaddress.ip_address(norm)
    allowed = any(ip in net for net in policy.allowed_cidrs if net.version == ip.version)
    if (ip in _DOCKER_NET or ip in _CGNAT_NET) and not allowed:
        raise ValueError(
            f"SSRF ブロック: {ip_str} は docker/CGNAT レンジです"
            "（送信するには Webhook 許可 CIDR に追加してください）"
        )
    if not ip.is_global and port in policy.internal_ports and not allowed:
        raise ValueError(
            f"SSRF ブロック: {ip_str}:{port} は内部サービスのポートです"
            "（送信するには Webhook 許可 CIDR に追加してください）"
        )


def resolve_webhook_target(url: str, policy: WebhookTargetPolicy | None = None) -> str:
    """Webhook URL を解決し、全解決結果を検証して接続に固定する IP を返す（拒否は ValueError）。"""
    policy = policy or WebhookTargetPolicy()
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL からホスト名を解析できません: {url!r}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"DNS 解決失敗 ({host!r}): {exc}") from exc
    resolved = [str(sockaddr[0]) for *_rest, sockaddr in results]
    if not resolved:
        raise ValueError(f"DNS 解決結果が空です ({host!r})")
    for ip_str in resolved:
        check_webhook_ip(ip_str, port, policy)
    return resolved[0]


def make_webhook_transport(
    url: str, policy: WebhookTargetPolicy | None = None
) -> tuple[str, _PinnedTransport]:
    """Webhook 用: 検証済み IP に固定したトランスポートを返す（リダイレクト追従は呼び出し側で禁止）。"""
    host = urlparse(url).hostname or ""
    pinned_ip = resolve_webhook_target(url, policy)
    return pinned_ip, _PinnedTransport(host, pinned_ip)
