"""netd/server.py のユニットテスト。

実際の UNIX ソケットを tmpdir に作成してテストする。
nft/dnsmasq/tailscale は FakeSystemOps で置き換える。

注意: macOS の AF_UNIX パス長制限 (104 バイト) のため、
ソケットパスは短い固定ディレクトリ (/tmp) 以下に作成する。
pytest の tmp_path は長すぎる場合があるため使用しない。
"""

import asyncio
import contextlib
import json
import os
import uuid
from pathlib import Path

import pytest

from millicall.netd.server import serve

# ---------------------------------------------------------------------------
# FakeSystemOps + FakeSettings — テスト用フェイク
# ---------------------------------------------------------------------------


class FakeSystemOps:
    """サーバテスト用 SystemOps フェイク。"""

    def __init__(
        self,
        *,
        run_rc: int = 0,
        run_stdout: str = "",
        run_stderr: str = "",
        read_content: str = "",
    ) -> None:
        self.run_calls: list[tuple[list[str], str | None]] = []
        self.write_calls: list[tuple[str, str]] = []
        self.read_calls: list[str] = []
        self._run_rc = run_rc
        self._run_stdout = run_stdout
        self._run_stderr = run_stderr
        self._read_content = read_content

    async def run(
        self,
        argv: list[str],
        *,
        input_text: str | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, str, str]:
        self.run_calls.append((argv, input_text))
        return (self._run_rc, self._run_stdout, self._run_stderr)

    def write_file(self, path: str, content: str) -> None:
        self.write_calls.append((path, content))

    def read_file(self, path: str) -> str:
        self.read_calls.append(path)
        return self._read_content


class FakeSettings:
    """テスト用 Settings フェイク（netd_socket_path は tmpdir を使う）。"""

    def __init__(self, socket_path: str) -> None:
        self.netd_socket_path = socket_path
        self.dnsmasq_conf_path = "/etc/dnsmasq.d/millicall.conf"
        self.dnsmasq_leases_path = "/var/lib/misc/dnsmasq.leases"
        self.nftables_table = "millicall_nat"


@pytest.fixture
def short_socket_path():
    """AF_UNIX のパス長制限 (macOS: 104 バイト) を回避するために
    /tmp 以下に短いパスのソケットファイルを作成するフィクスチャ。"""
    # uuid4 の先頭 8 文字を使って短いファイル名を生成
    path = f"/tmp/netd_{uuid.uuid4().hex[:8]}.sock"
    yield path
    # クリーンアップ
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


# ---------------------------------------------------------------------------
# テストヘルパ
# ---------------------------------------------------------------------------


async def _send_request(socket_path: str, payload: dict) -> dict:
    """UNIX ソケットにリクエストを送り、レスポンスを受け取る。

    Args:
        socket_path: UNIX ソケットのパス。
        payload: リクエスト dict。

    Returns:
        パースされたレスポンス dict。
    """
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        line = json.dumps(payload) + "\n"
        writer.write(line.encode())
        await writer.drain()
        resp_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        return json.loads(resp_line.decode())
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def _start_server(settings: FakeSettings, ops: FakeSystemOps) -> asyncio.Task:
    """サーバを起動してタスクを返す。"""
    task = asyncio.create_task(serve(settings, ops))
    # サーバが起動してソケットが作成されるまで少し待つ
    for _ in range(50):
        await asyncio.sleep(0.01)
        if os.path.exists(settings.netd_socket_path):
            break
    return task


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------


