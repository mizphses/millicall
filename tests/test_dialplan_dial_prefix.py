"""発信プレフィクスで発信トランクを選ぶダイヤルプラン（issue #139）のテスト。

生成した default.xml を FreeSWITCH の XML ダイヤルプランと同じ規則（extension を上から
評価し、最初に destination_number 条件が一致したもの）で簡易評価し、
どのトランク・どの番号・どの発信者番号で bridge されるかを確かめる。
"""

import re

import defusedxml.ElementTree as ET  # noqa: N817
import pytest

from millicall.telephony.fsconfig import ExtensionConfig, FreeswitchConfigWriter, TrunkConfig


def _writer(tmp_path, allow=None, default_id=None):
    return FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="192.168.1.10",
        esl_password="esl",
        international_allow_prefixes=allow,
        default_outbound_trunk_id=default_id,
    )


def _trunk(name, tid, caller_id, *, prefix="", trunk_type="hgw", outbound=True):
    return TrunkConfig(
        name=name,
        display_name=name,
        host="192.168.1.1",
        username="u",
        password="pw",
        caller_id=caller_id,
        trunk_type=trunk_type,
        id=tid,
        dial_prefix=prefix,
        outbound_enabled=outbound,
    )


def _trunks():
    return [
        _trunk("Flets01", 1, "0445895782"),
        _trunk("Flets02", 2, "0445895784", prefix="92"),
        _trunk("brastel", 3, "05068650165", prefix="*3", trunk_type="sip"),
    ]


def _render(tmp_path, trunks, **kw) -> str:
    w = _writer(tmp_path, **kw)
    return w.render_all([ExtensionConfig("1001", "A", "pw")], trunks)["dialplan/default.xml"]


def _expand(data: str, dest: str, permission: str) -> str:
    def sub(m: re.Match) -> str:
        name, offset = m.group(1), m.group(2)
        value = {"destination_number": dest, "calling_permission": permission}[name]
        return value[int(offset) :] if offset else value

    return re.sub(r"\$\{(destination_number|calling_permission)(?::(\d+))?\}", sub, data)


def _route(xml: str, dest: str, permission: str = "domestic") -> dict:
    """dest をダイヤルしたときに実行されるアクションを返す（簡易 FreeSWITCH 評価）。"""
    root = ET.fromstring(xml)
    for ext in root.iter("extension"):
        cond = ext.find("condition")
        if cond is None or cond.get("field") != "destination_number":
            continue
        if not re.search(cond.get("expression"), dest):
            continue
        inner = cond.find("condition")
        if inner is None:
            actions = cond.findall("action")
        elif re.search(inner.get("expression"), permission):
            actions = inner.findall("action")
        else:
            actions = inner.findall("anti-action")
        result = {"extension": ext.get("name"), "bridge": None, "caller_id": None,
                  "hangup": None}
        for a in actions:
            app, data = a.get("application"), _expand(a.get("data") or "", dest, permission)
            if app == "bridge":
                result["bridge"] = data
            elif app == "set" and data.startswith("effective_caller_id_number="):
                result["caller_id"] = data.split("=", 1)[1]
            elif app == "hangup" and result["hangup"] is None and result["bridge"] is None:
                result["hangup"] = data
        return result
    return {"extension": None, "bridge": None, "caller_id": None, "hangup": None}


def test_no_prefix_uses_default_trunk_name_order(tmp_path):
    dp = _render(tmp_path, _trunks())
    r = _route(dp, "0312345678")
    assert r["extension"] == "outbound_external"
    assert r["bridge"] == "{absolute_codec_string=PCMU}sofia/gateway/Flets01/0312345678"
    assert r["caller_id"] == "0445895782"


def test_prefix_selects_trunk_and_strips_prefix(tmp_path):
    dp = _render(tmp_path, _trunks())
    r = _route(dp, "9207089372750")
    assert r["extension"] == "outbound_external_via_Flets02"
    # hgw なので PCMU 固定、プレフィクス 92 を剥がした番号を渡す
    assert r["bridge"] == "{absolute_codec_string=PCMU}sofia/gateway/Flets02/07089372750"
    assert r["caller_id"] == "0445895784"


def test_prefix_with_186_passes_through_to_carrier(tmp_path):
    dp = _render(tmp_path, _trunks())
    r = _route(dp, "9218607089372750")
    assert r["bridge"] == "{absolute_codec_string=PCMU}sofia/gateway/Flets02/18607089372750"
    r = _route(dp, "18407089372750")
    assert r["bridge"] == "{absolute_codec_string=PCMU}sofia/gateway/Flets01/18407089372750"


