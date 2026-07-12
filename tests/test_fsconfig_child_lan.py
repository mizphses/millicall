"""子LAN（netd DHCP ネットワーク）適用時の internal プロファイル切り替えテスト。

子LAN 配下の SIP 電話機は ZTP で sip_server=network_config.lan_ip（子LAN GW IP）を
受け取り `<ext>@<lan_ip>` として REGISTER する。子ネットワーク適用時は internal の
バインドIP／ドメインと directory・dialplan のドメインを子LAN GW IP に揃える必要がある。
external プロファイルは HGW 互換のため常に sip_bind_ip（上流IP）を使う。
"""

import defusedxml.ElementTree as ET  # noqa: N817

from millicall.telephony.fsconfig import ExtensionConfig, FreeswitchConfigWriter, TrunkConfig


def _read(tmp_path, rel):
    return (tmp_path / rel).read_text()


# --- Writer レベル: internal_bind_ip / internal_domain パラメータ ---


def test_internal_bind_ip_and_domain_used_when_child_lan_applied(tmp_path) -> None:
    """子LAN適用時: internal の sip-ip/rtp-ip とドメインが子LAN GW IP になる。"""
    writer = FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="192.168.1.2",  # 上流IP（従来のドメイン）
        esl_password="secret",
        sip_bind_ip="192.168.1.2",  # 上流IP（external 用）
        internal_bind_ip="172.20.0.1",  # 子LAN GW IP
        internal_domain="172.20.0.1",
    )
    writer.write_all([ExtensionConfig("4096", "Phone", "pw-4096")])

    internal = _read(tmp_path, "sip_profiles/internal.xml")
    assert '<param name="sip-ip" value="172.20.0.1"/>' in internal
    assert '<param name="rtp-ip" value="172.20.0.1"/>' in internal
    assert 'name="172.20.0.1"' in internal  # <domain>
    # 上流IP は internal には現れない
    assert "192.168.1.2" not in internal

    directory = _read(tmp_path, "directory/default.xml")
    assert '<domain name="172.20.0.1">' in directory

    dialplan = _read(tmp_path, "dialplan/default.xml")
    assert "user/${destination_number}@172.20.0.1" in dialplan
    assert "@192.168.1.2" not in dialplan


def test_internal_falls_back_to_sip_bind_ip_and_domain_when_child_lan_none(tmp_path) -> None:
    """子LAN未適用時: 従来どおり sip_bind_ip / sip_domain が使われる（回帰なし）。"""
    writer = FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="192.168.1.2",
        esl_password="secret",
        sip_bind_ip="192.168.1.2",
        internal_bind_ip=None,
        internal_domain=None,
    )
    writer.write_all([ExtensionConfig("4096", "Phone", "pw-4096")])

    internal = _read(tmp_path, "sip_profiles/internal.xml")
    assert '<param name="sip-ip" value="192.168.1.2"/>' in internal
    assert '<param name="rtp-ip" value="192.168.1.2"/>' in internal
    assert 'name="192.168.1.2"' in internal

    directory = _read(tmp_path, "directory/default.xml")
    assert '<domain name="192.168.1.2">' in directory

    dialplan = _read(tmp_path, "dialplan/default.xml")
    assert "user/${destination_number}@192.168.1.2" in dialplan


def test_internal_bind_falls_back_to_auto_when_no_bind_ip(tmp_path) -> None:
    """子LAN未適用かつ sip_bind_ip も未設定なら sip-ip/rtp-ip は auto のまま。"""
    writer = FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="millicall.local",
        esl_password="secret",
        sip_ip="auto",
        rtp_ip="auto",
        sip_bind_ip=None,
    )
    writer.write_all([])
    internal = _read(tmp_path, "sip_profiles/internal.xml")
    assert internal.count('value="auto"') == 2


def test_external_trunk_always_uses_sip_bind_ip_regardless_of_child_lan(tmp_path) -> None:
    """external_trunk.xml は子LAN有無に関わらず常に sip_bind_ip（上流IP）を使う。"""
    trunk = TrunkConfig(
        name="hgw",
        display_name="HGW",
        host="192.168.1.1",
        username="30",
        password="pw",
        inbound_extension="4096",
    )
    # 子LAN適用ありで生成しても external には子LAN IP が漏れない
    writer = FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="192.168.1.2",
        esl_password="secret",
        sip_bind_ip="192.168.1.2",
        internal_bind_ip="172.20.0.1",
        internal_domain="172.20.0.1",
    )
    writer.write_all([], trunks=[trunk])
    external = _read(tmp_path, "sip_profiles/external_hgw.xml")
    assert "192.168.1.2" in external
    assert "172.20.0.1" not in external
    ET.fromstring(external)


def test_ring_group_bridge_uses_internal_domain_on_child_lan(tmp_path) -> None:
    """リンググループの user@domain も子LAN適用時は子LAN IP を使う。"""
    from millicall.telephony.fsconfig import RingGroupConfig

    writer = FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="192.168.1.2",
        esl_password="secret",
        sip_bind_ip="192.168.1.2",
        internal_bind_ip="172.20.0.1",
        internal_domain="172.20.0.1",
    )
    writer.write_all(
        [ExtensionConfig("4096", "A", "pw"), ExtensionConfig("4097", "B", "pw")],
        ring_groups=[RingGroupConfig(number="500", name="grp", member_numbers=["4096", "4097"])],
    )
    dialplan = _read(tmp_path, "dialplan/default.xml")
    assert "user/4096@172.20.0.1" in dialplan
    assert "user/4097@172.20.0.1" in dialplan
    assert "@192.168.1.2" not in dialplan


def test_set_internal_network_updates_after_construction(tmp_path) -> None:
    """set_internal_network で __init__ 後に子LAN値を差し替えられる（regenerate 経路）。"""
    writer = FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="192.168.1.2",
        esl_password="secret",
        sip_bind_ip="192.168.1.2",
    )
    # 未適用相当（None）ではフォールバック
    writer.set_internal_network(None, None)
    writer.write_all([])
    internal = _read(tmp_path, "sip_profiles/internal.xml")
    assert '<param name="sip-ip" value="192.168.1.2"/>' in internal

    # 適用相当（子LAN IP）へ切り替え
    writer.set_internal_network("172.20.0.1", "172.20.0.1")
    writer.write_all([])
    internal = _read(tmp_path, "sip_profiles/internal.xml")
    assert '<param name="sip-ip" value="172.20.0.1"/>' in internal
    assert 'name="172.20.0.1"' in internal
