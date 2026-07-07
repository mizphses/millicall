"""netd/commands.py のユニットテスト。

FakeSystemOps を使って実際の nft/dnsmasq/tailscale 呼び出しなしにテストする。
"""

import json

import pytest

from millicall.netd.commands import dispatch

# ---------------------------------------------------------------------------
# FakeSystemOps — テスト用のシステム操作フェイク
# ---------------------------------------------------------------------------


class FakeSystemOps:
    """テスト用 SystemOps フェイク実装。

    run() / write_file() / read_file() の呼び出しを記録し、
    テスト側で assert できるようにする。
    """

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
    """テスト用 Settings フェイク。"""

    dnsmasq_conf_path = "/etc/dnsmasq.d/millicall.conf"
    dnsmasq_leases_path = "/var/lib/misc/dnsmasq.leases"
    nftables_table = "millicall_nat"


SETTINGS = FakeSettings()

# テスト用の有効な apply_dhcp ペイロード
_VALID_DHCP_PAYLOAD = {
    "cmd": "apply_dhcp",
    "lan_interface": "enp3s0",
    "lan_ip": "172.20.0.1",
    "dhcp_range_start": "172.20.1.1",
    "dhcp_range_end": "172.20.254.254",
    "dhcp_lease_hours": 12,
    "provisioning_url": "http://172.20.0.1:8000/provisioning/",
    "lan_prefix": 16,
}

# テスト用の有効な apply_nat ペイロード
_VALID_NAT_PAYLOAD = {
    "cmd": "apply_nat",
    "enabled": True,
    "lan_ip": "172.20.0.1",
    "lan_prefix": 16,
    "wan_interface": "eth0",
}


# ---------------------------------------------------------------------------
# apply_dhcp
# ---------------------------------------------------------------------------


class TestApplyDhcp:
    """apply_dhcp ハンドラのテスト。"""

    @pytest.mark.asyncio
    async def test_valid_writes_conf_and_reloads(self):
        ops = FakeSystemOps()
        resp = await dispatch(_VALID_DHCP_PAYLOAD, ops, SETTINGS)

        assert resp["ok"] is True
        # 設定ファイルが書き込まれたことを確認
        assert len(ops.write_calls) == 1
        written_path, written_content = ops.write_calls[0]
        assert written_path == SETTINGS.dnsmasq_conf_path
        assert "interface=enp3s0" in written_content
        assert "dhcp-range=172.20.1.1,172.20.254.254" in written_content
        assert "http://172.20.0.1:8000/provisioning/" in written_content

        # dnsmasq restart が呼ばれたことを確認
        assert len(ops.run_calls) == 1
        argv, _ = ops.run_calls[0]
        assert argv == ["systemctl", "restart", "dnsmasq"]

    @pytest.mark.asyncio
    async def test_invalid_interface_name_returns_error_no_ops(self):
        """不正なインターフェイス名では ops を一切呼ばない。"""
        ops = FakeSystemOps()
        payload = {**_VALID_DHCP_PAYLOAD, "lan_interface": "eth0;rm -rf /"}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []
        assert ops.write_calls == []

    @pytest.mark.asyncio
    async def test_invalid_interface_with_newline_returns_error_no_ops(self):
        ops = FakeSystemOps()
        payload = {**_VALID_DHCP_PAYLOAD, "lan_interface": "eth0\nevil"}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []
        assert ops.write_calls == []

    @pytest.mark.asyncio
    async def test_invalid_lan_ip_returns_error_no_ops(self):
        ops = FakeSystemOps()
        payload = {**_VALID_DHCP_PAYLOAD, "lan_ip": "999.999.999.999"}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []
        assert ops.write_calls == []

    @pytest.mark.asyncio
    async def test_reversed_dhcp_range_returns_error_no_ops(self):
        ops = FakeSystemOps()
        payload = {
            **_VALID_DHCP_PAYLOAD,
            "dhcp_range_start": "172.20.254.254",
            "dhcp_range_end": "172.20.1.1",
        }
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []
        assert ops.write_calls == []

    @pytest.mark.asyncio
    async def test_provisioning_url_with_newline_injection_returns_error_no_ops(self):
        """改行インジェクション攻撃は拒否し、ops を呼ばない。"""
        ops = FakeSystemOps()
        payload = {
            **_VALID_DHCP_PAYLOAD,
            "provisioning_url": "http://172.20.0.1:8000/\ndhcp-option=66,http://evil.com/",
        }
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []
        assert ops.write_calls == []

    @pytest.mark.asyncio
    async def test_provisioning_url_wrong_host_returns_error_no_ops(self):
        ops = FakeSystemOps()
        payload = {
            **_VALID_DHCP_PAYLOAD,
            "provisioning_url": "http://192.168.1.100:8000/provisioning/",
        }
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []
        assert ops.write_calls == []

    @pytest.mark.asyncio
    async def test_dnsmasq_restart_failure_returns_error(self):
        ops = FakeSystemOps(run_rc=1, run_stderr="dnsmasq: error")
        resp = await dispatch(_VALID_DHCP_PAYLOAD, ops, SETTINGS)

        assert resp["ok"] is False
        assert "dnsmasq" in resp["error"].lower() or "1" in resp["error"]


