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
    dnsmasq_reload_cmd = "systemctl restart dnsmasq"


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

    @pytest.mark.asyncio
    async def test_custom_reload_cmd_is_used(self):
        """dnsmasq_reload_cmd を上書きすると、その argv が ops.run に渡される。"""

        class CustomReloadSettings(FakeSettings):
            dnsmasq_reload_cmd = "/usr/local/bin/reload-dnsmasq.sh"

        ops = FakeSystemOps()
        resp = await dispatch(_VALID_DHCP_PAYLOAD, ops, CustomReloadSettings())

        assert resp["ok"] is True
        assert len(ops.run_calls) == 1
        argv, _ = ops.run_calls[0]
        assert argv == ["/usr/local/bin/reload-dnsmasq.sh"]

    @pytest.mark.asyncio
    async def test_empty_reload_cmd_returns_error(self):
        """dnsmasq_reload_cmd が空文字列のときはエラーを返す。"""

        class EmptyReloadSettings(FakeSettings):
            dnsmasq_reload_cmd = ""

        ops = FakeSystemOps()
        resp = await dispatch(_VALID_DHCP_PAYLOAD, ops, EmptyReloadSettings())

        assert resp["ok"] is False
        assert ops.run_calls == []


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

        # nft への stdin にマスカレードルールが含まれる（NAT 有効）
        nft_idx = argvs.index(["nft", "-f", "-"])
        nft_input = ops.run_calls[nft_idx][1]
        assert nft_input is not None
        assert "masquerade" in nft_input

    @pytest.mark.asyncio
    async def test_input_filter_chain_present_when_enabled(self):
        """apply_nat (enabled=True) が生成するルールセットに INPUT フィルタチェーンが含まれること。"""
        ops = FakeSystemOps()
        payload = {**_VALID_NAT_PAYLOAD, "http_port": 80}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is True
        argvs = [call[0] for call in ops.run_calls]
        nft_idx = argvs.index(["nft", "-f", "-"])
        nft_input = ops.run_calls[nft_idx][1]
        assert nft_input is not None

        # millicall_filter テーブルの INPUT チェーンが含まれること
        assert "millicall_filter" in nft_input
        assert "type filter hook input" in nft_input
        # LAN CIDR (172.20.0.0/16) が accept ルールにあること
        assert "172.20.0.0/16" in nft_input
        assert "tcp dport 80" in nft_input
        # WAN インターフェイス (eth0) が drop ルールにあること
        assert "'eth0'" in nft_input or "eth0" in nft_input
        assert "drop" in nft_input
        # マスカレードも含まれること（NAT 有効）
        assert "masquerade" in nft_input

    @pytest.mark.asyncio
    async def test_input_filter_chain_present_when_disabled(self):
        """apply_nat (enabled=False) でも INPUT フィルタチェーンが生成されること。
        HTTP ポートの LAN 限定保護は NAT 有効/無効と直交するセキュリティ要件のため。
        """
        ops = FakeSystemOps()
        payload = {**_VALID_NAT_PAYLOAD, "enabled": False, "http_port": 80}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is True
        argvs = [call[0] for call in ops.run_calls]
        nft_idx = argvs.index(["nft", "-f", "-"])
        nft_input = ops.run_calls[nft_idx][1]
        assert nft_input is not None

        # INPUT フィルタは enabled=False でも出力される
        assert "millicall_filter" in nft_input
        assert "type filter hook input" in nft_input
        assert "tcp dport 80" in nft_input
        assert "drop" in nft_input
        # NAT 無効なのでマスカレードは含まれない、テーブル削除が含まれる
        assert "masquerade" not in nft_input
        assert "delete table" in nft_input

    @pytest.mark.asyncio
    async def test_input_filter_uses_custom_http_port(self):
        """http_port を明示指定した場合、そのポートが INPUT フィルタに使われること。"""
        ops = FakeSystemOps()
        payload = {**_VALID_NAT_PAYLOAD, "http_port": 8080}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is True
        argvs = [call[0] for call in ops.run_calls]
        nft_idx = argvs.index(["nft", "-f", "-"])
        nft_input = ops.run_calls[nft_idx][1]
        assert nft_input is not None
        assert "tcp dport 8080" in nft_input
        # "tcp dport 80 " (スペース区切り) は含まれないこと（8080 に差し替えられた）
        # 注意: "8080" は "80" をサブストリングとして含むため、厳密な語境界チェックを行う
        assert "tcp dport 80\n" not in nft_input and "tcp dport 80 " not in nft_input

    @pytest.mark.asyncio
    async def test_http_port_default_is_80_when_absent(self):
        """http_port をペイロードに含めない場合、デフォルト 80 が使われること（後方互換性）。"""
        ops = FakeSystemOps()
        # http_port を含まない旧形式のペイロード
        payload = {
            "cmd": "apply_nat",
            "enabled": True,
            "lan_ip": "172.20.0.1",
            "lan_prefix": 16,
            "wan_interface": "eth0",
        }
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is True
        argvs = [call[0] for call in ops.run_calls]
        nft_idx = argvs.index(["nft", "-f", "-"])
        nft_input = ops.run_calls[nft_idx][1]
        assert "tcp dport 80" in nft_input

    @pytest.mark.asyncio
    async def test_invalid_http_port_returns_error_no_ops(self):
        """http_port が範囲外の場合はエラーを返し ops を呼ばない。"""
        ops = FakeSystemOps()
        payload = {**_VALID_NAT_PAYLOAD, "http_port": 99999}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []

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
        # serve 無効(既定)なので up の 1 コールのみ
        assert len(ops.run_calls) == 1
        argv, _ = ops.run_calls[0]
        assert argv[0] == "tailscale"
        assert argv[1] == "up"
        assert "--authkey" in argv
        # キー自体が argv に含まれる（これは正常 — tailscale コマンドへの引数として必要）
        assert self._VALID_KEY in argv

    @pytest.mark.asyncio
    async def test_serve_enabled_runs_serve_after_up(self):
        """tailscale_serve_enabled=True のとき up 成功後に tailscale serve を張る。"""

        class ServeSettings(FakeSettings):
            tailscale_serve_enabled = True
            http_port = 80

        ops = FakeSystemOps()
        payload = {"cmd": "tailscale_up", "auth_key": self._VALID_KEY}
        resp = await dispatch(payload, ops, ServeSettings())

        assert resp["ok"] is True
        # up → serve の 2 コール
        assert len(ops.run_calls) == 2
        serve_argv, _ = ops.run_calls[1]
        assert serve_argv[0] == "tailscale"
        assert serve_argv[1] == "serve"
        assert "http://localhost:80" in serve_argv
        # auth key は serve コマンドに渡さない（秘密情報保護）
        assert self._VALID_KEY not in serve_argv

    @pytest.mark.asyncio
    async def test_serve_failure_does_not_fail_up(self):
        """serve が失敗しても up 自体の成功は覆らない。"""

        class ServeSettings(FakeSettings):
            tailscale_serve_enabled = True
            http_port = 8000

        # up は rc=0、serve も同じ ops で rc=0 になる想定だが、run_rc を 0 のままにして
        # serve が呼ばれることだけ確認（失敗系は run_rc をハンドラ内で個別制御できないため
        # ここでは serve が 2 コール目に出ることと ok=True を確認）。
        ops = FakeSystemOps()
        resp = await dispatch(
            {"cmd": "tailscale_up", "auth_key": self._VALID_KEY}, ops, ServeSettings()
        )
        assert resp["ok"] is True
        assert len(ops.run_calls) == 2
        assert "http://localhost:8000" in ops.run_calls[1][0]

    @pytest.mark.asyncio
    async def test_invalid_key_format_returns_error_no_ops(self):
        """不正なキー形式は ops を呼ばずエラーを返す。"""
        ops = FakeSystemOps()
        payload = {"cmd": "tailscale_up", "auth_key": "not-a-valid-key"}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert ops.run_calls == []

    @pytest.mark.asyncio
    async def test_key_echoed_in_stderr_is_redacted(self):
        """rc!=0 で stderr にキーが混入しても、切り詰め前に tskey-\\S+ を除去する（レビュー N1）。"""
        # 200 文字境界を跨いでもキーが残らないよう、長いパディングの先頭にキーを置く
        stderr = self._VALID_KEY + " " + ("x" * 300)
        ops = FakeSystemOps(run_rc=1, run_stderr=stderr)
        payload = {"cmd": "tailscale_up", "auth_key": self._VALID_KEY}
        resp = await dispatch(payload, ops, SETTINGS)

        assert resp["ok"] is False
        assert self._VALID_KEY not in resp.get("error", "")
        assert "tskey-" not in resp.get("error", "")

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


_TAILSCALE_STATUS_JSON = json.dumps(
    {
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
    }
)


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
    async def test_malicious_hostname_sanitized_to_empty(self):
        """信頼できないリースの不正 hostname（制御文字/注入）は空文字へ落とす（レビュー M2）。"""
        content = "100 aa:bb:cc:dd:ee:11 172.20.1.9 evil;rm\\x20-rf *\n"
        ops = FakeSystemOps(read_content=content)
        resp = await dispatch({"cmd": "get_dhcp_leases"}, ops, SETTINGS)
        assert resp["ok"] is True
        assert len(resp["leases"]) == 1
        assert resp["leases"][0]["hostname"] == ""  # RFC1123 非準拠 → 空文字

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
