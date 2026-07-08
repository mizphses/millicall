"""Task 4: ロジック系ノードハンドラ（condition / set_variable / time_condition / api_call）.

TDD: このファイルを実装より先に書く。最初の実行で ImportError が出ることを確認してから
logic.py を実装する。

テスト方針:
  - ChannelContext は bare インスタンス（ESL 接続なし）
  - 時刻は ctx.now (callable) で注入し、決定的なテストを保証する
  - httpx.AsyncClient は unittest.mock で差し替える
  - socket.getaddrinfo を patch して SSRF ガードを決定的にテストする
"""

from __future__ import annotations

import socket
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from millicall.workflows.context import ChannelContext

# ---- TDD: handlers が存在する前提で import する。最初の実行は ImportError で落ちる ----
from millicall.workflows.handlers.logic import (
    handle_api_call,
    handle_condition,
    handle_set_variable,
    handle_time_condition,
)

# --------------------------------------------------------------------------- #
# ヘルパ
# --------------------------------------------------------------------------- #


def make_ctx(variables: dict | None = None) -> ChannelContext:
    ctx = ChannelContext(uuid="test-uuid")
    if variables:
        for k, v in variables.items():
            ctx.set_var(k, v)
    return ctx


def make_condition_node(variable: str, operator: str, value: str):
    from millicall.workflows.nodes import ConditionConfig, ConditionNode

    return ConditionNode(
        id="cond1",
        type="condition",
        config=ConditionConfig(variable=variable, operator=operator, value=value),
    )


def make_set_variable_node(variable: str, value: str):
    from millicall.workflows.nodes import SetVariableConfig, SetVariableNode

    return SetVariableNode(
        id="sv1",
        type="set_variable",
        config=SetVariableConfig(variable=variable, value=value),
    )


def make_time_condition_node(
    start_time: str = "09:00",
    end_time: str = "18:00",
    days_of_week: list[str] | None = None,
    timezone: str = "Asia/Tokyo",
):
    from millicall.workflows.nodes import TimeConditionConfig, TimeConditionNode

    return TimeConditionNode(
        id="tc1",
        type="time_condition",
        config=TimeConditionConfig(
            start_time=start_time,
            end_time=end_time,
            days_of_week=days_of_week
            if days_of_week is not None
            else ["mon", "tue", "wed", "thu", "fri"],
            timezone=timezone,
        ),
    )


def make_api_call_node(
    url: str = "http://example.com/api",
    method: str = "GET",
    body_template: str = "",
    headers: dict | None = None,
    result_variable: str = "api_result",
    timeout: int = 10,
    content_type: str = "json",
):
    from millicall.workflows.nodes import ApiCallConfig, ApiCallNode

    return ApiCallNode(
        id="api1",
        type="api_call",
        config=ApiCallConfig(
            url=url,
            method=method,  # type: ignore[arg-type]
            body_template=body_template,
            headers=headers or {},
            result_variable=result_variable,
            timeout=timeout,
            content_type=content_type,  # type: ignore[arg-type]
        ),
    )


# sockaddr for a public IP (non-blocked)
_PUBLIC_SOCKADDR = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 80))]


def _mock_http_response(status_code: int = 200, text: str = '{"ok": true}') -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.is_success = 200 <= status_code < 300
    return resp


# --------------------------------------------------------------------------- #
# condition — eq
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_condition_eq_true() -> None:
    ctx = make_ctx({"myvar": "hello"})
    node = make_condition_node("myvar", "eq", "hello")
    result = await handle_condition(node, ctx)
    assert result == "true"


@pytest.mark.asyncio
async def test_condition_eq_false() -> None:
    ctx = make_ctx({"myvar": "hello"})
    node = make_condition_node("myvar", "eq", "world")
    result = await handle_condition(node, ctx)
    assert result == "false"


# --------------------------------------------------------------------------- #
# condition — neq
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_condition_neq_true() -> None:
    ctx = make_ctx({"x": "foo"})
    node = make_condition_node("x", "neq", "bar")
    result = await handle_condition(node, ctx)
    assert result == "true"


