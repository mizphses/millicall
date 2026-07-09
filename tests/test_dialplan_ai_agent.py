from pathlib import Path

import defusedxml.ElementTree as ET  # noqa: N817

from millicall.telephony.fsconfig import AiAgentConfig, FreeswitchConfigWriter


def test_default_dialplan_parks_for_ai_agent(tmp_path: Path):
    """内線番号を持つ AI エージェントは default コンテキストで answer→park する。"""
    writer = FreeswitchConfigWriter(
        output_dir=tmp_path, sip_domain="millicall.local", esl_password="x"
    )
    writer.write_all(
        extensions=[],
        trunks=[],
        ai_agents=[AiAgentConfig(number="600", agent_id=7)],
    )
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    ET.fromstring(dp)
    assert 'name="ai_agent_600"' in dp
    # dialplan は answer→park までとし、mod_audio_stream の起動は core が ESL で行う。
    # agent id はチャネル変数として設定し、core が CHANNEL_ANSWER イベントで読み取る。
    assert "millicall_ai_agent=7" in dp
    # verbose_events が無いと FS は CHANNEL_ANSWER に variable_* を含めず
    # core が AI 起動できない（実機で無音着信として発現した必須設定）
    assert '<action application="set" data="verbose_events=true"/>' in dp
    assert "answer" in dp
    assert "park" in dp
