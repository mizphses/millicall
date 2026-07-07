"""NetworkConfig / Device の __repr__ が機密フィールドを漏洩しないことを確認する。"""
from millicall.models import Device, NetworkConfig


def test_network_config_repr_excludes_tailscale_key():
    """NetworkConfig の repr に tailscale_auth_key_encrypted が含まれない。"""
    obj = NetworkConfig()
    obj.tailscale_auth_key_encrypted = "supersecret-fernet-token"
    r = repr(obj)
    assert "supersecret-fernet-token" not in r
    assert "tailscale_auth_key_encrypted" not in r


def test_device_repr_excludes_provision_token():
    """Device の repr に provision_token が含まれない。"""
    obj = Device()
    obj.provision_token = "one-time-token-secret"
    r = repr(obj)
    assert "one-time-token-secret" not in r
    assert "provision_token" not in r
