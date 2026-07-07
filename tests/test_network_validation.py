"""network/validation.py のユニットテスト。"""
import pytest

from millicall.network.validation import (
    is_valid_interface,
    is_valid_tailscale_authkey,
    normalize_mac,
    validate_cidr_prefix,
    validate_ipv4,
    validate_ipv4_range,
)


class TestIsValidInterface:
    """is_valid_interface のテスト。"""

    def test_valid_simple(self):
        assert is_valid_interface("eth0")

    def test_valid_with_dot(self):
        assert is_valid_interface("eth0.100")

    def test_valid_with_hyphen(self):
        assert is_valid_interface("enp3s0")

    def test_valid_with_underscore(self):
        assert is_valid_interface("lo_test")

    def test_valid_max_length(self):
        # 15 文字
        assert is_valid_interface("a" * 15)

    def test_invalid_too_long(self):
        # 16 文字以上は拒否
        assert not is_valid_interface("a" * 16)

    def test_invalid_empty(self):
        assert not is_valid_interface("")

    def test_invalid_space(self):
        assert not is_valid_interface("eth 0")

    def test_invalid_semicolon(self):
        assert not is_valid_interface("eth0;rm -rf /")

    def test_invalid_dollar(self):
        assert not is_valid_interface("$eth0")

    def test_invalid_slash(self):
        assert not is_valid_interface("eth0/subif")

    def test_invalid_backtick(self):
        assert not is_valid_interface("`eth0`")

    def test_invalid_ampersand(self):
        assert not is_valid_interface("eth0&evil")

    def test_invalid_pipe(self):
        assert not is_valid_interface("eth|pipe")


class TestValidateIpv4:
    """validate_ipv4 のテスト。"""

    def test_valid_ip(self):
        validate_ipv4("192.168.1.1")  # 例外なし

    def test_valid_loopback(self):
        validate_ipv4("127.0.0.1")

    def test_valid_max(self):
        validate_ipv4("255.255.255.255")

    def test_invalid_out_of_range(self):
        with pytest.raises(ValueError):
            validate_ipv4("256.0.0.1")

    def test_invalid_hostname(self):
        with pytest.raises(ValueError):
            validate_ipv4("example.com")

    def test_invalid_empty(self):
        with pytest.raises(ValueError):
            validate_ipv4("")

    def test_invalid_ipv6(self):
        with pytest.raises(ValueError):
            validate_ipv4("::1")

    def test_invalid_cidr_notation(self):
        with pytest.raises(ValueError):
            validate_ipv4("192.168.1.0/24")


class TestValidateIpv4Range:
    """validate_ipv4_range のテスト。"""

    def test_valid_range(self):
        validate_ipv4_range("172.20.1.1", "172.20.254.254")  # 例外なし

    def test_valid_equal(self):
        validate_ipv4_range("10.0.0.1", "10.0.0.1")  # start == end は許容

    def test_invalid_reversed(self):
        with pytest.raises(ValueError):
            validate_ipv4_range("172.20.254.254", "172.20.1.1")

    def test_invalid_start_ip(self):
        with pytest.raises(ValueError):
            validate_ipv4_range("bad.ip", "192.168.1.1")

    def test_invalid_end_ip(self):
        with pytest.raises(ValueError):
            validate_ipv4_range("192.168.1.1", "bad.ip")


class TestValidateCidrPrefix:
    """validate_cidr_prefix のテスト。"""

    def test_valid_zero(self):
        validate_cidr_prefix(0)

    def test_valid_16(self):
        validate_cidr_prefix(16)

    def test_valid_32(self):
        validate_cidr_prefix(32)

    def test_invalid_negative(self):
        with pytest.raises(ValueError):
            validate_cidr_prefix(-1)

    def test_invalid_33(self):
        with pytest.raises(ValueError):
            validate_cidr_prefix(33)


class TestIsValidTailscaleAuthkey:
    """is_valid_tailscale_authkey のテスト。"""

    def test_valid_key(self):
        assert is_valid_tailscale_authkey("tskey-abc123-XYZ")

    def test_valid_long_key(self):
        assert is_valid_tailscale_authkey("tskey-auth-ABCDEF1234567890-abcdef")

    def test_invalid_no_prefix(self):
        assert not is_valid_tailscale_authkey("abc123")

    def test_invalid_wrong_prefix(self):
        assert not is_valid_tailscale_authkey("ts-key-abc123")

    def test_invalid_empty(self):
        assert not is_valid_tailscale_authkey("")

    def test_invalid_special_chars(self):
        assert not is_valid_tailscale_authkey("tskey-abc!@#")

    def test_invalid_space(self):
        assert not is_valid_tailscale_authkey("tskey-abc 123")


class TestNormalizeMac:
    """normalize_mac のテスト。"""

    def test_colon_lowercase(self):
        assert normalize_mac("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"

    def test_colon_uppercase(self):
        assert normalize_mac("AA:BB:CC:DD:EE:FF") == "AA:BB:CC:DD:EE:FF"

    def test_hyphen_separator(self):
        assert normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"

    def test_dot_separator(self):
        # Cisco 形式: aabb.ccdd.eeff
        assert normalize_mac("aabb.ccdd.eeff") == "AA:BB:CC:DD:EE:FF"

    def test_no_separator(self):
        assert normalize_mac("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"

    def test_invalid_short(self):
        with pytest.raises(ValueError):
            normalize_mac("aa:bb:cc")

    def test_invalid_too_long(self):
        with pytest.raises(ValueError):
            normalize_mac("aa:bb:cc:dd:ee:ff:11")

    def test_invalid_non_hex(self):
        with pytest.raises(ValueError):
            normalize_mac("gg:bb:cc:dd:ee:ff")

    def test_invalid_empty(self):
        with pytest.raises(ValueError):
            normalize_mac("")

    def test_invalid_garbage(self):
        with pytest.raises(ValueError):
            normalize_mac("not-a-mac-address")