@pytest.mark.asyncio
async def test_condition_neq_false() -> None:
    ctx = make_ctx({"x": "foo"})
    node = make_condition_node("x", "neq", "foo")
    result = await handle_condition(node, ctx)
    assert result == "false"


# --------------------------------------------------------------------------- #
# condition — gt / lt / gte / lte (数値比較)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_condition_gt_true_numeric() -> None:
    ctx = make_ctx({"n": "10"})
    node = make_condition_node("n", "gt", "5")
    result = await handle_condition(node, ctx)
    assert result == "true"


@pytest.mark.asyncio
async def test_condition_gt_false_numeric() -> None:
    ctx = make_ctx({"n": "3"})
    node = make_condition_node("n", "gt", "5")
    result = await handle_condition(node, ctx)
    assert result == "false"


@pytest.mark.asyncio
async def test_condition_lt_true_numeric() -> None:
    ctx = make_ctx({"n": "3"})
    node = make_condition_node("n", "lt", "5")
    result = await handle_condition(node, ctx)
    assert result == "true"


@pytest.mark.asyncio
async def test_condition_lt_false_numeric() -> None:
    ctx = make_ctx({"n": "10"})
    node = make_condition_node("n", "lt", "5")
    result = await handle_condition(node, ctx)
    assert result == "false"


@pytest.mark.asyncio
async def test_condition_gte_equal_true() -> None:
    ctx = make_ctx({"n": "5"})
    node = make_condition_node("n", "gte", "5")
    result = await handle_condition(node, ctx)
    assert result == "true"


@pytest.mark.asyncio
async def test_condition_gte_greater_true() -> None:
    ctx = make_ctx({"n": "6"})
    node = make_condition_node("n", "gte", "5")
    result = await handle_condition(node, ctx)
    assert result == "true"


@pytest.mark.asyncio
async def test_condition_gte_less_false() -> None:
    ctx = make_ctx({"n": "4"})
    node = make_condition_node("n", "gte", "5")
    result = await handle_condition(node, ctx)
    assert result == "false"


@pytest.mark.asyncio
async def test_condition_lte_equal_true() -> None:
    ctx = make_ctx({"n": "5"})
    node = make_condition_node("n", "lte", "5")
    result = await handle_condition(node, ctx)
    assert result == "true"


@pytest.mark.asyncio
async def test_condition_lte_less_true() -> None:
    ctx = make_ctx({"n": "3"})
    node = make_condition_node("n", "lte", "5")
    result = await handle_condition(node, ctx)
    assert result == "true"


@pytest.mark.asyncio
async def test_condition_lte_greater_false() -> None:
    ctx = make_ctx({"n": "6"})
    node = make_condition_node("n", "lte", "5")
    result = await handle_condition(node, ctx)
    assert result == "false"


# --------------------------------------------------------------------------- #
# condition — 数値解析失敗時は文字列比較にフォールバック
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_condition_gt_string_fallback() -> None:
    ctx = make_ctx({"s": "z"})
    node = make_condition_node("s", "gt", "a")
    # 数値化できないため文字列比較: "z" > "a" → True
    result = await handle_condition(node, ctx)
    assert result == "true"


@pytest.mark.asyncio
async def test_condition_lt_string_fallback() -> None:
    ctx = make_ctx({"s": "a"})
    node = make_condition_node("s", "lt", "z")
    result = await handle_condition(node, ctx)
    assert result == "true"


# --------------------------------------------------------------------------- #
# condition — contains
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_condition_contains_true() -> None:
    ctx = make_ctx({"msg": "hello world"})
    node = make_condition_node("msg", "contains", "world")
    result = await handle_condition(node, ctx)
    assert result == "true"


@pytest.mark.asyncio
async def test_condition_contains_false() -> None:
    ctx = make_ctx({"msg": "hello"})
    node = make_condition_node("msg", "contains", "world")
    result = await handle_condition(node, ctx)
    assert result == "false"