class TestNetdServer:
    """serve() のインテグレーションテスト（実 UNIX ソケット + フェイク ops）。"""

    @pytest.mark.asyncio
    async def test_unknown_command_returns_error_response(self, short_socket_path: str):
        """不明なコマンドに対してエラー JSON レスポンスを返す。"""
        ops = FakeSystemOps()
        settings = FakeSettings(short_socket_path)

        task = await _start_server(settings, ops)
        try:
            resp = await _send_request(short_socket_path, {"cmd": "unknown_xyz"})
            assert resp["ok"] is False
            assert "unknown" in resp["error"].lower()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_valid_command_returns_ok_response(self, short_socket_path: str):
        """有効なコマンド (tailscale_down) に対して ok=True を返す。"""
        ops = FakeSystemOps()
        settings = FakeSettings(short_socket_path)

        task = await _start_server(settings, ops)
        try:
            resp = await _send_request(short_socket_path, {"cmd": "tailscale_down"})
            assert resp["ok"] is True
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_malformed_json_returns_error(self, short_socket_path: str):
        """不正な JSON に対してエラーレスポンスを返す。"""
        ops = FakeSystemOps()
        settings = FakeSettings(short_socket_path)

        task = await _start_server(settings, ops)
        try:
            reader, writer = await asyncio.open_unix_connection(short_socket_path)
            try:
                writer.write(b"this is not valid json\n")
                await writer.drain()
                resp_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                resp = json.loads(resp_line.decode())
                assert resp["ok"] is False
                assert "json" in resp["error"].lower() or "invalid" in resp["error"].lower()
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_oversized_line_returns_error(self, short_socket_path: str):
        """最大長を超えるリクエスト行に対してエラーレスポンスを返す。"""
        ops = FakeSystemOps()
        settings = FakeSettings(short_socket_path)

        task = await _start_server(settings, ops)
        try:
            reader, writer = await asyncio.open_unix_connection(short_socket_path)
            try:
                # 64KiB + 1 バイトを超える行を送信
                oversized = b"x" * 65537 + b"\n"
                writer.write(oversized)
                await writer.drain()
                resp_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                resp = json.loads(resp_line.decode())
                assert resp["ok"] is False
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_multiple_connections_sequential(self, short_socket_path: str):
        """複数の接続を順次処理できる。"""
        ops = FakeSystemOps()
        settings = FakeSettings(short_socket_path)

        task = await _start_server(settings, ops)
        try:
            # 1 回目
            resp1 = await _send_request(short_socket_path, {"cmd": "tailscale_down"})
            assert resp1["ok"] is True

            # 2 回目
            resp2 = await _send_request(short_socket_path, {"cmd": "unknown_xyz"})
            assert resp2["ok"] is False
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_socket_permissions_are_660(self, short_socket_path: str):
        """ソケットファイルのパーミッションが 0o660 に設定される。"""
        ops = FakeSystemOps()
        settings = FakeSettings(short_socket_path)

        task = await _start_server(settings, ops)
        try:
            mode = oct(os.stat(short_socket_path).st_mode)
            # 最後 3 桁が 660 であることを確認
            assert mode.endswith("660")
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_stale_socket_file_is_removed(self, short_socket_path: str):
        """古いソケットファイルが存在しても正常に起動できる。"""
        # 古いソケットファイルを作成
        Path(short_socket_path).touch()

        ops = FakeSystemOps()
        settings = FakeSettings(short_socket_path)

        task = await _start_server(settings, ops)
        try:
            resp = await _send_request(short_socket_path, {"cmd": "tailscale_down"})
            assert resp["ok"] is True
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_secret_auth_key_not_in_error_response(self, short_socket_path: str):
        """Tailscale 認証キー（秘密情報）がエラーレスポンスに漏れない。"""
        ops = FakeSystemOps()
        settings = FakeSettings(short_socket_path)

        task = await _start_server(settings, ops)
        try:
            secret_key = "tskey-auth-SUPERSECRET123-should-not-appear"
            # 不正な形式のキーを送信（シェルメタ文字付き）
            resp = await _send_request(
                short_socket_path,
                {"cmd": "tailscale_up", "auth_key": "invalid-key-" + secret_key},
            )
            resp_str = json.dumps(resp)
            # キーの値がレスポンスに含まれていないことを確認
            assert secret_key not in resp_str
            assert "SUPERSECRET123" not in resp_str
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_non_object_json_returns_error(self, short_socket_path: str):
        """JSON オブジェクト以外（配列など）に対してエラーを返す。"""
        ops = FakeSystemOps()
        settings = FakeSettings(short_socket_path)

        task = await _start_server(settings, ops)
        try:
            reader, writer = await asyncio.open_unix_connection(short_socket_path)
            try:
                writer.write(b'["not", "an", "object"]\n')
                await writer.drain()
                resp_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                resp = json.loads(resp_line.decode())
                assert resp["ok"] is False
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
