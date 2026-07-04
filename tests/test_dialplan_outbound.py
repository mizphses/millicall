import defusedxml.ElementTree as ET  # noqa: N817

from millicall.telephony.fsconfig import ExtensionConfig, FreeswitchConfigWriter, TrunkConfig


def _writer(tmp_path, allow=None):
    return FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="192.168.1.10",
        esl_password="esl",
        international_allow_prefixes=allow,
    )


def _trunk():
    return TrunkConfig(
        name="hgw", display_name="HGW", host="192.168.1.1",
        username="0312345678", password="pw", caller_id="0398765432",
    )


def test_outbound_bridges_to_gateway_with_callerid(tmp_path):
    w = _writer(tmp_path)
    w.write_all([ExtensionConfig("1001", "A", "pw")], trunks=[_trunk()])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    ET.fromstring(dp)
    # 内線はそのまま
    assert "user/${destination_number}@192.168.1.10" in dp
    # 外線: 0始まり → gateway bridge
    assert "sofia/gateway/hgw/${destination_number}" in dp
    assert 'expression="^(0\\d+)$"' in dp
    # 発信者番号 = 表示番号
    assert "effective_caller_id_number=0398765432" in dp


def test_international_blocked_by_default(tmp_path):
    w = _writer(tmp_path)
    w.write_all([], trunks=[_trunk()])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    assert 'name="outbound_intl_block"' in dp
    assert "010\\d+" in dp
    assert 'data="CALL_REJECTED"' in dp
    # allowlist が空なので allow 拡張は無い
    assert "outbound_intl_allow_" not in dp


def test_international_allowlist_creates_allow_extension(tmp_path):
    w = _writer(tmp_path, allow=["010"])
    w.write_all([], trunks=[_trunk()])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    assert 'name="outbound_intl_allow_010"' in dp
    assert 'expression="^(010\\d+)$"' in dp


def test_no_outbound_extension_without_trunk(tmp_path):
    w = _writer(tmp_path)
    w.write_all([ExtensionConfig("1001", "A", "pw")])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    assert "sofia/gateway/" not in dp
    assert 'name="outbound_intl_block"' not in dp