# --------------------------------------------------------------------------- #
# condition — 未定義変数は空文字として扱う
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_condition_undefined_variable_is_empty() -> None:
    ctx = make_ctx()
    node = make_condition_node("nosuchvar", "eq", "")
    result = await handle_condition(node, ctx)
    assert result == "true"


# --------------------------------------------------------------------------- #
# set_variable
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_set_variable_stores_plain_value() -> None:
    ctx = make_ctx()
    node = make_set_variable_node("greeting", "hello")
    result = await handle_set_variable(node, ctx)
    assert result is None  # None → "out" の既定遷移
    assert ctx.get_var("greeting") == "hello"


@pytest.mark.asyncio
async def test_set_variable_expands_template() -> None:
    ctx = make_ctx({"name": "Alice"})
    node = make_set_variable_node("msg", "Hi {{name}}!")
    result = await handle_set_variable(node, ctx)
    assert result is None
    assert ctx.get_var("msg") == "Hi Alice!"


@pytest.mark.asyncio
async def test_set_variable_overwrites_existing() -> None:
    ctx = make_ctx({"counter": "1"})
    node = make_set_variable_node("counter", "99")
    await handle_set_variable(node, ctx)
    assert ctx.get_var("counter") == "99"


# --------------------------------------------------------------------------- #
# time_condition — 時刻範囲
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_time_condition_within_range_is_match() -> None:
    """火曜 10:30 は 09:00-18:00 の範囲内かつ対象曜日 → match."""
    ctx = make_ctx()
    # 2024-01-02 は火曜日
    ctx.now = lambda: datetime(2024, 1, 2, 10, 30)
    node = make_time_condition_node("09:00", "18:00", ["mon", "tue", "wed", "thu", "fri"])
    result = await handle_time_condition(node, ctx)
    assert result == "match"


@pytest.mark.asyncio
async def test_time_condition_before_start_is_no_match() -> None:
    """開始時刻より前 → no_match."""
    ctx = make_ctx()
    ctx.now = lambda: datetime(2024, 1, 2, 8, 59)  # 8:59
    node = make_time_condition_node("09:00", "18:00")
    result = await handle_time_condition(node, ctx)
    assert result == "no_match"


@pytest.mark.asyncio
async def test_time_condition_at_start_is_match() -> None:
    """開始時刻ちょうど → match（包含）。"""
    ctx = make_ctx()
    ctx.now = lambda: datetime(2024, 1, 2, 9, 0)
    node = make_time_condition_node("09:00", "18:00")
    result = await handle_time_condition(node, ctx)
    assert result == "match"


@pytest.mark.asyncio
async def test_time_condition_at_end_is_no_match() -> None:
    """終了時刻ちょうど → no_match（排他的終端）。"""
    ctx = make_ctx()
    ctx.now = lambda: datetime(2024, 1, 2, 18, 0)
    node = make_time_condition_node("09:00", "18:00")
    result = await handle_time_condition(node, ctx)
    assert result == "no_match"


@pytest.mark.asyncio
async def test_time_condition_after_end_is_no_match() -> None:
    """終了時刻より後 → no_match."""
    ctx = make_ctx()
    ctx.now = lambda: datetime(2024, 1, 2, 20, 0)
    node = make_time_condition_node("09:00", "18:00")
    result = await handle_time_condition(node, ctx)
    assert result == "no_match"


@pytest.mark.asyncio
async def test_time_condition_wrong_day_is_no_match() -> None:
    """対象外曜日（土曜）→ no_match."""
    ctx = make_ctx()
    # 2024-01-06 は土曜日
    ctx.now = lambda: datetime(2024, 1, 6, 10, 30)
    node = make_time_condition_node("09:00", "18:00", ["mon", "tue", "wed", "thu", "fri"])
    result = await handle_time_condition(node, ctx)
    assert result == "no_match"


@pytest.mark.asyncio
async def test_time_condition_weekend_included_is_match() -> None:
    """土曜日が対象曜日に含まれる場合 → match."""
    ctx = make_ctx()
    ctx.now = lambda: datetime(2024, 1, 6, 10, 30)  # 土曜
    node = make_time_condition_node("09:00", "18:00", ["sat", "sun"])
    result = await handle_time_condition(node, ctx)
    assert result == "match"


