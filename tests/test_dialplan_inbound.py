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


# --------------------------------------------------------------------------- #
# Task 9: workflow ルートのダイヤルプランレンダリングテスト
# --------------------------------------------------------------------------- #


def test_inbound_workflow_route_renders_correctly(tmp_path):
    w = _writer(tmp_path)
    w.write_all(
        [],
        routes=[RouteConfig(match_number="0312345678", target_type="workflow", target_value="42")],
    )
    pub = (tmp_path / "dialplan" / "public.xml").read_text()
    ET.fromstring(pub)
    assert 'name="inbound_wf_0312345678"' in pub
    assert "millicall_workflow=42" in pub
    assert "verbose_events=true" in pub
    assert "ring_ready" not in pub  # ring_count=0, ring_ready なし
    assert 'data="0"' not in pub    # sleep data=0 も生成されない


def test_inbound_workflow_route_with_ring_count(tmp_path):
    w = _writer(tmp_path)
    w.write_all(
        [],
        routes=[
            RouteConfig(
                match_number="0312345678",
                target_type="workflow",
                target_value="42",
                ring_count=3,
            )
        ],
    )
    pub = (tmp_path / "dialplan" / "public.xml").read_text()
    assert "ring_ready" in pub
    assert 'data="18000"' in pub  # 3 * 6000 = 18000


def test_inbound_workflow_route_valid_xml(tmp_path):
    w = _writer(tmp_path)
    w.write_all(
        [],
        routes=[
            RouteConfig(
                match_number="0312345678",
                target_type="workflow",
                target_value="42",
                ring_count=2,
            )
        ],
    )
    pub = (tmp_path / "dialplan" / "public.xml").read_text()
    ET.fromstring(pub)  # 整形式 XML であることを確認
