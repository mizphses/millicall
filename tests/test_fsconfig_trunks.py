import defusedxml.ElementTree as ET  # noqa: N817
import pytest

from millicall.telephony.fsconfig import (
    ExtensionConfig,
    FreeswitchConfigWriter,
    TrunkConfig,
    allocate_source_ports,
    build_reload_commands,
)


def _writer(tmp_path):
    return FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="192.168.1.10",
        esl_password="esl-secret",
        external_sip_port=5080,
    )


def _trunk(name: str, **kw) -> TrunkConfig:
    return TrunkConfig(
        name=name,
        display_name=kw.get("display_name", name.upper()),
        host=kw.get("host", "192.168.1.1"),
        username=kw.get("username", "0312345678"),
        password=kw.get("password", "hgwpw"),
        did_number=kw.get("did_number", "0312345678"),
        caller_id=kw.get("caller_id", "0312345678"),
        source_port=kw.get("source_port"),
    )


def test_external_profile_generated_per_trunk(tmp_path):
    w = _writer(tmp_path)
    w.write_all([ExtensionConfig("1001", "Alice", "pw")], trunks=[_trunk("hgw")])
    ext = (tmp_path / "sip_profiles" / "external_hgw.xml").read_text()
    ET.fromstring(ext)  # well-formed
    assert 'name="external_hgw"' in ext
    assert 'gateway name="hgw"' in ext
    assert 'value="192.168.1.1"' in ext  # realm/proxy = HGW IP
    assert 'value="0312345678"' in ext  # username
    assert 'value="hgwpw"' in ext  # password (設定ファイルには実値が要る)
    assert 'value="5080"' in ext  # 単一トランクは 5080 のまま（後方互換）
    assert 'value="public"' in ext  # 着信 context
    # HGW 互換 settings が維持されていること
    assert 'name="dtmf-type"                  value="none"' in ext
    assert 'name="rfc2833-pt"                 value="0"' in ext
    assert 'name="apply-inbound-acl"          value="millicall_trusted"' in ext
    # 旧単一 external.xml は書かれない
    assert not (tmp_path / "sip_profiles" / "external.xml").exists()


def test_multiple_trunks_get_distinct_source_ports(tmp_path):
    w = _writer(tmp_path)
    w.write_all(
        [ExtensionConfig("1001", "Alice", "pw")],
        trunks=[_trunk("aaa"), _trunk("bbb"), _trunk("ccc")],
    )
    a = (tmp_path / "sip_profiles" / "external_aaa.xml").read_text()
    b = (tmp_path / "sip_profiles" / "external_bbb.xml").read_text()
    c = (tmp_path / "sip_profiles" / "external_ccc.xml").read_text()
    # name 昇順で 5080, 5082, 5084（+2 ずつ）
    assert 'name="sip-port"                   value="5080"' in a
    assert 'name="sip-port"                   value="5082"' in b
    assert 'name="sip-port"                   value="5084"' in c


def test_no_external_profile_when_no_trunks(tmp_path):
    w = _writer(tmp_path)
    w.write_all([ExtensionConfig("1001", "Alice", "pw")])
    profiles = list((tmp_path / "sip_profiles").glob("external*.xml"))
    assert profiles == []


def test_stale_external_profiles_cleaned(tmp_path):
    w = _writer(tmp_path)
    # 1 回目: 旧構成の external.xml と 3 トランクを書いた状態を作る
    (tmp_path / "sip_profiles").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sip_profiles" / "external.xml").write_text("<profile/>")
    w.write_all([ExtensionConfig("1001", "Alice", "pw")], trunks=[_trunk("aaa"), _trunk("bbb")])
    assert (tmp_path / "sip_profiles" / "external_aaa.xml").exists()
    assert (tmp_path / "sip_profiles" / "external_bbb.xml").exists()
    assert not (tmp_path / "sip_profiles" / "external.xml").exists()
    # 2 回目: bbb を削除 → external_bbb.xml が掃除される
    w.write_all([ExtensionConfig("1001", "Alice", "pw")], trunks=[_trunk("aaa")])
    assert (tmp_path / "sip_profiles" / "external_aaa.xml").exists()
    assert not (tmp_path / "sip_profiles" / "external_bbb.xml").exists()


def test_trunk_password_not_in_repr():
    cfg = TrunkConfig(name="hgw", display_name="HGW", host="h", username="u", password="topsecret")
    assert "topsecret" not in repr(cfg)


