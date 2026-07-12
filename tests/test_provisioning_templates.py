"""プロビジョニングテンプレート純粋関数のユニットテスト。

DB アクセスなし。Extension / NetworkConfig / Settings のモックオブジェクトを使って
各テンプレート関数の出力内容を検証する。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# モックオブジェクト
# ---------------------------------------------------------------------------


class _MockExtension:
    """Extension ORM オブジェクトのモック。"""

    def __init__(
        self,
        number: str = "1001",
        display_name: str = "Alice",
        sip_password: str = "s3cr3tPassw0rd",
    ) -> None:
        self.number = number
        self.display_name = display_name
        self.sip_password = sip_password


class _MockNetworkConfig:
    """NetworkConfig ORM オブジェクトのモック。"""

    def __init__(
        self,
        lan_ip: str = "172.20.0.1",
        lan_prefix: int = 16,
        provisioning_base_url: str = "http://172.20.0.1:8000",
    ) -> None:
        self.lan_ip = lan_ip
        self.lan_prefix = lan_prefix
        self.provisioning_base_url = provisioning_base_url


class _MockSettings:
    """Settings のモック。"""

    def __init__(self, sip_domain: str = "millicall.local", http_port: int = 80) -> None:
        self.sip_domain = sip_domain
        self.http_port = http_port


# ---------------------------------------------------------------------------
# render_panasonic_common
# ---------------------------------------------------------------------------


def test_panasonic_common_contains_lan_ip() -> None:
    """共通設定に LAN IP（NTP アドレス）が含まれる。"""
    from millicall.provisioning.templates import render_panasonic_common

    nc = _MockNetworkConfig(lan_ip="192.168.1.1", provisioning_base_url="http://192.168.1.1:8000")
    settings = _MockSettings()
    content = render_panasonic_common(network_config=nc, settings=settings)

    assert "192.168.1.1" in content


def test_panasonic_common_contains_provisioning_base_url() -> None:
    """共通設定にプロビジョニングベース URL が含まれる。"""
    from millicall.provisioning.templates import render_panasonic_common

    nc = _MockNetworkConfig(provisioning_base_url="http://pbx.example.com:8000")
    settings = _MockSettings()
    content = render_panasonic_common(network_config=nc, settings=settings)

    assert "pbx.example.com" in content


def test_panasonic_common_fallback_base_url() -> None:
    """provisioning_base_url が空の場合、LAN IP + core の HTTP ポートにフォールバックする。

    既定 http_port=80 はポートを省略する。カスタムポートは :port を付与する。
    """
    from millicall.provisioning.templates import render_panasonic_common

    nc = _MockNetworkConfig(lan_ip="10.0.0.1", provisioning_base_url="")
    # 既定ポート 80 → ポート省略
    content80 = render_panasonic_common(network_config=nc, settings=_MockSettings())
    assert "http://10.0.0.1/provisioning" in content80
    assert "10.0.0.1:8000" not in content80
    # カスタムポート → :port 付与
    content8000 = render_panasonic_common(network_config=nc, settings=_MockSettings(http_port=8000))
    assert "http://10.0.0.1:8000/provisioning" in content8000


def test_panasonic_common_uses_crlf() -> None:
    """共通設定は CRLF で行区切りされる（Panasonic 標準）。"""
    from millicall.provisioning.templates import render_panasonic_common

    nc = _MockNetworkConfig()
    settings = _MockSettings()
    content = render_panasonic_common(network_config=nc, settings=settings)

    assert "\r\n" in content


def test_panasonic_common_no_mac_or_hostname() -> None:
    """共通設定に MAC アドレスやホスト名は含まれない。"""
    from millicall.provisioning.templates import render_panasonic_common

    nc = _MockNetworkConfig()
    settings = _MockSettings()
    content = render_panasonic_common(network_config=nc, settings=settings)

    # MAC アドレス形式（XX:XX:XX:XX:XX:XX）が含まれないことを確認
    import re

    assert not re.search(r"[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}", content)


# ---------------------------------------------------------------------------
# render_panasonic_config
# ---------------------------------------------------------------------------


def test_panasonic_config_contains_extension_number() -> None:
    """端末固有設定に内線番号が含まれる。"""
    from millicall.provisioning.templates import render_panasonic_config

    ext = _MockExtension(number="2001")
    nc = _MockNetworkConfig()
    settings = _MockSettings()
    content = render_panasonic_config(extension=ext, network_config=nc, settings=settings)

    assert "2001" in content


def test_panasonic_config_contains_sip_password() -> None:
    """端末固有設定に SIP パスワードが含まれる。"""
    from millicall.provisioning.templates import render_panasonic_config

    ext = _MockExtension(sip_password="MyS3cretPass")
    nc = _MockNetworkConfig()
    settings = _MockSettings()
    content = render_panasonic_config(extension=ext, network_config=nc, settings=settings)

    assert "MyS3cretPass" in content


def test_panasonic_config_contains_lan_ip() -> None:
    """端末固有設定に LAN IP（SIP サーバーアドレス）が含まれる。"""
    from millicall.provisioning.templates import render_panasonic_config

    ext = _MockExtension()
    nc = _MockNetworkConfig(lan_ip="10.10.0.1")
    settings = _MockSettings()
    content = render_panasonic_config(extension=ext, network_config=nc, settings=settings)

    assert "10.10.0.1" in content


def test_panasonic_config_display_name_with_special_chars() -> None:
    """表示名に特殊文字（ダブルクォート）が含まれても設定ファイルが壊れない。"""
    from millicall.provisioning.templates import render_panasonic_config

    ext = _MockExtension(display_name='Alice "Bob" Smith')
    nc = _MockNetworkConfig()
    settings = _MockSettings()
    # 例外が発生しないこと、および設定ファイルが文字列であることを確認
    content = render_panasonic_config(extension=ext, network_config=nc, settings=settings)
    assert isinstance(content, str)
    # エスケープされたダブルクォートが含まれること
    assert '\\"' in content


def test_panasonic_config_display_name_with_newline() -> None:
    """表示名に改行が含まれても設定ファイルが壊れない。"""
    from millicall.provisioning.templates import render_panasonic_config

    ext = _MockExtension(display_name="Alice\nBob")
    nc = _MockNetworkConfig()
    settings = _MockSettings()
    content = render_panasonic_config(extension=ext, network_config=nc, settings=settings)
    # 改行は除去されていること（設定値内に改行がないこと）
    assert "Alice\nBob" not in content


def test_panasonic_config_uses_crlf() -> None:
    """端末固有設定は CRLF で行区切りされる。"""
    from millicall.provisioning.templates import render_panasonic_config

    ext = _MockExtension()
    nc = _MockNetworkConfig()
    settings = _MockSettings()
    content = render_panasonic_config(extension=ext, network_config=nc, settings=settings)

    assert "\r\n" in content


# ---------------------------------------------------------------------------
# render_panasonic_model_file（{MODEL}.cfg プレプロビジョニング入口ファイル）
# ---------------------------------------------------------------------------


def test_panasonic_model_file_contains_cfg_paths() -> None:
    """入口ファイルは Config{MAC}.cfg / ConfigCommon.cfg を指す 2 行を含む。"""
    from millicall.provisioning.templates import render_panasonic_model_file

    nc = _MockNetworkConfig(provisioning_base_url="http://10.0.0.1:8000")
    settings = _MockSettings()
    content = render_panasonic_model_file(network_config=nc, settings=settings)

    assert (
        'CFG_STANDARD_FILE_PATH="http://10.0.0.1:8000/provisioning/Panasonic/Config{MAC}.cfg"'
        in content
    )
    assert (
        'CFG_MASTER_FILE_PATH="http://10.0.0.1:8000/provisioning/Panasonic/ConfigCommon.cfg"'
        in content
    )


def test_panasonic_model_file_mac_is_literal() -> None:
    """{MAC} はリテラル文字列としてそのまま出力される（Python 展開されない）。"""
    from millicall.provisioning.templates import render_panasonic_model_file

    nc = _MockNetworkConfig()
    settings = _MockSettings()
    content = render_panasonic_model_file(network_config=nc, settings=settings)

    assert "Config{MAC}.cfg" in content


def test_panasonic_model_file_has_comment_header() -> None:
    """先頭にプレプロビジョニングを示すコメント行を含む。"""
    from millicall.provisioning.templates import render_panasonic_model_file

    nc = _MockNetworkConfig()
    settings = _MockSettings()
    content = render_panasonic_model_file(network_config=nc, settings=settings)

    assert content.startswith("# Millicall PBX - Panasonic pre-provisioning (model entry)")


def test_panasonic_model_file_uses_lf() -> None:
    """入口ファイルは LF で行区切りされる（CRLF を含まない）。"""
    from millicall.provisioning.templates import render_panasonic_model_file

    nc = _MockNetworkConfig()
    settings = _MockSettings()
    content = render_panasonic_model_file(network_config=nc, settings=settings)

    assert "\r\n" not in content
    assert "\n" in content


def test_panasonic_model_file_fallback_base_url() -> None:
    """provisioning_base_url が空なら LAN IP + HTTP ポートにフォールバックする。"""
    from millicall.provisioning.templates import render_panasonic_model_file

    nc = _MockNetworkConfig(lan_ip="10.0.0.1", provisioning_base_url="")
    content = render_panasonic_model_file(network_config=nc, settings=_MockSettings())

    assert "http://10.0.0.1/provisioning/Panasonic/Config{MAC}.cfg" in content


# ---------------------------------------------------------------------------
# render_yealink_boot
# ---------------------------------------------------------------------------


def test_yealink_boot_contains_include_config() -> None:
    """boot ファイルに include:config 行が含まれる。"""
    from millicall.provisioning.templates import render_yealink_boot

    nc = _MockNetworkConfig(provisioning_base_url="http://10.0.0.1:8000")
    settings = _MockSettings()
    content = render_yealink_boot(network_config=nc, settings=settings)

    assert "include:config" in content
    assert "10.0.0.1" in content


def test_yealink_boot_contains_common_cfg_reference() -> None:
    """boot ファイルに common.cfg への参照が含まれる。"""
    from millicall.provisioning.templates import render_yealink_boot

    nc = _MockNetworkConfig(provisioning_base_url="http://172.20.0.1:8000")
    settings = _MockSettings()
    content = render_yealink_boot(network_config=nc, settings=settings)

    assert "common.cfg" in content


# ---------------------------------------------------------------------------
# render_yealink_common
# ---------------------------------------------------------------------------


def test_yealink_common_contains_lan_ip() -> None:
    """Yealink 共通設定に LAN IP（NTP アドレス）が含まれる。"""
    from millicall.provisioning.templates import render_yealink_common

    nc = _MockNetworkConfig(lan_ip="192.168.10.1", provisioning_base_url="http://192.168.10.1:8000")
    settings = _MockSettings()
    content = render_yealink_common(network_config=nc, settings=settings)

    assert "192.168.10.1" in content


def test_yealink_common_contains_phonebook_url() -> None:
    """Yealink 共通設定に電話帳 URL が含まれる。"""
    from millicall.provisioning.templates import render_yealink_common

    nc = _MockNetworkConfig(provisioning_base_url="http://pbx.example.com")
    settings = _MockSettings()
    content = render_yealink_common(network_config=nc, settings=settings)

    assert "yealink.xml" in content


# ---------------------------------------------------------------------------
# render_yealink_config
# ---------------------------------------------------------------------------


def test_yealink_config_contains_account_password() -> None:
    """Yealink 端末固有設定に account.1.password が含まれる。"""
    from millicall.provisioning.templates import render_yealink_config

    ext = _MockExtension(sip_password="YlnkPass123")
    nc = _MockNetworkConfig()
    settings = _MockSettings()
    content = render_yealink_config(extension=ext, network_config=nc, settings=settings)

    assert "account.1.password" in content
    assert "YlnkPass123" in content


def test_yealink_config_contains_sip_server_address() -> None:
    """Yealink 端末固有設定に account.1.sip_server.1.address が含まれる。"""
    from millicall.provisioning.templates import render_yealink_config

    ext = _MockExtension()
    nc = _MockNetworkConfig(lan_ip="10.20.0.1")
    settings = _MockSettings()
    content = render_yealink_config(extension=ext, network_config=nc, settings=settings)

    assert "account.1.sip_server.1.address" in content
    assert "10.20.0.1" in content


def test_yealink_config_display_name_sanitized() -> None:
    """Yealink 設定の表示名から改行が除去される。"""
    from millicall.provisioning.templates import render_yealink_config

    ext = _MockExtension(display_name="Alice\r\nBob")
    nc = _MockNetworkConfig()
    settings = _MockSettings()
    content = render_yealink_config(extension=ext, network_config=nc, settings=settings)

    assert "Alice\r\nBob" not in content


# ---------------------------------------------------------------------------
# render_panasonic_phonebook
# ---------------------------------------------------------------------------


def test_panasonic_phonebook_contains_contact() -> None:
    """Panasonic 電話帳に連絡先の名前と番号が含まれる。"""
    from millicall.provisioning.templates import render_panasonic_phonebook

    contacts = [{"name": "山田太郎", "phone_number": "0312345678"}]
    xml_bytes = render_panasonic_phonebook(contacts)

    assert "山田太郎".encode() in xml_bytes
    assert b"0312345678" in xml_bytes


def test_panasonic_phonebook_is_xml() -> None:
    """Panasonic 電話帳は XML 宣言付きバイト列として返される。"""
    from millicall.provisioning.templates import render_panasonic_phonebook

    contacts = []
    xml_bytes = render_panasonic_phonebook(contacts)

    assert xml_bytes.startswith(b"<?xml")
    assert b"PhoneDirectory" in xml_bytes


def test_panasonic_phonebook_with_object_contacts() -> None:
    """ORM オブジェクト（属性アクセス）でも正しく動作する。"""
    from millicall.provisioning.templates import render_panasonic_phonebook

    class _FakeContact:
        def __init__(self, name: str, phone_number: str) -> None:
            self.name = name
            self.phone_number = phone_number

    contacts = [_FakeContact("鈴木花子", "0901234567")]
    xml_bytes = render_panasonic_phonebook(contacts)

    assert "鈴木花子".encode() in xml_bytes
    assert b"0901234567" in xml_bytes


# ---------------------------------------------------------------------------
# render_yealink_phonebook
# ---------------------------------------------------------------------------


def test_yealink_phonebook_contains_contact() -> None:
    """Yealink 電話帳に連絡先の名前と番号が含まれる。"""
    from millicall.provisioning.templates import render_yealink_phonebook

    contacts = [{"name": "佐藤次郎", "phone_number": "0661234567"}]
    xml_bytes = render_yealink_phonebook(contacts)

    assert "佐藤次郎".encode() in xml_bytes
    assert b"0661234567" in xml_bytes


def test_yealink_phonebook_is_xml() -> None:
    """Yealink 電話帳は XML 宣言付きバイト列として返される。"""
    from millicall.provisioning.templates import render_yealink_phonebook

    contacts = []
    xml_bytes = render_yealink_phonebook(contacts)

    assert xml_bytes.startswith(b"<?xml")
    assert b"YealinkIPPhoneDirectory" in xml_bytes
