from pathlib import Path

import defusedxml.ElementTree as ET  # noqa: N817

from millicall.telephony.fsconfig import FreeswitchConfigWriter, RouteConfig


def test_public_dialplan_parks_for_ai_agent(tmp_path: Path):
    writer = FreeswitchConfigWriter(
        output_dir=tmp_path, sip_domain="millicall.local", esl_password="x"
    )
    writer.write_all(
        extensions=[],
        trunks=[],
        routes=[RouteConfig(match_number="0312345678", target_type="ai_agent", target_value="7")],
    )
    public = (tmp_path / "dialplan" / "public.xml").read_text()
    # XML として妥当であること
    ET.fromstring(public)
    assert "0312345678" in public
    # dialplan は answer→park までとし、mod_audio_stream の起動は core が ESL で行う。
    # agent id はチャネル変数として設定し、core が CHANNEL_ANSWER イベントで読み取る。
    assert "millicall_ai_agent=7" in public
    # verbose_events が無いと FS は CHANNEL_ANSWER に variable_* を含めず
    # core が AI 起動できない（実機で無音着信として発現した必須設定）
    assert '<action application="set" data="verbose_events=true"/>' in public
    assert "answer" in public
    assert "park" in public
    # ai_agent は内線への bridge を行わない
    assert "bridge" not in public
