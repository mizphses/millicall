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