# ---------------------------------------------------------------------------
# apply_nat
# ---------------------------------------------------------------------------


class TestApplyNat:
    """apply_nat ハンドラのテスト。"""

    @pytest.mark.asyncio
    async def test_valid_enabled_calls_sysctl_and_nft(self):
        ops = FakeSystemOps()
        resp = await dispatch(_VALID_NAT_PAYLOAD, ops, SETTINGS)

        assert resp["ok"] is True
        # sysctl と nft の 2 コマンドが呼ばれる
        assert len(ops.run_calls) == 2
        argvs = [call[0] for call in ops.run_calls]
        assert ["sysctl", "-w", "net.ipv4.ip_forward=1"] in argvs
        nft_call = next(c for c in argvs if c[0] == "nft")
        assert nft_call == ["nft", "-f", "-"]

        # nft への stdin にマスカレードルールが含まれる
        nft_idx = argvs.index(["nft", "-f", "-"])
        nft_input = ops.run_calls[nft_idx][1]
        assert nft_input is not None
        assert "masquerade" in nft_input

    @pytest.mark.asyncio
    async def test_valid_disabled_no_sysctl(self):
        ops = FakeSystemOps()
        payload = {**_VALID_NAT_PAYLOAD, "enabled": False}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is True
        # enabled=False 時は sysctl を呼ばない
        argvs = [call[0] for call in ops.run_calls]
        assert not any("sysctl" in argv[0] for argv in argvs)

    @pytest.mark.asyncio
    async def test_invalid_wan_interface_injection_returns_error_no_ops(self):
        """WAN インターフェイス名のインジェクション攻撃は拒否し、ops を呼ばない。"""
        ops = FakeSystemOps()
        payload = {**_VALID_NAT_PAYLOAD, "wan_interface": "eth0;iptables -F"}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []

    @pytest.mark.asyncio
    async def test_invalid_lan_ip_returns_error_no_ops(self):
        ops = FakeSystemOps()
        payload = {**_VALID_NAT_PAYLOAD, "lan_ip": "not-an-ip"}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []

    @pytest.mark.asyncio
    async def test_invalid_prefix_returns_error_no_ops(self):
        ops = FakeSystemOps()
        payload = {**_VALID_NAT_PAYLOAD, "lan_prefix": 99}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []

    @pytest.mark.asyncio
    async def test_nft_failure_returns_error(self):
        ops = FakeSystemOps(run_rc=1, run_stderr="nft: error")
        resp = await dispatch(_VALID_NAT_PAYLOAD, ops, SETTINGS)

        assert resp["ok"] is False


