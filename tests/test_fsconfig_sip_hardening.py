"""
テスト: SIP多層防御 (Phase 6 Task 7)
- acl.conf.xml レンダリング（millicall_trusted, default="deny", RFC1918+loopback）
- internal / external プロファイルへの apply-inbound-acl=millicall_trusted 適用
- public.xml 匿名着信拒否（sip_reject_anonymous=False/True）
- Settings.sip_trusted_cidrs デフォルト値（HGW 192.168.1.1 を包含）
"""

import defusedxml.ElementTree as ET  # noqa: N817

from millicall.telephony.fsconfig import FreeswitchConfigWriter


def _writer(
    tmp_path,
    *,
    sip_trusted_cidrs: list[str] | None = None,
    sip_reject_anonymous: bool = False,
) -> FreeswitchConfigWriter:
    return FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="192.168.1.10",
        esl_password="esl-secret-xyz",
        sip_port=5060,
        sip_trusted_cidrs=sip_trusted_cidrs,
        sip_reject_anonymous=sip_reject_anonymous,
    )


# --- acl.conf.xml ---


def test_acl_conf_xml_created(tmp_path) -> None:
    """acl.conf.xml が write_all で生成されること。"""
    _writer(tmp_path).write_all([])
    assert (tmp_path / "autoload_configs" / "acl.conf.xml").exists()


def test_acl_conf_xml_default_deny(tmp_path) -> None:
    """millicall_trusted リストが default="deny" であること。"""
    _writer(tmp_path).write_all([])
    content = (tmp_path / "autoload_configs" / "acl.conf.xml").read_text()
    assert 'name="millicall_trusted"' in content
    assert 'default="deny"' in content


def test_acl_conf_xml_default_cidrs_include_hgw_range(tmp_path) -> None:
    """デフォルト CIDR に 192.168.0.0/16 が含まれ、HGW (192.168.1.1) が通過できること。"""
    _writer(tmp_path).write_all([])
    content = (tmp_path / "autoload_configs" / "acl.conf.xml").read_text()
    assert "192.168.0.0/16" in content
    # RFC1918 全帯域が含まれること
    assert "10.0.0.0/8" in content
    assert "172.16.0.0/12" in content
    # loopback
    assert "127.0.0.1/32" in content


def test_acl_conf_xml_custom_cidrs(tmp_path) -> None:
    """カスタム CIDR リストが正しくレンダリングされること。"""
    cidrs = ["192.168.100.0/24", "10.10.0.0/16"]
    _writer(tmp_path, sip_trusted_cidrs=cidrs).write_all([])
    content = (tmp_path / "autoload_configs" / "acl.conf.xml").read_text()
    assert "192.168.100.0/24" in content
    assert "10.10.0.0/16" in content
    # 未指定 CIDR は含まれないこと
    assert "172.16.0.0/12" not in content


def test_acl_conf_xml_is_well_formed(tmp_path) -> None:
    """生成された acl.conf.xml が整形式 XML であること。"""
    _writer(tmp_path).write_all([])
    content = (tmp_path / "autoload_configs" / "acl.conf.xml").read_text()
    ET.fromstring(content)  # 不正 XML なら例外が発生する


def test_acl_conf_xml_in_returned_paths(tmp_path) -> None:
    """acl.conf.xml が write_all の戻り値リストに含まれること。"""
    paths = _writer(tmp_path).write_all([])
    names = [p.name for p in paths]
    assert "acl.conf.xml" in names


# --- SIP プロファイルへの ACL 適用 ---


def test_internal_profile_has_apply_inbound_acl(tmp_path) -> None:
    """internal.xml に apply-inbound-acl=millicall_trusted が設定されていること。"""
    _writer(tmp_path).write_all([])
    content = (tmp_path / "sip_profiles" / "internal.xml").read_text()
    assert 'name="apply-inbound-acl"' in content
    assert 'value="millicall_trusted"' in content


def test_external_profile_has_apply_inbound_acl(tmp_path) -> None:
    """external.xml に apply-inbound-acl=millicall_trusted が設定されていること。"""
    _writer(tmp_path).write_all([])
    content = (tmp_path / "sip_profiles" / "external.xml").read_text()
    assert 'name="apply-inbound-acl"' in content
    assert 'value="millicall_trusted"' in content


def test_external_profile_acl_is_not_none(tmp_path) -> None:
    """external.xml の apply-inbound-acl が 'none' でないこと（regression: ACL無効化の防止）。"""
    _writer(tmp_path).write_all([])
    content = (tmp_path / "sip_profiles" / "external.xml").read_text()
    import re

    # apply-inbound-acl パラメータの value が 'none' でないこと（コメント内の 'none' は無視）
    match = re.search(r'name="apply-inbound-acl"\s+value="([^"]+)"', content)
    assert match is not None, "apply-inbound-acl param が見つからない"
    assert match.group(1) != "none", "apply-inbound-acl が 'none' のままでは ACL が無効"


# --- 匿名着信拒否オプション ---


def test_anonymous_reject_absent_when_disabled(tmp_path) -> None:
    """sip_reject_anonymous=False（デフォルト）のとき、public.xml に拒否 extension が含まれないこと。
    リグレッションガード: 非通知 HGW 回線からの実機着信が誤って拒否されないことを確認する。
    """
    _writer(tmp_path, sip_reject_anonymous=False).write_all([])
    content = (tmp_path / "dialplan" / "public.xml").read_text()
    assert "reject_anonymous" not in content
    assert "CALL_REJECTED" not in content