def test_trunk_fields_escaped_in_external_xml(tmp_path):
    """XML special characters in trunk host, username, password must be entity-escaped."""
    w = _writer(tmp_path)
    w.write_all(
        [ExtensionConfig("1001", "Alice", "pw")],
        trunks=[
            _trunk(
                "evil",
                host='hgw & <evil>"',
                username="u&1",
                password="p<w>",
            )
        ],
    )
    ext = (tmp_path / "sip_profiles" / "external_evil.xml").read_text()
    assert "hgw &amp; &lt;evil&gt;" in ext
    assert "u&amp;1" in ext
    assert "p&lt;w&gt;" in ext
    assert "<evil>" not in ext
    assert "u&1" not in ext or "u&amp;1" in ext
    ET.fromstring(ext)  # raises if XML is not well-formed


def test_write_all_rejects_unsafe_trunk_name(tmp_path):
    w = _writer(tmp_path)
    bad = TrunkConfig(
        name="../../etc/passwd",
        display_name="bad",
        host="h",
        username="u",
        password="p",
    )
    with pytest.raises(ValueError):
        w.write_all([ExtensionConfig("1001", "Alice", "pw")], trunks=[bad])


# --- allocate_source_ports（純関数） ---


def test_allocate_single_trunk_keeps_5080():
    ports = allocate_source_ports([_trunk("hgw")])
    assert ports == {"hgw": 5080}


def test_allocate_auto_increments_by_two_in_name_order():
    ports = allocate_source_ports([_trunk("ccc"), _trunk("aaa"), _trunk("bbb")])
    assert ports == {"aaa": 5080, "bbb": 5082, "ccc": 5084}


def test_allocate_respects_explicit_ports_and_skips_them():
    ports = allocate_source_ports([_trunk("aaa"), _trunk("bbb", source_port=5082), _trunk("ccc")])
    # aaa=5080, bbb=5082(明示), ccc は 5082 を避けて 5084
    assert ports == {"aaa": 5080, "bbb": 5082, "ccc": 5084}


def test_allocate_auto_avoids_explicit_at_base():
    ports = allocate_source_ports([_trunk("aaa", source_port=5080), _trunk("bbb")])
    assert ports == {"aaa": 5080, "bbb": 5082}


def test_allocate_avoids_internal_sip_port():
    # ベースが 5060、internal も 5060 の場合、5060 を避けて 5062 から採番する
    ports = allocate_source_ports(
        [_trunk("aaa"), _trunk("bbb")],
        external_sip_port=5060,
        internal_sip_port=5060,
    )
    assert ports == {"aaa": 5062, "bbb": 5064}


def test_allocate_duplicate_explicit_ports_raises():
    with pytest.raises(ValueError):
        allocate_source_ports([_trunk("aaa", source_port=6000), _trunk("bbb", source_port=6000)])


def test_allocate_explicit_equals_internal_raises():
    with pytest.raises(ValueError):
        allocate_source_ports([_trunk("aaa", source_port=5060)])


# --- build_reload_commands（純関数） ---


def test_build_reload_commands_changed_present_restarts():
    # changed が現存トランク集合にあれば restart（作成/更新）
    assert build_reload_commands(["hgw"], changed="hgw") == ["sofia profile external_hgw restart"]


def test_build_reload_commands_changed_absent_stops():
    # changed が現存集合に無ければ stop（削除）。旧 in-memory プロファイルを破棄し
    # ゴースト登録を防ぐ。
    assert build_reload_commands([], changed="hgw") == ["sofia profile external_hgw stop"]
    # 他トランクが残っていても、削除対象が集合に無ければ stop
    assert build_reload_commands(["aaa"], changed="bbb") == ["sofia profile external_bbb stop"]


def test_build_reload_commands_all_sorted():
    assert build_reload_commands(["ccc", "aaa", "bbb"]) == [
        "sofia profile external_aaa restart",
        "sofia profile external_bbb restart",
        "sofia profile external_ccc restart",
    ]


def test_build_reload_commands_rejects_unsafe_name():
    with pytest.raises(ValueError):
        build_reload_commands(["a; rm -rf /"])


def test_build_reload_commands_rejects_unsafe_changed_name():
    # stop 経路でも名前を検証する
    with pytest.raises(ValueError):
        build_reload_commands([], changed="a; rm -rf /")