# ---------------------------------------------------------------------------
# tailscale_up
# ---------------------------------------------------------------------------


class TestTailscaleUp:
    """tailscale_up ハンドラのテスト。"""

    _VALID_KEY = "tskey-auth-ABC123DEF456-xyz789"

    @pytest.mark.asyncio
    async def test_valid_key_calls_tailscale(self):
        ops = FakeSystemOps()
        payload = {"cmd": "tailscale_up", "auth_key": self._VALID_KEY}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is True
        assert len(ops.run_calls) == 1
        argv, _ = ops.run_calls[0]
        assert argv[0] == "tailscale"
        assert argv[1] == "up"
        assert "--authkey" in argv
        # キー自体が argv に含まれる（これは正常 — tailscale コマンドへの引数として必要）
        assert self._VALID_KEY in argv

    @pytest.mark.asyncio
    async def test_invalid_key_format_returns_error_no_ops(self):
        """不正なキー形式は ops を呼ばずエラーを返す。"""
        ops = FakeSystemOps()
        payload = {"cmd": "tailscale_up", "auth_key": "not-a-valid-key"}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []

    @pytest.mark.asyncio
    async def test_invalid_key_never_in_error_response(self):
        """不正なキーはエラーメッセージに含まれてはならない。"""
        ops = FakeSystemOps()
        secret_key = "not-a-valid-key-with-secret-data"
        payload = {"cmd": "tailscale_up", "auth_key": secret_key}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        # キーの値がエラーメッセージに漏れていないことを確認
        assert secret_key not in resp.get("error", "")
        assert "not-a-valid-key" not in resp.get("error", "")

    @pytest.mark.asyncio
    async def test_empty_key_returns_error_no_ops(self):
        ops = FakeSystemOps()
        payload = {"cmd": "tailscale_up", "auth_key": ""}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []

    @pytest.mark.asyncio
    async def test_key_with_shell_metachar_returns_error_no_ops(self):
        """シェルメタ文字を含むキーは拒否する。"""
        ops = FakeSystemOps()
        payload = {"cmd": "tailscale_up", "auth_key": "tskey-abc;rm -rf /"}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []

    @pytest.mark.asyncio
    async def test_tailscale_command_failure_key_not_in_error(self):
        """tailscale コマンド失敗時、キーはエラーレスポンスに含まれない。"""
        auth_key = self._VALID_KEY
        ops = FakeSystemOps(run_rc=1, run_stderr=f"Error: invalid auth key: {auth_key}")
        payload = {"cmd": "tailscale_up", "auth_key": auth_key}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        # キーがエラーメッセージに漏れていないことを確認
        assert auth_key not in resp.get("error", "")


# ---------------------------------------------------------------------------
# tailscale_down
# ---------------------------------------------------------------------------


class TestTailscaleDown:
    """tailscale_down ハンドラのテスト。"""

    @pytest.mark.asyncio
    async def test_calls_tailscale_down(self):
        ops = FakeSystemOps()
        resp = await dispatch({"cmd": "tailscale_down"}, ops, SETTINGS)

        assert resp["ok"] is True
        assert len(ops.run_calls) == 1
        assert ops.run_calls[0][0] == ["tailscale", "down"]


# ---------------------------------------------------------------------------
# tailscale_status
# ---------------------------------------------------------------------------


_TAILSCALE_STATUS_JSON = json.dumps({
    "BackendState": "Running",
    "Self": {
        "ID": "n123456789",
        "HostName": "millicall-server",
        "DNSName": "millicall-server.ts.net",
        "TailscaleIPs": ["100.64.0.1"],
        "Online": True,
        # 認証キー等の機密情報（実際の出力には含まれないが念のため）
        "AuthKey": "tskey-should-not-be-returned",
    },
    "Peer": {
        "n987654321": {
            "ID": "n987654321",
            "HostName": "peer-device",
            "DNSName": "peer-device.ts.net",
            "TailscaleIPs": ["100.64.0.2"],
            "Online": True,
        }
    },
})