def test_anonymous_reject_present_when_enabled(tmp_path) -> None:
    """sip_reject_anonymous=True のとき、public.xml に拒否 extension が含まれること。"""
    _writer(tmp_path, sip_reject_anonymous=True).write_all([])
    content = (tmp_path / "dialplan" / "public.xml").read_text()
    assert "reject_anonymous" in content
    assert "CALL_REJECTED" in content


def test_anonymous_reject_matches_anonymous_caller_id(tmp_path) -> None:
    """sip_reject_anonymous=True のとき、'anonymous' をキャッチする正規表現が含まれること。"""
    _writer(tmp_path, sip_reject_anonymous=True).write_all([])
    content = (tmp_path / "dialplan" / "public.xml").read_text()
    # caller_id_number が anonymous/restricted/Anonymous/空 にマッチすること
    assert "anonymous" in content
    assert "restricted" in content


def test_anonymous_reject_does_not_match_numeric_caller_id(tmp_path) -> None:
    """拒否 extension の正規表現が数字の caller-ID にマッチしないこと。
    186プレフィックス付き（発番号あり）の呼は通過することを確認する。
    """
    _writer(tmp_path, sip_reject_anonymous=True).write_all([])
    content = (tmp_path / "dialplan" / "public.xml").read_text()
    import re

    # 拒否 extension の condition 正規表現を取り出す
    match = re.search(r'reject_anonymous.*?expression="([^"]+)"', content, re.DOTALL)
    assert match is not None, "reject_anonymous extension の condition が見つからない"
    pattern = match.group(1)
    # 数字の caller-ID はマッチしない（通話が通過する）
    assert not re.fullmatch(pattern, "09012345678"), f"数字 caller-ID が誤ってマッチ: {pattern}"
    # anonymous はマッチする（拒否される）
    assert re.fullmatch(pattern, "anonymous"), f"anonymous がマッチしない: {pattern}"
    assert re.fullmatch(pattern, ""), f"空文字がマッチしない: {pattern}"


def test_anonymous_reject_public_xml_well_formed(tmp_path) -> None:
    """sip_reject_anonymous=True のとき public.xml が整形式 XML であること。"""
    _writer(tmp_path, sip_reject_anonymous=True).write_all([])
    content = (tmp_path / "dialplan" / "public.xml").read_text()
    ET.fromstring(content)  # 不正 XML なら例外が発生する


# --- Settings デフォルト値の確認 ---


def test_settings_default_sip_trusted_cidrs_includes_private_ranges() -> None:
    """Settings.sip_trusted_cidrs のデフォルト値に RFC1918 全帯域と loopback が含まれること。
    HGW 192.168.1.1 が 192.168.0.0/16 に内包されることを確認する。
    """
    from millicall.config import Settings

    s = Settings()
    assert "192.168.0.0/16" in s.sip_trusted_cidrs, "HGW が内包される 192.168.0.0/16 が必要"
    assert "10.0.0.0/8" in s.sip_trusted_cidrs
    assert "172.16.0.0/12" in s.sip_trusted_cidrs
    assert "127.0.0.1/32" in s.sip_trusted_cidrs


def test_settings_default_sip_reject_anonymous_is_false() -> None:
    """sip_reject_anonymous のデフォルトが False（非通知HGW回線保護）であること。"""
    from millicall.config import Settings

    s = Settings()
    assert s.sip_reject_anonymous is False, (
        "非通知HGW回線保護のためデフォルトは False でなければならない"
    )


def test_settings_sip_trusted_cidrs_from_comma_string() -> None:
    """MILLICALL_SIP_TRUSTED_CIDRS をカンマ区切り文字列で渡せること。"""
    from millicall.config import Settings

    s = Settings(sip_trusted_cidrs="10.0.0.0/8,192.168.0.0/16")  # type: ignore[arg-type]
    assert s.sip_trusted_cidrs == ["10.0.0.0/8", "192.168.0.0/16"]


def test_build_config_writer_passes_sip_hardening_settings(tmp_path) -> None:
    """build_config_writer が sip_trusted_cidrs / sip_reject_anonymous を Writer に渡すこと。"""
    from millicall.config import Settings
    from millicall.secrets_store import Secrets
    from millicall.telephony.service import build_config_writer

    settings = Settings(
        fs_config_dir=tmp_path,
        sip_domain="millicall.local",
        sip_trusted_cidrs=["192.168.0.0/16"],  # type: ignore[arg-type]
        sip_reject_anonymous=True,
    )
    secrets = Secrets(
        session_secret="session-secret-test",
        master_key="master-key-test",
        esl_password="esl-secret-test",
    )

    writer = build_config_writer(settings, secrets)
    writer.write_all([])

    # ACL に指定 CIDR が含まれること
    acl = (tmp_path / "autoload_configs" / "acl.conf.xml").read_text()
    assert "192.168.0.0/16" in acl

    # 匿名着信拒否 extension が含まれること
    pub = (tmp_path / "dialplan" / "public.xml").read_text()
    assert "reject_anonymous" in pub
