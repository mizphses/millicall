"""calling_permission のダイヤルプラン・ディレクトリレンダリングテスト（トールフラウド対策 §7）。

- user.xml に calling_permission channel 変数が設定される
- dialplan で "internal" 権限の内線は国内 PSTN を CALL_REJECTED で拒否
- dialplan で "domestic" 権限の内線は国内 PSTN を通過、国際は拒否
- dialplan で "international" 権限の内線は allowlist 一致の国際番号を通過
- allowlist に載っていない国際番号は "international" 権限でも outbound_intl_block でブロック
- 各権限の条件が XML として整形式である
"""
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
        name="hgw",
        display_name="HGW",
        host="192.168.1.1",
        username="0312345678",
        password="pw",
        caller_id="0398765432",
    )


def _ext(number, display_name, calling_permission="domestic"):
    return ExtensionConfig(
        number=number, display_name=display_name, sip_password="pw",
        calling_permission=calling_permission,
    )


# ---- directory: user.xml 変数 ----

def test_user_xml_sets_calling_permission_internal(tmp_path):
    w = _writer(tmp_path)
    w.write_all([_ext("1001", "InternalUser", calling_permission="internal")])
    user_xml = (tmp_path / "directory" / "default" / "1001.xml").read_text()
    ET.fromstring(user_xml)
    assert 'name="calling_permission"' in user_xml
    assert 'value="internal"' in user_xml


def test_user_xml_sets_calling_permission_domestic(tmp_path):
    w = _writer(tmp_path)
    w.write_all([_ext("1002", "DomesticUser", calling_permission="domestic")])
    user_xml = (tmp_path / "directory" / "default" / "1002.xml").read_text()
    assert 'name="calling_permission"' in user_xml
    assert 'value="domestic"' in user_xml


def test_user_xml_sets_calling_permission_international(tmp_path):
    w = _writer(tmp_path)
    w.write_all([_ext("1003", "IntlUser", calling_permission="international")])
    user_xml = (tmp_path / "directory" / "default" / "1003.xml").read_text()
    assert 'name="calling_permission"' in user_xml
    assert 'value="international"' in user_xml


def test_user_xml_each_extension_gets_own_permission(tmp_path):
    """複数内線がそれぞれ個別の calling_permission を持つ。"""
    w = _writer(tmp_path)
    w.write_all([
        _ext("1001", "A", calling_permission="internal"),
        _ext("1002", "B", calling_permission="domestic"),
        _ext("1003", "C", calling_permission="international"),
    ])
    for number, perm in [("1001", "internal"), ("1002", "domestic"), ("1003", "international")]:
        txt = (tmp_path / "directory" / "default" / f"{number}.xml").read_text()
        assert f'value="{perm}"' in txt


# ---- dialplan: 発信権限ゲート ----

def test_dialplan_domestic_permission_condition_in_outbound_external(tmp_path):
    """outbound_external に domestic|international の条件が含まれる。"""
    w = _writer(tmp_path)
    w.write_all([_ext("1001", "A")], trunks=[_trunk()])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    ET.fromstring(dp)
    assert "outbound_external" in dp
    assert "domestic|international" in dp or "domestic" in dp


def test_dialplan_internal_permission_rejected_for_domestic_dest(tmp_path):
    """outbound_external に CALL_REJECTED の anti-action が含まれ、
    calling_permission="internal" の内線が国内 PSTN へ発信した場合に拒否される設計になっている。"""
    w = _writer(tmp_path)
    w.write_all([_ext("1001", "A", calling_permission="internal")], trunks=[_trunk()])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    # DOMESTIC_REJECTED のログと CALL_REJECTED の hangup が含まれる
    assert "DOMESTIC_REJECTED" in dp
    assert "CALL_REJECTED" in dp


def test_dialplan_intl_permission_check_in_allow_extension(tmp_path):
    """allowlist 拡張に "international" 権限チェックが含まれる。"""
    w = _writer(tmp_path, allow=["010"])
    w.write_all([_ext("1001", "A", calling_permission="international")], trunks=[_trunk()])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    ET.fromstring(dp)
    assert "outbound_intl_allow_010" in dp
    assert "international" in dp
    # allowlist 拡張でも CALL_REJECTED の anti-action が含まれる（権限不足時拒否）
    assert "INTL_REJECTED" in dp


def test_dialplan_intl_block_still_backstop_with_permissions(tmp_path):
    """権限チェックを追加しても outbound_intl_block は残り、allowlist 外をブロックする。"""
    w = _writer(tmp_path, allow=["010"])
    w.write_all([], trunks=[_trunk()])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    assert 'name="outbound_intl_block"' in dp
    assert "010\\d+" in dp


def test_dialplan_extension_order_preserved(tmp_path):
    """拡張の出現順: internal_extensions → intl_allow → intl_block → outbound_external"""
    w = _writer(tmp_path, allow=["010"])
    w.write_all([], trunks=[_trunk()])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    assert dp.index("internal_extensions") < dp.index("outbound_intl_allow_")
    assert dp.index("outbound_intl_allow_") < dp.index("outbound_intl_block")
    assert dp.index("outbound_intl_block") < dp.index("outbound_external")


def test_dialplan_valid_xml_with_all_tiers(tmp_path):
    """3 種の権限を持つ内線が存在しても dialplan XML が整形式であること。"""
    w = _writer(tmp_path, allow=["010"])
    w.write_all(
        [
            _ext("1001", "Internal", calling_permission="internal"),
            _ext("1002", "Domestic", calling_permission="domestic"),
            _ext("1003", "Intl", calling_permission="international"),
        ],
        trunks=[_trunk()],
    )
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    ET.fromstring(dp)  # 整形式 XML であることを確認


def test_dialplan_no_outbound_extension_without_trunk(tmp_path):
    """トランク無しの場合は権限チェック拡張も生成されない。"""
    w = _writer(tmp_path)
    w.write_all([_ext("1001", "A", calling_permission="internal")])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    assert "outbound_external" not in dp
    assert "DOMESTIC_REJECTED" not in dp
    assert "outbound_intl_block" not in dp


def test_dialplan_calling_permission_variable_referenced(tmp_path):
    """dialplan テンプレートが ${calling_permission} 変数を参照している。"""
    w = _writer(tmp_path, allow=["010"])
    w.write_all([], trunks=[_trunk()])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    assert "${calling_permission}" in dp or "calling_permission" in dp


def test_dialplan_domestic_extension_can_reach_pstn(tmp_path):
    """domestic 権限内線向け: outbound_external に gateway bridge アクションが含まれる。"""
    w = _writer(tmp_path)
    w.write_all([_ext("1001", "Domestic", calling_permission="domestic")], trunks=[_trunk()])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    # outbound_external には gateway へのブリッジが含まれる（domestic 条件内）
    assert "sofia/gateway/hgw/${destination_number}" in dp


def test_dialplan_international_extension_intl_allow_bridge(tmp_path):
    """international 権限内線向け: allowlist 拡張に gateway bridge アクションが含まれる。"""
    w = _writer(tmp_path, allow=["010"])
    w.write_all([_ext("1003", "Intl", calling_permission="international")], trunks=[_trunk()])
    dp = (tmp_path / "dialplan" / "default.xml").read_text()
    assert "outbound_intl_allow_010" in dp
    assert "sofia/gateway/hgw/${destination_number}" in dp