class TestTailscaleStatus:
    """tailscale_status ハンドラのテスト。"""

    @pytest.mark.asyncio
    async def test_returns_backend_state(self):
        ops = FakeSystemOps(run_stdout=_TAILSCALE_STATUS_JSON)
        resp = await dispatch({"cmd": "tailscale_status"}, ops, SETTINGS)

        assert resp["ok"] is True
        assert resp["status"]["backend_state"] == "Running"

    @pytest.mark.asyncio
    async def test_returns_self_info(self):
        ops = FakeSystemOps(run_stdout=_TAILSCALE_STATUS_JSON)
        resp = await dispatch({"cmd": "tailscale_status"}, ops, SETTINGS)

        self_info = resp["status"]["self"]
        assert self_info["hostname"] == "millicall-server"
        assert "100.64.0.1" in self_info["ips"]

    @pytest.mark.asyncio
    async def test_returns_peers(self):
        ops = FakeSystemOps(run_stdout=_TAILSCALE_STATUS_JSON)
        resp = await dispatch({"cmd": "tailscale_status"}, ops, SETTINGS)

        peers = resp["status"]["peers"]
        assert len(peers) == 1
        assert peers[0]["hostname"] == "peer-device"

    @pytest.mark.asyncio
    async def test_auth_key_not_in_response(self):
        """機密情報フィールド（AuthKey 等）はレスポンスに含まれない。"""
        ops = FakeSystemOps(run_stdout=_TAILSCALE_STATUS_JSON)
        resp = await dispatch({"cmd": "tailscale_status"}, ops, SETTINGS)

        # レスポンスを JSON 文字列に変換して確認
        resp_str = json.dumps(resp)
        assert "tskey-should-not-be-returned" not in resp_str
        assert "AuthKey" not in resp_str


# ---------------------------------------------------------------------------
# get_dhcp_leases
# ---------------------------------------------------------------------------


_LEASES_CONTENT = """\
1720000000 aa:bb:cc:dd:ee:ff 172.20.1.10 phone-yealink-1 *
1720000001 AA:BB:CC:DD:EE:00 172.20.1.11 phone-panasonic-1 01:aa:bb:cc:dd:ee:00
1720000002 ff-ee-dd-cc-bb-aa 172.20.1.12 * *
bad-line-missing-fields
not-a-mac 172.20.1.13 hostname-bad *
1720000005 aa:bb:cc:dd:ee:ff 999.999.999.999 bad-ip *
"""


