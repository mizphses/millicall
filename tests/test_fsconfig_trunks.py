import defusedxml.ElementTree as ET  # noqa: N817

from millicall.telephony.fsconfig import ExtensionConfig, FreeswitchConfigWriter, TrunkConfig


def _writer(tmp_path):
    return FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="192.168.1.10",
        esl_password="esl-secret",
        external_sip_port=5080,
    )


def test_external_profile_generated_per_trunk(tmp_path):
    w = _writer(tmp_path)
    w.write_all(
        [ExtensionConfig("1001", "Alice", "pw")],
        trunks=[
            TrunkConfig(
                name="hgw",
                display_name="HGW",
                host="192.168.1.1",
                username="0312345678",
                password="hgwpw",
                did_number="0312345678",
                caller_id="0312345678",
            )
        ],
    )
    ext = (tmp_path / "sip_profiles" / "external.xml").read_text()
    ET.fromstring(ext)  # well-formed
    assert 'name="external"' in ext
    assert 'gateway name="hgw"' in ext
    assert 'value="192.168.1.1"' in ext  # realm/proxy = HGW IP
    assert 'value="0312345678"' in ext  # username
    assert 'value="hgwpw"' in ext  # password (設定ファイルには実値が要る)
    assert 'value="5080"' in ext  # external sip-port
    assert 'value="public"' in ext  # 着信 context


def test_external_profile_written_even_without_trunks(tmp_path):
    w = _writer(tmp_path)
    w.write_all([ExtensionConfig("1001", "Alice", "pw")])
    ext = tmp_path / "sip_profiles" / "external.xml"
    assert ext.exists()
    assert "gateway name=" not in ext.read_text()  # gateway 無しの空プロファイル


def test_trunk_password_not_in_repr():
    cfg = TrunkConfig(
        name="hgw", display_name="HGW", host="h", username="u", password="topsecret"
    )
    assert "topsecret" not in repr(cfg)


def test_trunk_fields_escaped_in_external_xml(tmp_path):
    """XML special characters in trunk host, username, password must be entity-escaped."""
    w = _writer(tmp_path)
    w.write_all(
        [ExtensionConfig("1001", "Alice", "pw")],
        trunks=[
            TrunkConfig(
                name="evil",
                display_name="Evil Gateway",
                host='hgw & <evil>"',
                username="u&1",
                password="p<w>",
                did_number="0312345678",
                caller_id="0312345678",
            )
        ],
    )
    ext = (tmp_path / "sip_profiles" / "external.xml").read_text()
    assert "hgw &amp; &lt;evil&gt;" in ext
    assert "u&amp;1" in ext
    assert "p&lt;w&gt;" in ext
    assert "<evil>" not in ext
    assert "u&1" not in ext or "u&amp;1" in ext
    ET.fromstring(ext)  # raises if XML is not well-formed
