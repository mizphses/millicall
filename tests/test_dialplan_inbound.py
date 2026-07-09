"""統一番号プランのダイヤルプラン生成テスト。

public: トランクごとの着信 → inbound_extension への transfer。
default: グループ一斉鳴動 → AI → ワークフロー → 汎用内線の順。
"""

import defusedxml.ElementTree as ET  # noqa: N817

from millicall.telephony.fsconfig import (
    AiAgentConfig,
    FreeswitchConfigWriter,
    RingGroupConfig,
    TrunkConfig,
    WorkflowConfig,
)


def _writer(tmp_path):
    return FreeswitchConfigWriter(output_dir=tmp_path, sip_domain="192.168.1.10", esl_password="e")


def _trunk(**kw) -> TrunkConfig:
    base = dict(
        name="hgw",
        display_name="HGW",
        host="192.168.1.1",
        username="30",
        password="pw",
        did_number="0312345678",
        inbound_extension="200",
    )
    base.update(kw)
    return TrunkConfig(**base)


# --------------------------------------------------------------------------- #
# public: トランク着信 → transfer
# --------------------------------------------------------------------------- #


def test_inbound_trunk_transfers_to_extension(tmp_path):
    w = _writer(tmp_path)
    w.write_all([], trunks=[_trunk()])
    pub = (tmp_path / "dialplan" / "public.xml").read_text()
    ET.fromstring(pub)
    assert 'name="inbound_trunk_hgw"' in pub
    # username / did_number のどちらでも同じトランクの着信とみなす
    assert 'expression="^(30|0312345678)$"' in pub
    assert '<action application="transfer" data="200 XML default"/>' in pub


def test_inbound_trunk_without_destination_not_rendered(tmp_path):
    w = _writer(tmp_path)
    w.write_all([], trunks=[_trunk(inbound_extension="")])
    pub = (tmp_path / "dialplan" / "public.xml").read_text()
    assert "inbound_trunk_hgw" not in pub


def test_inbound_trunk_same_username_and_did(tmp_path):
    w = _writer(tmp_path)
    w.write_all([], trunks=[_trunk(did_number="30")])
    pub = (tmp_path / "dialplan" / "public.xml").read_text()
    assert 'expression="^(30)$"' in pub


def test_inbound_no_route_hangs_up(tmp_path):
    w = _writer(tmp_path)
    w.write_all([])
    pub = (tmp_path / "dialplan" / "public.xml").read_text()
    assert 'name="inbound_no_route"' in pub
    assert 'data="NO_ROUTE_DESTINATION"' in pub


def test_public_written_even_without_trunks(tmp_path):
    w = _writer(tmp_path)
    w.write_all([])
    assert (tmp_path / "dialplan" / "public.xml").exists()


# --------------------------------------------------------------------------- #
# default: グループ着信（一斉鳴動）
# --------------------------------------------------------------------------- #


def test_ring_group_bridges_all_members(tmp_path):
    w = _writer(tmp_path)
    w.write_all(
        [],
        ring_groups=[
            RingGroupConfig(number="200", name="営業", member_numbers=["1001", "1002", "1003"])
        ],
    )
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    ET.fromstring(dp)
    assert 'name="ring_group_200"' in dp
    # カンマ区切り = 一斉鳴動
    assert (
        "user/1001@192.168.1.10,user/1002@192.168.1.10,user/1003@192.168.1.10" in dp
    )


def test_ring_group_without_members_not_rendered(tmp_path):
    w = _writer(tmp_path)
    w.write_all([], ring_groups=[RingGroupConfig(number="200", name="空", member_numbers=[])])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    assert "ring_group_200" not in dp


# --------------------------------------------------------------------------- #
# default: AI エージェント / ワークフロー（内線番号で park）
# --------------------------------------------------------------------------- #


def test_workflow_number_parks_in_default(tmp_path):
    w = _writer(tmp_path)
    w.write_all([], workflows=[WorkflowConfig(number="300", workflow_id=42)])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    ET.fromstring(dp)
    assert 'name="workflow_300"' in dp
    assert "millicall_workflow=42" in dp
    assert "verbose_events=true" in dp
    assert "ring_ready" not in dp


def test_workflow_ring_count_renders_sleep(tmp_path):
    w = _writer(tmp_path)
    w.write_all([], workflows=[WorkflowConfig(number="300", workflow_id=42, ring_count=3)])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    assert "ring_ready" in dp
    assert 'data="18000"' in dp  # 3 * 6000


def test_number_plan_order_group_ai_workflow_generic(tmp_path):
    """default コンテキストの評価順: グループ → AI → ワークフロー → 汎用内線。"""
    w = _writer(tmp_path)
    w.write_all(
        [],
        ring_groups=[RingGroupConfig(number="200", name="g", member_numbers=["1001"])],
        ai_agents=[AiAgentConfig(number="600", agent_id=7)],
        workflows=[WorkflowConfig(number="300", workflow_id=42)],
    )
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    ET.fromstring(dp)
    i_group = dp.index("ring_group_200")
    i_ai = dp.index("ai_agent_600")
    i_wf = dp.index("workflow_300")
    i_generic = dp.index("internal_extensions")
    assert i_group < i_ai < i_wf < i_generic
