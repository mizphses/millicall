"""プロビジョニング設定ファイル生成 — 純粋関数群。

Panasonic KX-HDV および Yealink 向けの設定ファイルテキストと
電話帳 XML を生成する。DB アクセスは一切行わず、テスト容易性を保つ。

セキュリティ注意:
  - extension.number / sip_password / display_name はサーバー制御値のため直接補間する。
  - display_name には改行・クォートが含まれうるため各フォーマットに応じてエスケープする。
  - MAC アドレスやホスト名は設定ファイルに埋め込まない。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from xml.etree.ElementTree import Element, SubElement, tostring

from millicall.config import http_port_suffix

if TYPE_CHECKING:
    from millicall.config import Settings
    from millicall.models import Extension, NetworkConfig


def _panasonic_escape(value: str) -> str:
    """Panasonic cfg の二重引用符内に埋め込む文字列をエスケープする。

    二重引用符をバックスラッシュでエスケープし、改行・キャリッジリターンを除去する。
    """
    return value.replace('"', '\\"').replace("\n", "").replace("\r", "")


def _yealink_escape(value: str) -> str:
    """Yealink cfg の key = value 形式に埋め込む文字列をエスケープする。

    改行・キャリッジリターンを除去する（値の途中での行割りを防ぐ）。
    """
    return value.replace("\n", "").replace("\r", "")


def _provisioning_base(network_config: NetworkConfig, settings: Settings) -> str:
    """provisioning_base_url が設定されていればそれを、なければ LAN IP + core の HTTP ポートを返す。

    標準ポート 80 は URL から省略する（`http://<lan_ip>/`）。
    """
    if network_config.provisioning_base_url:
        return network_config.provisioning_base_url.rstrip("/")
    return f"http://{network_config.lan_ip}{http_port_suffix(settings.http_port)}"


# ---------------------------------------------------------------------------
# Panasonic KX-HDV
# ---------------------------------------------------------------------------


def render_panasonic_common(
    *,
    network_config: NetworkConfig,
    settings: Settings,
) -> str:
    """ConfigCommon.cfg を生成する（全 Panasonic 機種共通設定）。

    行区切りは CRLF（Panasonic 標準）。
    """
    base = _provisioning_base(network_config, settings)
    lan_ip = network_config.lan_ip
    lines = [
        "# Panasonic SIP Phone Standard Format File #",
        "# DO NOT CHANGE THIS LINE!",
        "",
        "# Millicall PBX - Common Configuration",
        "",
        "# Provisioning",
        f'CFG_STANDARD_FILE_PATH="{base}/provisioning/Panasonic/Config{{MAC}}.cfg"',
        f'CFG_MASTER_FILE_PATH="{base}/provisioning/Panasonic/ConfigCommon.cfg"',
        'CFG_RESYNC_TIME="02:00"',
        'CFG_RESYNC_FROM_SIP="Y"',
        "",
        "# NTP",
        f'NTP_ADDR="{lan_ip}"',
        'TIME_ZONE="GMT +9:00"',
        'DST_ENABLE="N"',
        "",
        "# Language",
        'DEFAULT_LANGUAGE="jp"',
        'WEB_LANGUAGE="jp"',
        "",
        "# DNS SRV disabled",
        'SIP_DNSSRV_ENA_1="N"',
        'SIP_DNSSRV_ENA_2="N"',
        "",
        "# Tone settings - Japanese (TTC standard)",
        "",
        "# Dial tone 1: 400Hz continuous",
        'DIAL_TONE1_FRQ="400"',
        'DIAL_TONE1_TIMING="0"',
        'DIAL_TONE1_RPT="1"',
        "",
        "# Ringback tone: 400+415Hz, 1s ON / 2s OFF",
        'RINGBACK_TONE_FRQ="400,415"',
        'RINGBACK_TONE_TIMING="60,1000,2000"',
        'RINGBACK_TONE_RPT="1"',
        "",
        "# Busy tone: 400Hz, 0.5s ON / 0.5s OFF",
        'BUSY_TONE_FRQ="400"',
        'BUSY_TONE_TIMING="60,500,500"',
        'BUSY_TONE_RPT="1"',
        "",
        "# Reorder tone (congestion): 400Hz, 0.25s ON / 0.19s OFF",
        'REORDER_TONE_FRQ="400"',
        'REORDER_TONE_TIMING="60,250,190"',
        'REORDER_TONE_RPT="1"',
        'REORDER_TONE_ENABLE="Y"',
        "",
        "# Remote Phonebook",
        f'XMLAPP_LDAP_URL="{base}/provisioning/phonebook/panasonic.xml"',
        "",
    ]
    return "\r\n".join(lines) + "\r\n"


def render_panasonic_config(
    *,
    extension: Extension,
    network_config: NetworkConfig,
    settings: Settings,
) -> str:
    """Config{{MAC}}.cfg を生成する（端末固有設定）。

    SIP 認証情報（number / sip_password）と表示名を埋め込む。
    行区切りは CRLF（Panasonic 標準）。
    """
    lan_ip = network_config.lan_ip
    number = extension.number
    display = _panasonic_escape(extension.display_name)
    password = extension.sip_password
    lines = [
        "# Panasonic SIP Phone Standard Format File #",
        "# DO NOT CHANGE THIS LINE!",
        "",
        f"# Millicall PBX - Extension {number}",
        "",
        "# SIP Settings - Line 1",
        f'PHONE_NUMBER_1="{number}"',
        f'SIP_RGSTR_ADDR_1="{lan_ip}"',
        'SIP_RGSTR_PORT_1="5060"',
        f'SIP_PRXY_ADDR_1="{lan_ip}"',
        'SIP_PRXY_PORT_1="5060"',
        f'SIP_OUTPROXY_ADDR_1="{lan_ip}"',
        'SIP_OUTPROXY_PORT_1="5060"',
        # SIP サービスドメインは登録先（internal プロファイル）のドメインと一致させる。
        # 子LAN では internal は lan_ip をドメインにするため、ここも lan_ip にしないと
        # 電話が <ext>@<sip_domain> で登録して "Can't find user" になる（Yealink は
        # サーバ=lan_ip をそのままドメインに使うため一致していた。Panasonic のみ明示ずれ）。
        f'SIP_SVCDOMAIN_1="{lan_ip}"',
        f'SIP_AUTHID_1="{number}"',
        f'SIP_PASS_1="{password}"',
        f'SIP_URI_1="{number}"',
        'SIP_DNSSRV_ENA_1="N"',
        "",
        "# Registration",
        'REG_EXPIRE_TIME_1="300"',
        "",
        "# Display",
        f'DISPLAY_NAME_1="{display}"',
        "",
        "# Codec Settings - Line 1",
        "# Enable PCMU (G.711u)",
        'CODEC_ENABLE4_1="Y"',
        'CODEC_PRIORITY4_1="1"',
        "# Enable PCMA (G.711a)",
        'CODEC_ENABLE1_1="Y"',
        'CODEC_PRIORITY1_1="2"',
        "",
    ]
    return "\r\n".join(lines) + "\r\n"


def render_panasonic_model_file(
    *,
    network_config: NetworkConfig,
    settings: Settings,
) -> str:
    """{MODEL}.cfg（プレプロビジョニング入口ファイル）を生成する。

    Panasonic KX-HDV は DHCP option 66 = ``http://<lan_ip>/provisioning/``（末尾 ``/``）の
    とき、最初に ``{MODEL}.cfg``（例 ``KX-HDV130N.cfg``）を入口ファイルとして GET する。
    この入口ファイルは CFG_STANDARD_FILE_PATH / CFG_MASTER_FILE_PATH を設定するだけで、
    そこから実設定（Config{MAC}.cfg・ConfigCommon.cfg）へ連鎖する。

    ``{MAC}`` は電話機側が置換するマクロのため、リテラル文字列としてそのまま出力する
    （Python 側では展開しない）。行区切りは LF。
    """
    base = _provisioning_base(network_config, settings)
    lines = [
        # Panasonic は設定ファイル先頭にこのマジック行が必須。無いとフォーマット不正で
        # ファイル全体を破棄し、CFG_*_FILE_PATH も読まれず実設定へ連鎖しない（実機で発覚）。
        "# Panasonic SIP Phone Standard Format File #",
        "# DO NOT CHANGE THIS LINE!",
        "",
        "# Millicall PBX - Panasonic pre-provisioning (model entry)",
        f'CFG_STANDARD_FILE_PATH="{base}/provisioning/Panasonic/Config{{MAC}}.cfg"',
        f'CFG_MASTER_FILE_PATH="{base}/provisioning/Panasonic/ConfigCommon.cfg"',
    ]
    # 他の Panasonic ファイルと同じく CRLF 区切り。
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------------------
# Yealink
# ---------------------------------------------------------------------------


def render_yealink_boot(
    *,
    network_config: NetworkConfig,
    settings: Settings,
) -> str:
    """y000000000000.boot を生成する（全 Yealink 機種が起動時に取得するブートファイル）。

    行区切りは LF。
    """
    base = _provisioning_base(network_config, settings)
    lines = [
        "#!version:1.0.0.1",
        "",
        f"include:config <{base}/provisioning/Yealink/common.cfg>",
        f"include:config <{base}/provisioning/Yealink/$MAC.cfg>",
        "",
        "overwrite_mode = 1",
        "specific_model.excluded_mode = 0",
    ]
    return "\n".join(lines) + "\n"


def render_yealink_common(
    *,
    network_config: NetworkConfig,
    settings: Settings,
) -> str:
    """common.cfg を生成する（全 Yealink 機種共通設定）。

    行区切りは LF。
    """
    base = _provisioning_base(network_config, settings)
    lan_ip = network_config.lan_ip
    lines = [
        "#!version:1.0.0.1",
        "",
        "## Millicall PBX - Yealink Common Configuration",
        "",
        "## Auto Provisioning",
        f"static.auto_provision.server.url = {base}/provisioning/Yealink",
        "static.auto_provision.power_on = 1",
        "static.auto_provision.repeat.enable = 1",
        "static.auto_provision.repeat.minutes = 1440",
        "",
        "## NTP / Timezone (Asia/Tokyo, +9)",
        f"local_time.ntp_server1 = {lan_ip}",
        "local_time.ntp_server2 = ntp.nict.jp",
        "local_time.time_zone = +9",
        "local_time.time_zone_name = Japan",
        "local_time.summer_time = 0",
        "local_time.date_format = 2",
        "local_time.time_format = 1",
        "",
        "## Language",
        "lang.gui = Japanese",
        "lang.wui = Japanese",
        "",
        "## Tone - Japanese (TTC standard)",
        "## Format: freq1*gain1+freq2*gain2/on_duration,freq/off_duration",
        "voice.tone.country = Custom",
        "voice.tone.dial = 400/0",
        "voice.tone.ring = 400+15/1000,0/2000",
        "voice.tone.busy = 400/500,0/500",
        "voice.tone.congestion = 400/250,0/190",
        "voice.tone.callwaiting = 400/200,0/600,400/200,0/3000",
        "voice.tone.dialrecall = 400/200,0/200,400/200,0/200,400/200,0/200,400/0",
        "voice.tone.info = 950/330,1400/330,1800/330,0/1000",
        "voice.tone.stutter = 400/100,0/100,400/100,0/100,400/100,0/100,400/0",
        "",
        "## Remote Phonebook",
        f"remote_phonebook.data.1.url = {base}/provisioning/phonebook/yealink.xml",
        "remote_phonebook.data.1.name = Millicall",
        "",
    ]
    return "\n".join(lines) + "\n"


def render_yealink_config(
    *,
    extension: Extension,
    network_config: NetworkConfig,
    settings: Settings,
) -> str:
    """{mac}.cfg を生成する（端末固有設定）。

    SIP 認証情報（number / sip_password）と表示名を埋め込む。
    行区切りは LF。
    """
    lan_ip = network_config.lan_ip
    number = extension.number
    display = _yealink_escape(extension.display_name)
    password = extension.sip_password
    lines = [
        "#!version:1.0.0.1",
        "",
        f"## Millicall PBX - Extension {number}",
        "",
        "## Account 1 - Registration",
        "account.1.enable = 1",
        f"account.1.label = {number}",
        f"account.1.display_name = {display}",
        f"account.1.auth_name = {number}",
        f"account.1.user_name = {number}",
        f"account.1.password = {password}",
        f"account.1.sip_server.1.address = {lan_ip}",
        "account.1.sip_server.1.port = 5060",
        "account.1.sip_server.1.transport_type = 0",
        "account.1.sip_server.1.expires = 300",
        "",
        "## Codec (G.711u priority 1, G.711a priority 2, G.722 priority 3)",
        "account.1.codec.pcmu.enable = 1",
        "account.1.codec.pcmu.priority = 1",
        "account.1.codec.pcma.enable = 1",
        "account.1.codec.pcma.priority = 2",
        "account.1.codec.g722.enable = 1",
        "account.1.codec.g722.priority = 3",
        "account.1.codec.g729.enable = 0",
        "",
        "## NAT",
        "account.1.nat.nat_traversal = 0",
        "account.1.nat.udp_update_enable = 1",
        "account.1.nat.udp_update_time = 30",
        "account.1.nat.rport = 1",
        "",
        "## DTMF (RFC2833)",
        "account.1.dtmf.type = 1",
        "account.1.dtmf.dtmf_payload = 101",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Phonebook XML
# ---------------------------------------------------------------------------


def render_panasonic_phonebook(contacts: list) -> bytes:
    """Panasonic XML 電話帳を生成する。

    Args:
        contacts: ``name`` 属性と ``phone_number`` 属性を持つオブジェクトのリスト
                  （または辞書のリスト）。

    Returns:
        UTF-8 エンコードの XML バイト列。
    """
    root = Element("PhoneDirectory")
    for c in contacts:
        name = c["name"] if isinstance(c, dict) else c.name
        phone = c["phone_number"] if isinstance(c, dict) else c.phone_number
        entry = SubElement(root, "DirectoryEntry")
        SubElement(entry, "Name").text = name
        SubElement(entry, "Telephone").text = phone
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode(
        "utf-8"
    )


def render_yealink_phonebook(contacts: list) -> bytes:
    """Yealink XML 電話帳を生成する。

    Args:
        contacts: ``name`` 属性と ``phone_number`` 属性を持つオブジェクトのリスト
                  （または辞書のリスト）。

    Returns:
        UTF-8 エンコードの XML バイト列。
    """
    root = Element("YealinkIPPhoneDirectory")
    for c in contacts:
        name = c["name"] if isinstance(c, dict) else c.name
        phone = c["phone_number"] if isinstance(c, dict) else c.phone_number
        entry = SubElement(root, "DirectoryEntry")
        SubElement(entry, "Name").text = name
        tel = SubElement(entry, "Telephone")
        tel.text = phone
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode(
        "utf-8"
    )