def test_star_prefix_is_regex_escaped_and_sip_has_no_codec(tmp_path):
    dp = _render(tmp_path, _trunks())
    ET.fromstring(dp)
    assert 'expression="^\\*3((?:18[46])?0\\d+)$"' in dp
    r = _route(dp, "*305012345678")
    assert r["extension"] == "outbound_external_via_brastel"
    assert r["bridge"] == "sofia/gateway/brastel/05012345678"
    assert r["caller_id"] == "05068650165"
    # * がエスケープされていなければ「3 の 0 回以上」になり 305… に一致してしまう
    assert _route(dp, "305012345678")["bridge"] is None


def test_hash_prefix(tmp_path):
    trunks = [_trunk("a", 1, "0311111111"), _trunk("b", 2, "0322222222", prefix="#9")]
    dp = _render(tmp_path, trunks)
    r = _route(dp, "#90312345678")
    assert r["bridge"] == "{absolute_codec_string=PCMU}sofia/gateway/b/0312345678"


@pytest.mark.parametrize("dest", ["9207089372750", "0312345678"])
def test_internal_permission_is_rejected(tmp_path, dest):
    dp = _render(tmp_path, _trunks())
    r = _route(dp, dest, permission="internal")
    assert r["bridge"] is None
    assert r["hangup"] == "CALL_REJECTED"


@pytest.mark.parametrize("dest", ["92010123456789", "920012345678", "92186010123456789"])
def test_prefixed_international_is_blocked_without_allowlist(tmp_path, dest):
    dp = _render(tmp_path, _trunks())
    r = _route(dp, dest, permission="international")
    assert r["extension"] == "outbound_intl_block_via_Flets02"
    assert r["bridge"] is None
    assert r["hangup"] == "CALL_REJECTED"


def test_prefixed_international_allowlist_requires_international(tmp_path):
    dp = _render(tmp_path, _trunks(), allow=["0101"])
    r = _route(dp, "9201011234567", permission="international")
    assert r["extension"] == "outbound_intl_allow_0101_via_Flets02"
    assert r["bridge"] == "{absolute_codec_string=PCMU}sofia/gateway/Flets02/01011234567"
    r = _route(dp, "9201011234567", permission="domestic")
    assert r["bridge"] is None
    assert r["hangup"] == "CALL_REJECTED"


def test_default_outbound_trunk_setting(tmp_path):
    dp = _render(tmp_path, _trunks(), default_id=3)
    r = _route(dp, "0312345678")
    assert r["bridge"] == "sofia/gateway/brastel/0312345678"
    assert r["caller_id"] == "05068650165"
    # プレフィクス発信は既定設定に関係なく効く
    assert _route(dp, "920312345678")["bridge"].endswith("gateway/Flets02/0312345678")


@pytest.mark.parametrize("default_id", [99, 1])
def test_default_falls_back_when_missing_or_not_outbound(tmp_path, default_id):
    trunks = [
        _trunk("Flets01", 1, "0445895782", outbound=False),
        _trunk("Flets02", 2, "0445895784", prefix="92"),
        _trunk("brastel", 3, "05068650165", trunk_type="sip"),
    ]
    dp = _render(tmp_path, trunks, default_id=default_id)
    # Flets01 は発信に使わないので、名前順で次の Flets02 が既定になる
    r = _route(dp, "0312345678")
    assert r["bridge"].endswith("sofia/gateway/Flets02/0312345678")
    assert "gateway/Flets01/" not in dp


def test_outbound_disabled_trunk_prefix_is_not_routed(tmp_path):
    trunks = [_trunk("a", 1, "0311111111"), _trunk("b", 2, "0322222222", prefix="92",
                                                   outbound=False)]
    dp = _render(tmp_path, trunks)
    assert "outbound_external_via_b" not in dp
    assert _route(dp, "920312345678")["bridge"] is None


def test_no_outbound_routes_when_all_trunks_inbound_only(tmp_path):
    dp = _render(tmp_path, [_trunk("a", 1, "0311111111", prefix="92", outbound=False)])
    assert "sofia/gateway/" not in dp
    assert 'name="outbound_intl_block"' not in dp


def test_internal_extension_still_wins(tmp_path):
    dp = _render(tmp_path, _trunks())
    assert _route(dp, "1001")["extension"] == "internal_extensions"
