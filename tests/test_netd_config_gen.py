"""netd/config_gen.py のユニットテスト。

副作用なし — 純粋関数のみのテスト。
"""

import pytest

from millicall.netd.config_gen import render_dnsmasq_conf, render_nftables_ruleset

# ---------------------------------------------------------------------------
# render_dnsmasq_conf
# ---------------------------------------------------------------------------


class TestRenderDnsmasqConf:
    """render_dnsmasq_conf のテスト。"""

    _VALID_KWARGS = dict(
        lan_interface="enp3s0",
        lan_ip="172.20.0.1",
        dhcp_range_start="172.20.1.1",
        dhcp_range_end="172.20.254.254",
        dhcp_lease_hours=12,
        provisioning_url="http://172.20.0.1:8000/provisioning/",
        lan_prefix=16,
    )

    def test_contains_interface_line(self):
        conf = render_dnsmasq_conf(**self._VALID_KWARGS)
        assert "interface=enp3s0" in conf

    def test_contains_bind_interfaces(self):
        conf = render_dnsmasq_conf(**self._VALID_KWARGS)
        assert "bind-interfaces" in conf

    def test_contains_dhcp_range(self):
        conf = render_dnsmasq_conf(**self._VALID_KWARGS)
        assert "dhcp-range=172.20.1.1,172.20.254.254,255.255.0.0,12h" in conf

    def test_contains_gateway_option(self):
        conf = render_dnsmasq_conf(**self._VALID_KWARGS)
        assert "dhcp-option=3,172.20.0.1" in conf

    def test_contains_dns_option(self):
        conf = render_dnsmasq_conf(**self._VALID_KWARGS)
        assert "dhcp-option=6,172.20.0.1" in conf

    def test_contains_provisioning_url_option(self):
        conf = render_dnsmasq_conf(**self._VALID_KWARGS)
        assert "dhcp-option=66,http://172.20.0.1:8000/provisioning/" in conf

    def test_provisioning_url_without_port_accepted(self):
        # HTTP ポート 80 のとき core は http_port_suffix でポートを省略した
        # http://<lan_ip>/provisioning/ を生成する。これが検証を通り、
        # そのまま dhcp-option=66 に載ること（ポート必須にしていた回帰の防止）。
        kwargs = {**self._VALID_KWARGS, "provisioning_url": "http://172.20.0.1/provisioning/"}
        conf = render_dnsmasq_conf(**kwargs)
        assert "dhcp-option=66,http://172.20.0.1/provisioning/" in conf

    def test_provisioning_url_host_only_no_path_no_port_accepted(self):
        kwargs = {**self._VALID_KWARGS, "provisioning_url": "http://172.20.0.1/"}
        conf = render_dnsmasq_conf(**kwargs)
        assert "dhcp-option=66,http://172.20.0.1/" in conf

    def test_ends_with_newline(self):
        conf = render_dnsmasq_conf(**self._VALID_KWARGS)
        assert conf.endswith("\n")

    def test_prefix24_netmask(self):
        kwargs = {**self._VALID_KWARGS, "lan_prefix": 24}
        conf = render_dnsmasq_conf(**kwargs)
        assert "255.255.255.0" in conf

    def test_prefix8_netmask(self):
        kwargs = {**self._VALID_KWARGS, "lan_prefix": 8}
        conf = render_dnsmasq_conf(**kwargs)
        assert "255.0.0.0" in conf

    # --- エラーケース: インターフェイス名 ---

    def test_invalid_interface_space(self):
        kwargs = {**self._VALID_KWARGS, "lan_interface": "eth 0"}
        with pytest.raises(ValueError, match="インターフェイス"):
            render_dnsmasq_conf(**kwargs)

    def test_invalid_interface_semicolon(self):
        kwargs = {**self._VALID_KWARGS, "lan_interface": "eth0;rm -rf /"}
        with pytest.raises(ValueError, match="インターフェイス"):
            render_dnsmasq_conf(**kwargs)

    def test_invalid_interface_newline(self):
        kwargs = {**self._VALID_KWARGS, "lan_interface": "eth0\nrm -rf /"}
        with pytest.raises(ValueError, match="インターフェイス"):
            render_dnsmasq_conf(**kwargs)

    def test_invalid_interface_too_long(self):
        kwargs = {**self._VALID_KWARGS, "lan_interface": "a" * 16}
        with pytest.raises(ValueError, match="インターフェイス"):
            render_dnsmasq_conf(**kwargs)

    # --- エラーケース: IP アドレス ---

    def test_invalid_lan_ip(self):
        kwargs = {**self._VALID_KWARGS, "lan_ip": "not-an-ip"}
        with pytest.raises(ValueError):
            render_dnsmasq_conf(**kwargs)

    def test_invalid_dhcp_range_start(self):
        kwargs = {**self._VALID_KWARGS, "dhcp_range_start": "999.0.0.1"}
        with pytest.raises(ValueError):
            render_dnsmasq_conf(**kwargs)

    def test_invalid_dhcp_range_reversed(self):
        """開始 IP が終了 IP より大きい場合はエラー。"""
        kwargs = {
            **self._VALID_KWARGS,
            "dhcp_range_start": "172.20.254.254",
            "dhcp_range_end": "172.20.1.1",
        }
        with pytest.raises(ValueError):
            render_dnsmasq_conf(**kwargs)

    # --- エラーケース: provisioning_url ---

    def test_invalid_url_newline(self):
        """改行を含む URL はインジェクション攻撃を招くため拒否。"""
        kwargs = {
            **self._VALID_KWARGS,
            "provisioning_url": "http://172.20.0.1:8000/provisioning/\nmalicious=value",
        }
        with pytest.raises(ValueError):
            render_dnsmasq_conf(**kwargs)

    def test_invalid_url_space(self):
        """空白を含む URL は拒否。"""
        kwargs = {
            **self._VALID_KWARGS,
            "provisioning_url": "http://172.20.0.1:8000/provisioning/ evil",
        }
        with pytest.raises(ValueError):
            render_dnsmasq_conf(**kwargs)

    def test_invalid_url_wrong_host(self):
        """URL のホストが lan_ip と一致しない場合は拒否。"""
        kwargs = {
            **self._VALID_KWARGS,
            "provisioning_url": "http://192.168.1.1:8000/provisioning/",
        }
        with pytest.raises(ValueError, match="lan_ip"):
            render_dnsmasq_conf(**kwargs)

    def test_invalid_url_https_scheme(self):
        """https スキームは拒否（LAN 内 http のみ許可）。"""
        kwargs = {
            **self._VALID_KWARGS,
            "provisioning_url": "https://172.20.0.1:8000/provisioning/",
        }
        with pytest.raises(ValueError):
            render_dnsmasq_conf(**kwargs)

    def test_invalid_url_shell_metachar(self):
        """シェルメタ文字を含む URL は拒否。"""
        kwargs = {
            **self._VALID_KWARGS,
            "provisioning_url": "http://172.20.0.1:8000/provisioning/;rm${IFS}-rf${IFS}/",
        }
        with pytest.raises(ValueError):
            render_dnsmasq_conf(**kwargs)

    def test_invalid_url_backtick(self):
        """バッククォートを含む URL は拒否。"""
        kwargs = {
            **self._VALID_KWARGS,
            "provisioning_url": "http://172.20.0.1:8000/`evil`",
        }
        with pytest.raises(ValueError):
            render_dnsmasq_conf(**kwargs)

    def test_invalid_dhcp_lease_hours_zero(self):
        """dhcp_lease_hours が 0 以下の場合はエラー。"""
        kwargs = {**self._VALID_KWARGS, "dhcp_lease_hours": 0}
        with pytest.raises(ValueError):
            render_dnsmasq_conf(**kwargs)

    def test_invalid_cidr_prefix_out_of_range(self):
        kwargs = {**self._VALID_KWARGS, "lan_prefix": 33}
        with pytest.raises(ValueError):
            render_dnsmasq_conf(**kwargs)