class TestGetDhcpLeases:
    """get_dhcp_leases ハンドラのテスト。"""

    @pytest.mark.asyncio
    async def test_parses_valid_leases(self):
        ops = FakeSystemOps(read_content=_LEASES_CONTENT)
        resp = await dispatch({"cmd": "get_dhcp_leases"}, ops, SETTINGS)

        assert resp["ok"] is True
        leases = resp["leases"]
        # 有効な行 3 件（bad-line と not-a-mac と bad-ip はスキップ）
        assert len(leases) == 3

    @pytest.mark.asyncio
    async def test_mac_normalized_to_uppercase_colon(self):
        ops = FakeSystemOps(read_content=_LEASES_CONTENT)
        resp = await dispatch({"cmd": "get_dhcp_leases"}, ops, SETTINGS)

        macs = {lease["mac"] for lease in resp["leases"]}
        assert "AA:BB:CC:DD:EE:FF" in macs  # 小文字コロン → 正規化
        assert "AA:BB:CC:DD:EE:00" in macs  # 大文字コロン → そのまま
        assert "FF:EE:DD:CC:BB:AA" in macs  # ハイフン区切り → 正規化

    @pytest.mark.asyncio
    async def test_hostname_asterisk_replaced_with_empty(self):
        ops = FakeSystemOps(read_content=_LEASES_CONTENT)
        resp = await dispatch({"cmd": "get_dhcp_leases"}, ops, SETTINGS)

        # hostname が "*" の行は空文字に置き換え
        hostnames = {lease["hostname"] for lease in resp["leases"]}
        assert "" in hostnames  # ff-ee-dd-cc-bb-aa の行

    @pytest.mark.asyncio
    async def test_malformed_lines_skipped(self):
        """不正な行はスキップされ、エラーにならない。"""
        ops = FakeSystemOps(read_content="bad-line\n" + _LEASES_CONTENT)
        resp = await dispatch({"cmd": "get_dhcp_leases"}, ops, SETTINGS)

        assert resp["ok"] is True
        assert len(resp["leases"]) == 3  # 有効な 3 件のみ

    @pytest.mark.asyncio
    async def test_file_not_found_returns_empty_leases(self):
        """リースファイルが存在しない場合は空リストを返す。"""

        class NotFoundOps(FakeSystemOps):
            def read_file(self, path: str) -> str:
                raise FileNotFoundError(path)

        ops = NotFoundOps()
        resp = await dispatch({"cmd": "get_dhcp_leases"}, ops, SETTINGS)

        assert resp["ok"] is True
        assert resp["leases"] == []

    @pytest.mark.asyncio
    async def test_reads_from_correct_path(self):
        ops = FakeSystemOps()
        await dispatch({"cmd": "get_dhcp_leases"}, ops, SETTINGS)
        assert SETTINGS.dnsmasq_leases_path in ops.read_calls


# ---------------------------------------------------------------------------
# get_nat_status
# ---------------------------------------------------------------------------


class TestGetNatStatus:
    """get_nat_status ハンドラのテスト。"""

    @pytest.mark.asyncio
    async def test_masquerade_present_returns_enabled_true(self):
        nft_output = "table ip millicall_nat {\n  chain postrouting {\n    masquerade\n  }\n}"
        ops = FakeSystemOps(run_stdout=nft_output)
        resp = await dispatch({"cmd": "get_nat_status"}, ops, SETTINGS)

        assert resp["ok"] is True
        assert resp["enabled"] is True

    @pytest.mark.asyncio
    async def test_table_not_found_returns_enabled_false(self):
        """nft コマンドが失敗した場合（テーブルなし）は enabled=False。"""
        ops = FakeSystemOps(run_rc=1, run_stderr="Error: No such file or directory")
        resp = await dispatch({"cmd": "get_nat_status"}, ops, SETTINGS)

        assert resp["ok"] is True
        assert resp["enabled"] is False

    @pytest.mark.asyncio
    async def test_no_masquerade_in_output_returns_enabled_false(self):
        ops = FakeSystemOps(run_stdout="table ip millicall_nat { }")
        resp = await dispatch({"cmd": "get_nat_status"}, ops, SETTINGS)

        assert resp["ok"] is True
        assert resp["enabled"] is False


# ---------------------------------------------------------------------------
# 未知コマンド
# ---------------------------------------------------------------------------


class TestUnknownCommand:
    """未知コマンドのテスト。"""

    @pytest.mark.asyncio
    async def test_unknown_cmd_returns_error(self):
        ops = FakeSystemOps()
        resp = await dispatch({"cmd": "unknown_command_xyz"}, ops, SETTINGS)

        assert resp["ok"] is False
        assert "unknown" in resp["error"].lower()

    @pytest.mark.asyncio
    async def test_missing_cmd_returns_error(self):
        ops = FakeSystemOps()
        resp = await dispatch({}, ops, SETTINGS)

        assert resp["ok"] is False

    @pytest.mark.asyncio
    async def test_unknown_cmd_makes_no_ops_calls(self):
        ops = FakeSystemOps()
        await dispatch({"cmd": "rm_rf_slash"}, ops, SETTINGS)

        assert ops.run_calls == []
        assert ops.write_calls == []
        assert ops.read_calls == []
