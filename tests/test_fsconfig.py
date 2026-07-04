import defusedxml.ElementTree as ET  # noqa: N817

from millicall.telephony.fsconfig import ExtensionConfig, FreeswitchConfigWriter


def _writer(tmp_path):
    return FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="192.168.1.10",
        esl_password="esl-secret-xyz",
        sip_port=5060,
    )


def test_write_all_creates_user_file(tmp_path) -> None:
    writer = _writer(tmp_path)
    writer.write_all([ExtensionConfig("1001", "Alice", "pw-1001")])
    user_file = tmp_path / "directory" / "default" / "1001.xml"
    assert user_file.exists()
    content = user_file.read_text()
    assert 'user id="1001"' in content
    assert 'value="pw-1001"' in content
    assert 'value="Alice"' in content


def test_write_all_creates_static_configs(tmp_path) -> None:
    writer = _writer(tmp_path)
    writer.write_all([ExtensionConfig("1001", "Alice", "pw-1001")])
    assert (tmp_path / "directory" / "default.xml").exists()
    internal = (tmp_path / "sip_profiles" / "internal.xml").read_text()
    assert 'name="internal"' in internal
    assert 'value="5060"' in internal
    assert 'name="192.168.1.10"' in internal
    assert '<param name="sip-ip" value="auto"/>' in internal
    assert '<param name="rtp-ip" value="auto"/>' in internal
    dialplan = (tmp_path / "dialplan" / "default.xml").read_text()
    assert "user/${destination_number}@192.168.1.10" in dialplan


def test_event_socket_password_injected(tmp_path) -> None:
    writer = _writer(tmp_path)
    writer.write_all([])
    es = (tmp_path / "autoload_configs" / "event_socket.conf.xml").read_text()
    assert 'value="esl-secret-xyz"' in es
    assert 'value="127.0.0.1"' in es


def test_stale_user_files_removed(tmp_path) -> None:
    writer = _writer(tmp_path)
    writer.write_all([ExtensionConfig("1001", "Alice", "pw-1001")])
    writer.write_all([ExtensionConfig("1002", "Bob", "pw-1002")])
    assert not (tmp_path / "directory" / "default" / "1001.xml").exists()
    assert (tmp_path / "directory" / "default" / "1002.xml").exists()


def test_returns_written_paths(tmp_path) -> None:
    writer = _writer(tmp_path)
    paths = writer.write_all([ExtensionConfig("1001", "Alice", "pw-1001")])
    assert all(p.exists() for p in paths)


# --- MANDATORY DEVIATION: sip_bind_ip overrides sip-ip and rtp-ip ---


def test_sip_bind_ip_used_in_profile_when_set(tmp_path) -> None:
    """When sip_bind_ip is provided, the profile uses it for sip-ip and rtp-ip."""
    writer = FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="10.0.0.1",
        esl_password="secret",
        sip_bind_ip="192.168.100.5",
    )
    writer.write_all([])
    internal = (tmp_path / "sip_profiles" / "internal.xml").read_text()
    assert 'value="192.168.100.5"' in internal
    # "auto" should NOT appear when sip_bind_ip is set
    assert "auto" not in internal


def test_sip_bind_ip_falls_back_to_auto_when_none(tmp_path) -> None:
    """When sip_bind_ip is None, the profile falls back to the auto defaults."""
    writer = FreeswitchConfigWriter(
        output_dir=tmp_path,
        sip_domain="10.0.0.1",
        esl_password="secret",
        sip_ip="auto",
        rtp_ip="auto",
        sip_bind_ip=None,
    )
    writer.write_all([])
    internal = (tmp_path / "sip_profiles" / "internal.xml").read_text()
    # Both sip-ip and rtp-ip should be "auto"
    assert internal.count('value="auto"') == 2


