"""ロジック系ノードハンドラ — condition / set_variable / time_condition / api_call (Phase 4b Task 4).

各ハンドラは :func:`~millicall.workflows.executor.register_handler` を使って
グローバルレジストリに登録される。このモジュールをインポートするだけで登録が完了する。

設計原則:
  * **start / end / hangup** は Task 3 コア組込ハンドラ（_CORE_HANDLERS）が担当するため再登録しない。
  * **goto** はコアの builtin ナビゲーション（executor.py の ``_follow_goto``）が処理するため
    ハンドラ不要。
  * **time_condition** の時計は ``ctx.now`` (callable) を介して注入可能。
    未設定時は ``datetime.now(tz=ZoneInfo(config.timezone))`` を使う（テスト決定的化）。
  * **api_call** は httpx を使い、SSRF ガードを実行してからリクエストを送出する。
    SSRF ガード: ホスト名を DNS 解決し、プライベート/ループバック/リンクローカルIP を拒否する。
"""

from __future__ import annotations

import ipaddress
import socket
from datetime import datetime, time
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from millicall.workflows.executor import register_handler

if TYPE_CHECKING:
    from millicall.workflows.context import ChannelContext

# --------------------------------------------------------------------------- #
# SSRF ガード — ブロック対象 IP ネットワーク
# --------------------------------------------------------------------------- #

def _normalize_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> (
    ipaddress.IPv4Address | ipaddress.IPv6Address
):
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
    """指定された IP 文字列がブロック対象（内部到達可能）かどうかを返す。

    手書きの CIDR 表ではなく標準ライブラリの分類フラグを使う。これにより
    CGNAT (100.64.0.0/10)、0.0.0.0/8、ベンチマーク帯などの穴を一括で塞ぐ。
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


def _resolve_and_check_ssrf(url: str) -> str:
    """URL を解決・検証し、接続に固定すべき検証済み IP を返す。

    ホスト名を ``socket.getaddrinfo`` で一度だけ解決し、全ての解決済み IP を
    :func:`_is_blocked_ip` で検査する。いずれかが内部到達可能なら ``ValueError``。
    返した IP を実際の接続先に固定する（呼び出し側の pinned transport）ことで、
    検証後に再解決される DNS リバインディング (TOCTOU) を防ぐ。
    DNS 解決失敗も ValueError として扱う（フロー側は "error" に落ちる）。
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL からホスト名を解析できません: {url!r}")

    # URL に直接 IP リテラルが書かれている場合も同じ経路で検査する。
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

    # 全 IP が検証を通過。接続はこの検証済み IP に固定する。
    return resolved_ips[0]


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
            # SNI と証明書検証は元ホストで行い、接続先だけ検証済み IP に差し替える。
            request.extensions = {
                **request.extensions,
                "sni_hostname": self._pinned_host,
            }
            request.url = request.url.copy_with(host=self._pinned_ip)
        return await super().handle_async_request(request)


# --------------------------------------------------------------------------- #
# 曜日マッピング (weekday() 0=月曜 … 6=日曜)
# --------------------------------------------------------------------------- #

_WEEKDAY_MAP = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _local_now(timezone: str, ctx: ChannelContext) -> datetime:
    """テスト注入可能な「現在時刻取得」。

    ``ctx.now`` が callable な場合はそれを呼び出す（ナイーブ datetime も受け入れ、
    そのままローカル時刻として扱う）。それ以外は ``datetime.now(tz=ZoneInfo(timezone))``
    を使用する。
    """
    clock = getattr(ctx, "now", None)
    if callable(clock):
        result = clock()
        if result.tzinfo is None:
            # ナイーブ datetime → 指定タイムゾーンのローカル時刻として扱う
            return result
        # タイムゾーン付き → 指定タイムゾーンに変換
        return result.astimezone(ZoneInfo(timezone))
    return datetime.now(tz=ZoneInfo(timezone))


# --------------------------------------------------------------------------- #
# condition ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("condition")
async def handle_condition(node: object, ctx: ChannelContext) -> str:
    """条件分岐ノード。

    config.variable を ctx から取得し、config.operator で config.value と比較する。
    数値比較演算子（gt/lt/gte/lte）は数値化を試み、失敗した場合は文字列比較にフォールバック。
    結果は "true" または "false" を返す。
    """
    config = node.config  # type: ignore[attr-defined]
    var_value = ctx.get_var(config.variable, default="")
    cmp_value = config.value
    operator = config.operator

    # eq / neq は常に文字列比較
    if operator == "eq":
        matched = var_value == cmp_value
    elif operator == "neq":
        matched = var_value != cmp_value
    elif operator == "contains":
        matched = cmp_value in var_value
    else:
        # gt / lt / gte / lte — まず数値化を試みる
        try:
            num_var = float(var_value)
            num_cmp = float(cmp_value)
            if operator == "gt":
                matched = num_var > num_cmp
            elif operator == "lt":
                matched = num_var < num_cmp
            elif operator == "gte":
                matched = num_var >= num_cmp
            else:  # lte
                matched = num_var <= num_cmp
        except (ValueError, TypeError):
            # 文字列比較にフォールバック
            if operator == "gt":
                matched = var_value > cmp_value
            elif operator == "lt":
                matched = var_value < cmp_value
            elif operator == "gte":
                matched = var_value >= cmp_value
            else:  # lte
                matched = var_value <= cmp_value

    return "true" if matched else "false"


