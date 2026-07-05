from millicall.crypto import SecretBox, mask_secret


def test_encrypt_decrypt_roundtrip():
    box = SecretBox("m" * 48)
    token = box.encrypt("sk-secret-123")
    assert token != "sk-secret-123"
    assert box.decrypt(token) == "sk-secret-123"


def test_same_master_key_can_decrypt_across_instances():
    token = SecretBox("k" * 48).encrypt("hello")
    assert SecretBox("k" * 48).decrypt(token) == "hello"


def test_mask_hides_middle():
    assert mask_secret("sk-1234567890abcd") == "****abcd"
    assert mask_secret("abc") == "****"
    assert mask_secret("") == ""
