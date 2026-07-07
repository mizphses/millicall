"""NetdClient の単体テスト。

実際の netd プロセスや root 権限は一切不要。テスト用に asyncio.start_unix_server で
インプロセス UNIX ソケットサーバーを立ち上げ、プロトコルの正しさとエラーハンドリングを
検証する。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile

import pytest

from millicall.network.client import NetdClient, NetdError

# ---------------------------------------------------------------------------
# テスト用ソケットサーバーのヘルパ
# ---------------------------------------------------------------------------

# macOS の AF_UNIX ソケットパス上限は 104 バイト。pytest の tmp_path はフルパスが
# 長くなりすぎるため、短い一時ディレクトリを使って確実に上限内に収める。


@pytest.fixture
def short_tmp():
    """AF_UNIX パス長制限（macOS: 104 B）に収まる短いソケットパスを返す fixture。

    /tmp 以下の短い名前の一時ディレクトリを作成して返す。
    """
    d = tempfile.mkdtemp(dir="/tmp", prefix="mc")
    yield d
    # クリーンアップ
    for name in os.listdir(d):
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(d, name))
    with contextlib.suppress(OSError):
        os.rmdir(d)


def _make_server(short_dir: str, response: dict | bytes):
    """1リクエストを受信して固定レスポンスを返す UNIX ソケットサーバーを返すファクトリ。

    Args:
        short_dir: AF_UNIX パス長制限内に収まる短い一時ディレクトリのパス。
        response: 送信する応答。dict なら JSON 化して改行を付与、bytes なら生で送信。

    Returns:
        (socket_path, handler, received_lines) のタプル。
        received_lines は接続ごとに受信した行を格納するリスト（後から検証可能）。
    """
    sock_path = os.path.join(short_dir, "t.sock")
    received_lines: list[bytes] = []

    async def _handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        received_lines.append(line)
        if isinstance(response, dict):
            writer.write((json.dumps(response) + "\n").encode())
        else:
            writer.write(response)
        await writer.drain()
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()

    return sock_path, _handler, received_lines


# ---------------------------------------------------------------------------
# 各コマンドのペイロード検証と成功レスポンス解析
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_dhcp_sends_correct_payload(short_tmp):
    """apply_dhcp が正しいフィールドを持つリクエストを送信することを確認する。"""
    sock_path, handler, received = _make_server(short_tmp, {"ok": True})
    server = await asyncio.start_unix_server(handler, path=sock_path)
    async with server:
        client = NetdClient(sock_path, timeout=2.0)
        await client.apply_dhcp(
            lan_interface="eth0",
            lan_ip="192.168.100.1",
            lan_prefix=24,
            dhcp_range_start="192.168.100.100",
            dhcp_range_end="192.168.100.200",
            dhcp_lease_hours=12,
            provisioning_url="http://core/provision",
        )

    assert len(received) == 1
    payload = json.loads(received[0].decode())
    assert payload["cmd"] == "apply_dhcp"
    assert payload["lan_interface"] == "eth0"
    assert payload["lan_ip"] == "192.168.100.1"
    assert payload["lan_prefix"] == 24
    assert payload["dhcp_range_start"] == "192.168.100.100"
    assert payload["dhcp_range_end"] == "192.168.100.200"
    assert payload["dhcp_lease_hours"] == 12
    assert payload["provisioning_url"] == "http://core/provision"


@pytest.mark.asyncio
async def test_apply_nat_sends_correct_payload(short_tmp):
    """apply_nat が正しいフィールドを持つリクエストを送信することを確認する。"""
    sock_path, handler, received = _make_server(short_tmp, {"ok": True})
    server = await asyncio.start_unix_server(handler, path=sock_path)
    async with server:
        client = NetdClient(sock_path, timeout=2.0)
        await client.apply_nat(
            enabled=True,
            lan_ip="192.168.100.1",
            lan_prefix=24,
            wan_interface="eth1",
        )

    assert len(received) == 1
    payload = json.loads(received[0].decode())
    assert payload["cmd"] == "apply_nat"
    assert payload["enabled"] is True
    assert payload["lan_ip"] == "192.168.100.1"
    assert payload["lan_prefix"] == 24
    assert payload["wan_interface"] == "eth1"


@pytest.mark.asyncio
async def test_tailscale_up_sends_correct_payload(short_tmp):
    """tailscale_up が auth_key を含む正しいリクエストを送信することを確認する。"""
    sock_path, handler, received = _make_server(short_tmp, {"ok": True})
    server = await asyncio.start_unix_server(handler, path=sock_path)
    async with server:
        client = NetdClient(sock_path, timeout=2.0)
        await client.tailscale_up(auth_key="tskey-auth-test123")

    assert len(received) == 1
    payload = json.loads(received[0].decode())
    assert payload["cmd"] == "tailscale_up"
    assert payload["auth_key"] == "tskey-auth-test123"


@pytest.mark.asyncio
async def test_tailscale_down_sends_correct_payload(short_tmp):
    """tailscale_down が正しいコマンドを送信することを確認する。"""
    sock_path, handler, received = _make_server(short_tmp, {"ok": True})
    server = await asyncio.start_unix_server(handler, path=sock_path)
    async with server:
        client = NetdClient(sock_path, timeout=2.0)
        await client.tailscale_down()

    assert len(received) == 1
    payload = json.loads(received[0].decode())
    assert payload["cmd"] == "tailscale_down"


@pytest.mark.asyncio
async def test_tailscale_status_returns_status_dict(short_tmp):
    """tailscale_status がレスポンスの status 辞書を返すことを確認する。"""
    status_data = {"BackendState": "Running", "TailscaleIPs": ["100.64.0.1"]}
    sock_path, handler, received = _make_server(
        short_tmp, {"ok": True, "status": status_data}
    )
    server = await asyncio.start_unix_server(handler, path=sock_path)
    async with server:
        client = NetdClient(sock_path, timeout=2.0)
        result = await client.tailscale_status()

    assert result == status_data
    payload = json.loads(received[0].decode())
    assert payload["cmd"] == "tailscale_status"


@pytest.mark.asyncio
async def test_get_dhcp_leases_returns_leases_list(short_tmp):
    """get_dhcp_leases がレスポンスの leases リストを返すことを確認する。"""
    leases = [
        {"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.100.101", "hostname": "phone-1"},
        {"mac": "11:22:33:44:55:66", "ip": "192.168.100.102", "hostname": "phone-2"},
    ]
    sock_path, handler, received = _make_server(
        short_tmp, {"ok": True, "leases": leases}
    )
    server = await asyncio.start_unix_server(handler, path=sock_path)
    async with server:
        client = NetdClient(sock_path, timeout=2.0)
        result = await client.get_dhcp_leases()

    assert result == leases
    payload = json.loads(received[0].decode())
    assert payload["cmd"] == "get_dhcp_leases"


@pytest.mark.asyncio
async def test_get_nat_status_returns_bool(short_tmp):
    """get_nat_status が enabled フラグの bool 値を返すことを確認する。"""
    sock_path, handler, received = _make_server(
        short_tmp, {"ok": True, "enabled": True}
    )
    server = await asyncio.start_unix_server(handler, path=sock_path)
    async with server:
        client = NetdClient(sock_path, timeout=2.0)
        result = await client.get_nat_status()

    assert result is True
    payload = json.loads(received[0].decode())
    assert payload["cmd"] == "get_nat_status"


@pytest.mark.asyncio
async def test_get_nat_status_false(short_tmp):
    """get_nat_status が enabled=false のとき False を返すことを確認する。"""
    sock_path, handler, _ = _make_server(short_tmp, {"ok": True, "enabled": False})
    server = await asyncio.start_unix_server(handler, path=sock_path)
    async with server:
        client = NetdClient(sock_path, timeout=2.0)
        result = await client.get_nat_status()

    assert result is False


# ---------------------------------------------------------------------------
# ok:false レスポンス → NetdError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ok_false_raises_netd_error(short_tmp):
    """ok:false レスポンスを受け取ったとき NetdError を送出することを確認する。"""
    sock_path, handler, _ = _make_server(
        short_tmp, {"ok": False, "error": "設定書き込み失敗"}
    )
    server = await asyncio.start_unix_server(handler, path=sock_path)
    async with server:
        client = NetdClient(sock_path, timeout=2.0)
        with pytest.raises(NetdError, match="設定書き込み失敗"):
            await client.apply_dhcp(
                lan_interface="eth0",
                lan_ip="192.168.100.1",
                lan_prefix=24,
                dhcp_range_start="192.168.100.100",
                dhcp_range_end="192.168.100.200",
                dhcp_lease_hours=12,
                provisioning_url="http://core/provision",
            )


@pytest.mark.asyncio
async def test_nat_ok_false_raises_netd_error(short_tmp):
    """apply_nat での ok:false が NetdError を送出することを確認する。"""
    sock_path, handler, _ = _make_server(
        short_tmp, {"ok": False, "error": "nftables エラー"}
    )
    server = await asyncio.start_unix_server(handler, path=sock_path)
    async with server:
        client = NetdClient(sock_path, timeout=2.0)
        with pytest.raises(NetdError, match="nftables エラー"):
            await client.apply_nat(
                enabled=True,
                lan_ip="192.168.100.1",
                lan_prefix=24,
                wan_interface="eth1",
            )


# ---------------------------------------------------------------------------
# tailscale_up の ok:false → エラーに auth_key が含まれないこと
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tailscale_up_error_does_not_leak_auth_key(short_tmp):
    """tailscale_up が ok:false のとき、送出した NetdError に auth_key が含まれないことを確認する。"""
    secret_key = "tskey-auth-supersecretvalue9999"
    sock_path, handler, _ = _make_server(
        short_tmp, {"ok": False, "error": "認証失敗"}
    )
    server = await asyncio.start_unix_server(handler, path=sock_path)
    async with server:
        client = NetdClient(sock_path, timeout=2.0)
        with pytest.raises(NetdError) as exc_info:
            await client.tailscale_up(auth_key=secret_key)

    error_message = str(exc_info.value)
    assert secret_key not in error_message, (
        f"auth_key がエラーメッセージに含まれています: {error_message!r}"
    )


# ---------------------------------------------------------------------------
# 接続失敗 → NetdError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_failure_raises_netd_error(tmp_path):
    """存在しないソケットパスへの接続が NetdError を送出することを確認する。"""
    # 存在しないパスは短くする必要がない（接続試行前にエラーになる）
    nonexistent_sock = "/tmp/mc_nonexistent_test.sock"
    client = NetdClient(nonexistent_sock, timeout=2.0)
    with pytest.raises(NetdError):
        await client.get_nat_status()


# ---------------------------------------------------------------------------
# タイムアウト → NetdError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_raises_netd_error(short_tmp):
    """応答しないサーバーへの接続がタイムアウトし NetdError を送出することを確認する。"""
    sock_path = os.path.join(short_tmp, "slow.sock")
    handler_done = asyncio.Event()

    # 接続は受け付けるが応答しないサーバー。クライアントが切断したら終了する。
    async def _slow_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            # リクエストを読んだ後、クライアント切断を検知できるよう短い間隔で確認する
            await reader.readline()
            with contextlib.suppress(asyncio.TimeoutError):
                # クライアントが切断するまで待機（最大 5 秒 — タイムアウト後すぐ切断される）
                await asyncio.wait_for(reader.read(1), timeout=5.0)
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            handler_done.set()

    server = await asyncio.start_unix_server(_slow_handler, path=sock_path)
    try:
        # timeout を非常に短くして確実にタイムアウトを発生させる
        client = NetdClient(sock_path, timeout=0.1)
        with pytest.raises(NetdError, match="タイムアウト"):
            await client.get_nat_status()
        # ハンドラが確実に終了するまで待つ（最大 6 秒）
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(handler_done.wait(), timeout=6.0)
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# 不正 JSON レスポンス → NetdError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_json_raises_netd_error(short_tmp):
    """不正な JSON レスポンスを受信したとき NetdError を送出することを確認する。"""
    sock_path, handler, _ = _make_server(short_tmp, b"this is not json\n")
    server = await asyncio.start_unix_server(handler, path=sock_path)
    async with server:
        client = NetdClient(sock_path, timeout=2.0)
        with pytest.raises(NetdError, match="JSON パース失敗"):
            await client.get_nat_status()