# --------------------------------------------------------------------------- #
# set_variable ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("set_variable")
async def handle_set_variable(node: object, ctx: ChannelContext) -> None:
    """変数設定ノード。

    config.value を ``{{var}}`` テンプレート展開したうえで config.variable に格納する。
    戻り値は None（単一出力ノード → "out" 既定遷移）。
    """
    config = node.config  # type: ignore[attr-defined]
    expanded = ctx.render(config.value)
    ctx.set_var(config.variable, expanded)
    return None


# --------------------------------------------------------------------------- #
# time_condition ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("time_condition")
async def handle_time_condition(node: object, ctx: ChannelContext) -> str:
    """時間条件ノード。

    現在時刻（ctx.now で注入可能）が config.start_time から config.end_time の
    半開区間 [start, end) 内、かつ config.days_of_week に該当する場合に "match"、
    それ以外は "no_match" を返す。
    """
    config = node.config  # type: ignore[attr-defined]
    now = _local_now(config.timezone, ctx)

    # ナイーブ datetime の場合は .time() でそのまま使用
    # タイムゾーン付きの場合は astimezone で変換済みなので同様
    current_time = now.time().replace(second=0, microsecond=0)

    # start_time / end_time を time オブジェクトに変換
    sh, sm = (int(p) for p in config.start_time.split(":"))
    eh, em = (int(p) for p in config.end_time.split(":"))
    start_t = time(sh, sm)
    end_t = time(eh, em)

    # 曜日チェック (weekday(): 0=月 … 6=日)
    current_day = _WEEKDAY_MAP[now.weekday()]

    in_time_range = start_t <= current_time < end_t
    in_day_range = current_day in config.days_of_week

    return "match" if (in_time_range and in_day_range) else "no_match"


# --------------------------------------------------------------------------- #
# api_call ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("api_call")
async def handle_api_call(node: object, ctx: ChannelContext) -> str:
    """API 呼び出しノード。

    1. URL / body / headers を ``ctx.render`` で {{var}} 展開する。
    2. SSRF ガード: ホスト名を解決し、プライベート/ループバック/リンクローカル IP を拒否する。
    3. httpx.AsyncClient でリクエストを実行する。
    4. config.result_variable にレスポンス本文、{result_variable}_status にステータスコードを格納する。
    5. HTTP 2xx → "success"、それ以外（4xx/5xx）→ "error"。
    6. 例外（SSRF ガード含む）→ "error"（フロー継続）。
    """
    config = node.config  # type: ignore[attr-defined]
    result_var = config.result_variable

    try:
        # --- テンプレート展開 ---
        url = ctx.render(config.url)
        body_str = ctx.render(config.body_template) if config.body_template else ""
        headers = {k: ctx.render(v) for k, v in config.headers.items()}

        # Content-Type ヘッダの自動付与（既に指定がない場合）
        if body_str and "Content-Type" not in headers and "content-type" not in {k.lower() for k in headers}:
            if config.content_type == "json":
                headers["Content-Type"] = "application/json"
            elif config.content_type == "form":
                headers["Content-Type"] = "application/x-www-form-urlencoded"

        # --- SSRF ガード（解決 + 検証 + 接続 IP の固定） ---
        host = urlparse(url).hostname or ""
        pinned_ip = _resolve_and_check_ssrf(url)  # ブロック対象なら ValueError

        # --- HTTP リクエスト ---
        # 検証済み IP に接続を固定し、リダイレクトは辿らない（別ホストへの
        # SSRF 迂回・再解決による TOCTOU を防ぐ）。
        content: bytes | None = body_str.encode() if body_str else None
        transport = _PinnedTransport(host, pinned_ip)
        async with httpx.AsyncClient(
            timeout=config.timeout,
            transport=transport,
            follow_redirects=False,
        ) as client:
            response = await client.request(
                config.method,
                url,
                headers=headers,
                content=content,
            )

        # --- 結果の格納 ---
        ctx.set_var(result_var, response.text)
        ctx.set_var(f"{result_var}_status", str(response.status_code))

        return "success" if response.is_success else "error"

    except Exception:  # noqa: BLE001
        # あらゆる例外（SSRF ガード ValueError / httpx 系 / その他）をフロー継続として扱う
        # result_variable は空文字のままにする（set_var 済みの場合もある）
        return "error"