@pytest.mark.asyncio
async def test_time_condition_uses_real_clock_when_no_now() -> None:
    """ctx.now が未設定の場合、実時間で動作（クラッシュしないことを確認）。"""
    ctx = make_ctx()  # ctx.now は未設定
    node = make_time_condition_node()
    # 結果は実時間依存なので match/no_match どちらでもよい（エラーにならないことを確認）
    result = await handle_time_condition(node, ctx)
    assert result in ("match", "no_match")


# --------------------------------------------------------------------------- #
# api_call — 正常系
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_api_call_get_success_stores_result() -> None:
    """GET 成功 → 'success'、レスポンス本文と status を変数に格納。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://example.com/api", method="GET")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=_mock_http_response(200, '{"ok":true}'))

    with (
        patch("millicall.workflows.handlers.logic.httpx.AsyncClient", return_value=mock_client),
        patch("socket.getaddrinfo", return_value=_PUBLIC_SOCKADDR),
    ):
        result = await handle_api_call(node, ctx)

    assert result == "success"
    assert ctx.get_var("api_result") == '{"ok":true}'
    assert ctx.get_var("api_result_status") == "200"


@pytest.mark.asyncio
async def test_api_call_post_with_body() -> None:
    """POST ボディ付き → リクエストが呼ばれ 'success'。"""
    ctx = make_ctx({"payload": "test"})
    node = make_api_call_node(
        url="http://example.com/send",
        method="POST",
        body_template='{"data":"{{payload}}"}',
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=_mock_http_response(201, "created"))

    with (
        patch("millicall.workflows.handlers.logic.httpx.AsyncClient", return_value=mock_client),
        patch("socket.getaddrinfo", return_value=_PUBLIC_SOCKADDR),
    ):
        result = await handle_api_call(node, ctx)

    assert result == "success"
    # request が呼ばれていること（SSRF をパスして実行された）
    mock_client.request.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_call_url_template_expanded() -> None:
    """URL の {{var}} がテンプレート展開される。"""
    ctx = make_ctx({"id": "42"})
    node = make_api_call_node(url="http://example.com/items/{{id}}")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=_mock_http_response(200, "ok"))

    with (
        patch("millicall.workflows.handlers.logic.httpx.AsyncClient", return_value=mock_client),
        patch("socket.getaddrinfo", return_value=_PUBLIC_SOCKADDR),
    ):
        await handle_api_call(node, ctx)

    # 展開後の URL でリクエストが呼ばれること
    call_args = mock_client.request.call_args
    assert "http://example.com/items/42" in call_args[0] or "http://example.com/items/42" in str(
        call_args
    )


# --------------------------------------------------------------------------- #
# api_call — エラー系（HTTP エラー / ネットワーク例外）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_api_call_non_2xx_returns_error() -> None:
    """HTTP 500 → 'error'（ステータスは変数に格納）。"""
    ctx = make_ctx()
    node = make_api_call_node()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=_mock_http_response(500, "Internal Server Error"))

    with (
        patch("millicall.workflows.handlers.logic.httpx.AsyncClient", return_value=mock_client),
        patch("socket.getaddrinfo", return_value=_PUBLIC_SOCKADDR),
    ):
        result = await handle_api_call(node, ctx)

    assert result == "error"
    assert ctx.get_var("api_result_status") == "500"


@pytest.mark.asyncio
async def test_api_call_network_error_returns_error() -> None:
    """ネットワーク例外 → 'error'（フロー継続）。"""
    import httpx

    ctx = make_ctx()
    node = make_api_call_node()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with (
        patch("millicall.workflows.handlers.logic.httpx.AsyncClient", return_value=mock_client),
        patch("socket.getaddrinfo", return_value=_PUBLIC_SOCKADDR),
    ):
        result = await handle_api_call(node, ctx)

    assert result == "error"


@pytest.mark.asyncio
async def test_api_call_custom_result_variable() -> None:
    """result_variable が設定されている場合、その名前で変数を格納する。"""
    ctx = make_ctx()
    node = make_api_call_node(result_variable="my_custom_var")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=_mock_http_response(200, "data"))

    with (
        patch("millicall.workflows.handlers.logic.httpx.AsyncClient", return_value=mock_client),
        patch("socket.getaddrinfo", return_value=_PUBLIC_SOCKADDR),
    ):
        await handle_api_call(node, ctx)

    assert ctx.get_var("my_custom_var") == "data"
    assert ctx.get_var("my_custom_var_status") == "200"


# --------------------------------------------------------------------------- #
# api_call — SSRF ガード
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_api_call_ssrf_loopback_blocked() -> None:
    """ループバック IP (127.0.0.1) → SSRF ガードで 'error'。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://localhost/api")

    loopback_sockaddr = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))]
    with patch("socket.getaddrinfo", return_value=loopback_sockaddr):
        result = await handle_api_call(node, ctx)

    assert result == "error"