# ---------------------------------------------------------------------------
# render_nftables_ruleset
# ---------------------------------------------------------------------------


class TestRenderNftablesRuleset:
    """render_nftables_ruleset のテスト。"""

    _VALID_KWARGS = dict(
        enabled=True,
        lan_ip="172.20.0.1",
        lan_prefix=16,
        wan_interface="eth0",
        table_name="millicall_nat",
    )

    def test_enabled_contains_table(self):
        ruleset = render_nftables_ruleset(**self._VALID_KWARGS)
        assert "table ip millicall_nat" in ruleset

    def test_enabled_contains_masquerade(self):
        ruleset = render_nftables_ruleset(**self._VALID_KWARGS)
        assert "masquerade" in ruleset

    def test_enabled_contains_cidr(self):
        ruleset = render_nftables_ruleset(**self._VALID_KWARGS)
        # ネットワークアドレスに正規化されていることを確認
        assert "172.20.0.0/16" in ruleset

    def test_enabled_contains_wan_interface(self):
        ruleset = render_nftables_ruleset(**self._VALID_KWARGS)
        assert "eth0" in ruleset

    def test_wan_interface_double_quoted_not_single(self):
        # nftables はシングルクォートを受け付けない。インターフェイス名は
        # ダブルクォートで囲む必要がある（Python repr の 'eth0' は構文エラーになる）。
        ruleset = render_nftables_ruleset(**self._VALID_KWARGS)
        assert 'oif "eth0" masquerade' in ruleset
        assert 'iif "eth0"' in ruleset
        assert "'eth0'" not in ruleset

    def test_enabled_has_postrouting_chain(self):
        ruleset = render_nftables_ruleset(**self._VALID_KWARGS)
        assert "postrouting" in ruleset

    def test_disabled_contains_delete_table(self):
        kwargs = {**self._VALID_KWARGS, "enabled": False}
        ruleset = render_nftables_ruleset(**kwargs)
        assert "delete table" in ruleset
        assert "millicall_nat" in ruleset

    def test_disabled_no_masquerade(self):
        kwargs = {**self._VALID_KWARGS, "enabled": False}
        ruleset = render_nftables_ruleset(**kwargs)
        assert "masquerade" not in ruleset

    def test_ends_with_newline(self):
        ruleset = render_nftables_ruleset(**self._VALID_KWARGS)
        assert ruleset.endswith("\n")

    def test_custom_table_name(self):
        kwargs = {**self._VALID_KWARGS, "table_name": "my_nat_table"}
        ruleset = render_nftables_ruleset(**kwargs)
        assert "my_nat_table" in ruleset

    # --- エラーケース ---

    def test_invalid_lan_ip(self):
        kwargs = {**self._VALID_KWARGS, "lan_ip": "not-an-ip"}
        with pytest.raises(ValueError):
            render_nftables_ruleset(**kwargs)

    def test_invalid_lan_prefix(self):
        kwargs = {**self._VALID_KWARGS, "lan_prefix": 33}
        with pytest.raises(ValueError):
            render_nftables_ruleset(**kwargs)

    def test_invalid_wan_interface_space(self):
        kwargs = {**self._VALID_KWARGS, "wan_interface": "eth 0"}
        with pytest.raises(ValueError, match="WAN インターフェイス"):
            render_nftables_ruleset(**kwargs)

    def test_invalid_wan_interface_semicolon(self):
        kwargs = {**self._VALID_KWARGS, "wan_interface": "eth0;evil"}
        with pytest.raises(ValueError, match="WAN インターフェイス"):
            render_nftables_ruleset(**kwargs)

    def test_invalid_wan_interface_newline(self):
        kwargs = {**self._VALID_KWARGS, "wan_interface": "eth0\nrm -rf /"}
        with pytest.raises(ValueError, match="WAN インターフェイス"):
            render_nftables_ruleset(**kwargs)

    def test_cidr_is_network_address(self):
        """lan_ip がホストアドレスでも、ネットワーク CIDR を使用することを確認。"""
        kwargs = {**self._VALID_KWARGS, "lan_ip": "172.20.5.1", "lan_prefix": 16}
        ruleset = render_nftables_ruleset(**kwargs)
        # ネットワークアドレス（172.20.0.0/16）に正規化される
        assert "172.20.0.0/16" in ruleset
        # ホストアドレス（172.20.5.1）はルールセットに直接含まれない
        assert "172.20.5.1" not in ruleset
