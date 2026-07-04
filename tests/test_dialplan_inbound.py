import defusedxml.ElementTree as ET  # noqa: N817

from millicall.telephony.fsconfig import FreeswitchConfigWriter, RouteConfig


def _writer(tmp_path):
    return FreeswitchConfigWriter(output_dir=tmp_path, sip_domain="192.168.1.10", esl_password="e")


def test_inbound_route_bridges_to_extension(tmp_path):
    w = _writer(tmp_path)
    w.write_all(
        [],
        routes=[RouteConfig(match_number="0312345678", target_type="extension", target_value="1001")],
    )
    pub = (tmp_path / "dialplan" / "public.xml").read_text()
    ET.fromstring(pub)
    assert 'context name="public"' in pub
    assert 'name="inbound_0312345678"' in pub
    assert 'expression="^0312345678$"' in pub
    assert "user/1001@192.168.1.10" in pub


def test_inbound_no_route_hangs_up(tmp_path):
    w = _writer(tmp_path)
    w.write_all([])
    pub = (tmp_path / "dialplan" / "public.xml").read_text()
    assert 'name="inbound_no_route"' in pub
    assert 'data="NO_ROUTE_DESTINATION"' in pub


def test_public_written_even_without_routes(tmp_path):
    w = _writer(tmp_path)
    w.write_all([])
    assert (tmp_path / "dialplan" / "public.xml").exists()
