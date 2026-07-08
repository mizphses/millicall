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
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

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
        ip.is_private
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
    return (
        ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


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

    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
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