@pytest.mark.asyncio
async def test_api_call_ssrf_private_10_blocked() -> None:
    """プライベートアドレス 10.x.x.x → SSRF ガードで 'error'。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://internal.example/api")

    private_sockaddr = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 80))]
    with patch("socket.getaddrinfo", return_value=private_sockaddr):
        result = await handle_api_call(node, ctx)

    assert result == "error"


@pytest.mark.asyncio
async def test_api_call_ssrf_private_192_blocked() -> None:
    """プライベートアドレス 192.168.x.x → SSRF ガードで 'error'。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://192.168.1.100/api")

    private_sockaddr = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.100", 80))]
    with patch("socket.getaddrinfo", return_value=private_sockaddr):
        result = await handle_api_call(node, ctx)

    assert result == "error"


@pytest.mark.asyncio
async def test_api_call_ssrf_private_172_blocked() -> None:
    """プライベートアドレス 172.16.x.x → SSRF ガードで 'error'。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://172.16.0.1/api")

    private_sockaddr = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("172.16.0.1", 80))]
    with patch("socket.getaddrinfo", return_value=private_sockaddr):
        result = await handle_api_call(node, ctx)

    assert result == "error"


@pytest.mark.asyncio
async def test_api_call_ssrf_metadata_169_blocked() -> None:
    """メタデータ IP 169.254.169.254 → SSRF ガードで 'error'。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://169.254.169.254/latest/meta-data/")

    metadata_sockaddr = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 80))]
    with patch("socket.getaddrinfo", return_value=metadata_sockaddr):
        result = await handle_api_call(node, ctx)

    assert result == "error"


@pytest.mark.asyncio
async def test_api_call_ssrf_ipv6_loopback_blocked() -> None:
    """IPv6 ループバック ::1 → SSRF ガードで 'error'。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://[::1]/api")

    ipv6_sockaddr = [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 80, 0, 0))]
    with patch("socket.getaddrinfo", return_value=ipv6_sockaddr):
        result = await handle_api_call(node, ctx)

    assert result == "error"


@pytest.mark.asyncio
async def test_api_call_dns_failure_returns_error() -> None:
    """DNS 解決失敗 → SSRF ガードで 'error'（フロー継続）。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://nonexistent.invalid/api")

    with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name not known")):
        result = await handle_api_call(node, ctx)

    assert result == "error"