def test_autoescape_escapes_special_chars_in_user_xml(tmp_path) -> None:
    """XML special characters in display_name must be entity-escaped, not injected raw."""
    writer = _writer(tmp_path)
    writer.write_all([ExtensionConfig("1001", 'A & B <X>"', "pw-1001")])
    content = (tmp_path / "directory" / "default" / "1001.xml").read_text()
    assert "A &amp; B &lt;X&gt;" in content
    ET.fromstring(content)  # raises if XML is not well-formed


def test_sip_password_not_in_repr() -> None:
    """sip_password must not leak through dataclass repr."""
    cfg = ExtensionConfig("1001", "Alice", "hunter2")
    assert "hunter2" not in repr(cfg)


# --- Regression: sip_bind_ip must flow from Settings through build_config_writer ---


def test_build_config_writer_wires_sip_bind_ip(tmp_path) -> None:
    """build_config_writer (production path) must pass sip_bind_ip to the Writer.

    This test exercises the real production factory rather than constructing
    FreeswitchConfigWriter directly, ensuring the wiring in service.py stays intact.
    """
    from millicall.config import Settings
    from millicall.secrets_store import Secrets
    from millicall.telephony.service import build_config_writer

    settings = Settings(
        fs_config_dir=tmp_path,
        sip_domain="millicall.local",
        sip_bind_ip="10.0.0.5",
    )
    secrets = Secrets(
        session_secret="session-secret-test",
        master_key="master-key-test",
        esl_password="esl-secret-test",
    )

    writer = build_config_writer(settings, secrets)
    writer.write_all([])

    internal = (tmp_path / "sip_profiles" / "internal.xml").read_text()
    assert 'value="10.0.0.5"' in internal, (
        "sip-ip / rtp-ip must reflect sip_bind_ip='10.0.0.5' when wired through build_config_writer"
    )


# --- re_escape filter guard: input validation ---


def test_re_escape_rejects_unsafe_input(tmp_path) -> None:
    """re_escape filter must reject unsafe input (non-[0-9*#])."""
    writer = _writer(tmp_path)
    # Call filter directly via writer._env.filters["re_escape"]
    filter_func = writer._env.filters["re_escape"]

    # Safe inputs should not raise
    assert filter_func("123") is not None
    assert filter_func("*100#") is not None

    # Unsafe inputs should raise ValueError
    import pytest
    with pytest.raises(ValueError, match="re_escape filter: unsafe input"):
        filter_func("<evil>")

    with pytest.raises(ValueError, match="re_escape filter: unsafe input"):
        filter_func("123; DROP TABLE")


def test_match_number_with_star_and_hash(tmp_path) -> None:
    """match_number with * and # must be properly escaped in regex expression."""
    from millicall.telephony.fsconfig import RouteConfig

    writer = _writer(tmp_path)
    writer.write_all(
        [],
        routes=[RouteConfig(match_number="*100#", target_type="extension", target_value="1001")],
    )
    pub = (tmp_path / "dialplan" / "public.xml").read_text()
    # Verify the regex is escaped: * and # become \* and \#
    assert 'expression="^\\*100\\#$"' in pub
    # Verify the XML parses correctly
    ET.fromstring(pub)


def test_disabled_route_excluded_from_public_xml(tmp_path) -> None:
    """Disabled route must not appear in the generated public.xml."""
    from millicall.telephony.fsconfig import RouteConfig

    writer = _writer(tmp_path)
    # Create a route with match_number 0312345678 (enabled)
    # This tests the unit-level variant via write_all
    enabled_route = RouteConfig(
        match_number="0312345678", target_type="extension", target_value="1001"
    )
    writer.write_all([], routes=[enabled_route])
    pub = (tmp_path / "dialplan" / "public.xml").read_text()

    # Enabled route should be in the XML
    assert 'name="inbound_0312345678"' in pub

    # Now write with empty routes (simulating disabled route)
    writer.write_all([], routes=[])
    pub = (tmp_path / "dialplan" / "public.xml").read_text()

    # Disabled route should NOT be in the XML
    assert 'name="inbound_0312345678"' not in pub
    assert 'expression="^0312345678$"' not in pub