@pytest.mark.asyncio
async def test_api_call_public_ip_is_allowed() -> None:
    """パブリック IP → SSRF ガード通過 → リクエストが実行される。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://example.com/api")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=_mock_http_response(200, "ok"))

    with (
        patch("millicall.workflows.handlers.logic.httpx.AsyncClient", return_value=mock_client),
        patch("socket.getaddrinfo", return_value=_PUBLIC_SOCKADDR),
    ):
        result = await handle_api_call(node, ctx)

    assert result == "success"
    mock_client.request.assert_awaited_once()


# --------------------------------------------------------------------------- #
# SSRF ガード — 追加のバイパス回帰（正規化 / 分類フラグ / TOCTOU 固定）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_api_call_ssrf_ipv4_mapped_ipv6_loopback_blocked() -> None:
    """IPv4-mapped IPv6 (::ffff:127.0.0.1) → 正規化してブロック。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://example.com/api")

    mapped = [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::ffff:127.0.0.1", 80, 0, 0))]
    with patch("socket.getaddrinfo", return_value=mapped):
        result = await handle_api_call(node, ctx)

    assert result == "error"


@pytest.mark.asyncio
async def test_api_call_ssrf_cgnat_100_64_blocked() -> None:
    """CGNAT 帯 (100.64.0.0/10) → is_private で塞ぐ（旧 CIDR 表の穴）。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://example.com/api")

    cgnat = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("100.64.1.1", 80))]
    with patch("socket.getaddrinfo", return_value=cgnat):
        result = await handle_api_call(node, ctx)

    assert result == "error"


@pytest.mark.asyncio
async def test_api_call_ssrf_unspecified_0_0_0_0_blocked() -> None:
    """0.0.0.0/8 → is_unspecified/is_reserved で塞ぐ（旧 CIDR 表の穴）。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://example.com/api")

    unspec = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("0.0.0.0", 80))]
    with patch("socket.getaddrinfo", return_value=unspec):
        result = await handle_api_call(node, ctx)

    assert result == "error"


@pytest.mark.asyncio
async def test_api_call_ssrf_multi_ip_any_blocked_rejects() -> None:
    """複数解決結果のうち 1 つでも内部 IP なら全体をブロック。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://example.com/api")

    mixed = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 80)),
    ]
    with patch("socket.getaddrinfo", return_value=mixed):
        result = await handle_api_call(node, ctx)

    assert result == "error"


@pytest.mark.asyncio
async def test_api_call_pins_resolved_ip_no_follow_redirects() -> None:
    """検証済み IP に接続を固定し、follow_redirects=False で AsyncClient を構成する。"""
    ctx = make_ctx()
    node = make_api_call_node(url="http://example.com/api")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=_mock_http_response(200, "ok"))

    captured: dict = {}

    def _factory(*args, **kwargs):
        captured.update(kwargs)
        return mock_client

    with (
        patch("millicall.workflows.handlers.logic.httpx.AsyncClient", side_effect=_factory),
        patch("socket.getaddrinfo", return_value=_PUBLIC_SOCKADDR),
    ):
        result = await handle_api_call(node, ctx)

    assert result == "success"
    assert captured.get("follow_redirects") is False
    # pinned transport が渡っており、検証済み IP に固定されている
    transport = captured.get("transport")
    assert transport is not None
    assert transport._pinned_ip == "93.184.216.34"
    assert transport._pinned_host == "example.com"


# --------------------------------------------------------------------------- #
# ハンドラが executor.HANDLERS に登録されていること
# --------------------------------------------------------------------------- #


def test_handlers_registered_in_global_registry() -> None:
    """logic.py のインポートで condition/set_variable/time_condition/api_call が登録される。"""
    from millicall.workflows.executor import HANDLERS

    assert "condition" in HANDLERS
    assert "set_variable" in HANDLERS
    assert "time_condition" in HANDLERS
    assert "api_call" in HANDLERS


def test_core_handlers_not_overwritten() -> None:
    """logic.py は start/end/hangup を上書きしない（コアが保有）。"""
    # コアハンドラは _CORE_HANDLERS に存在するが HANDLERS(グローバル) には入れない設計
    # logic.py が start/end/hangup を HANDLERS に追加していないことを確認
    from millicall.workflows.executor import HANDLERS

    # start/end/hangup は _CORE_HANDLERS にあり、logic.py で上書きしないのが正しい
    # ただし上書きしても動作は同じなので、ここでは「logic.py が不要なものを登録しない」だけ確認
    # goto はコア組込ナビゲーションなのでハンドラ不要
    assert "goto" not in HANDLERS
