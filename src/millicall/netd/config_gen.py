"""netd 設定ファイル生成モジュール。

純粋関数群。副作用なし、ファイルシステム/ネットワーク呼び出しなし。
すべての引数はこのモジュール内で再検証する（defense in depth）。

**セキュリティ注意**:
- 設定ファイルへのインジェクション（改行、シェルメタ文字）を防ぐため、
  生成前にすべての入力を厳格に検証する。
- provisioning_url は改行・空白・シェルメタ文字を含んではならない。
  dnsmasq.conf の dhcp-option 行に直接書き込まれるためインジェクションリスクがある。
- nftables ルールセットは ipaddress モジュールで CIDR を安全に構築する。
"""

import ipaddress
import re

from millicall.network.validation import (
    is_valid_interface,
    validate_cidr_prefix,
    validate_ipv4,
    validate_ipv4_range,
)

# dnsmasq.conf の dhcp-option 行に許容する URL パターン。
# http://<IPv4 アドレス>:<ポート>/<パス> のみ許可。
# HTTPS / ホスト名 / シェルメタ文字 / 空白 / 改行はすべて拒否。
_PROVISIONING_URL_RE = re.compile(
    r"^http://(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d{1,5})(/[^\s\r\n]*)?$"
)

# シェルメタ文字・改行・制御文字を検出する正規表現
_DANGEROUS_CHARS_RE = re.compile(r'[\r\n\x00-\x1f\x7f;|&`$()\\<>"\']')


def _validate_provisioning_url(url: str, lan_ip: str) -> None:
    """プロビジョニング URL を検証する。

    - http スキームのみ許可（HTTPS は証明書管理が必要なため LAN 内では http）
    - ホスト部分は lan_ip と完全一致しなければならない
    - ポートは 1–65535 の範囲
    - 空白・改行・シェルメタ文字を含まない
    - dnsmasq.conf 行インジェクション防止

    Args:
        url: 検証する URL 文字列。
        lan_ip: LAN インターフェイスの IP アドレス（ホスト部と照合する）。

    Raises:
        ValueError: URL が不正な場合。
    """
    if not url:
        raise ValueError("provisioning_url は空にできません")

    if _DANGEROUS_CHARS_RE.search(url):
        raise ValueError(
            "provisioning_url にシェルメタ文字・改行・制御文字が含まれています"
        )

    m = _PROVISIONING_URL_RE.match(url)
    if not m:
        raise ValueError(
            f"provisioning_url は http://<lan_ip>:<port>/... の形式でなければなりません: {url!r}"
        )

    host_in_url = m.group(1)
    port_str = m.group(2)

    if host_in_url != lan_ip:
        raise ValueError(
            f"provisioning_url のホスト ({host_in_url!r}) が "
            f"lan_ip ({lan_ip!r}) と一致しません"
        )

    port = int(port_str)
    if not (1 <= port <= 65535):
        raise ValueError(f"provisioning_url のポート {port} は 1–65535 の範囲外です")


def _cidr_to_netmask(prefix: int) -> str:
    """CIDR プレフィックス長をドット区切りサブネットマスクに変換する。

    Args:
        prefix: CIDR プレフィックス長（0–32）。

    Returns:
        ドット区切りサブネットマスク文字列（例: "255.255.0.0"）。
    """
    network = ipaddress.IPv4Network(f"0.0.0.0/{prefix}", strict=False)
    return str(network.netmask)


def render_dnsmasq_conf(
    *,
    lan_interface: str,
    lan_ip: str,
    dhcp_range_start: str,
    dhcp_range_end: str,
    dhcp_lease_hours: int,
    provisioning_url: str,
    lan_prefix: int = 16,
) -> str:
    """dnsmasq 設定ファイル文字列を生成する。

    旧 setup-host-network.sh の dnsmasq.conf 構造を踏襲しつつ、
    nftables 対応・Jinja2 不使用で純粋関数として実装する。

    Args:
        lan_interface: LAN インターフェイス名（例: "enp3s0"）。
        lan_ip: LAN IP アドレス（例: "172.20.0.1"）。DHCP サーバのゲートウェイ兼 DNS。
        dhcp_range_start: DHCP 払い出し開始 IP。
        dhcp_range_end: DHCP 払い出し終了 IP。
        dhcp_lease_hours: リース時間（時間）。
        provisioning_url: TFTP/HTTP プロビジョニング URL（option 66）。
                         http://<lan_ip>:<port>/... 形式のみ許可。
        lan_prefix: LAN サブネットの CIDR プレフィックス長（デフォルト: 16）。

    Returns:
        dnsmasq.conf の内容文字列。

    Raises:
        ValueError: いずれかの引数が不正な場合。
    """
    # --- 入力検証（defense in depth: validation.py の関数を再利用） ---
    if not is_valid_interface(lan_interface):
        raise ValueError(f"不正なインターフェイス名: {lan_interface!r}")
    validate_ipv4(lan_ip)
    validate_ipv4_range(dhcp_range_start, dhcp_range_end)
    validate_cidr_prefix(lan_prefix)
    if not isinstance(dhcp_lease_hours, int) or dhcp_lease_hours < 1:
        raise ValueError(f"dhcp_lease_hours は 1 以上の整数でなければなりません: {dhcp_lease_hours!r}")
    _validate_provisioning_url(provisioning_url, lan_ip)

    netmask = _cidr_to_netmask(lan_prefix)

    lines = [
        "# Millicall netd が自動生成した設定ファイルです。手動編集は上書きされます。",
        f"interface={lan_interface}",
        "bind-interfaces",
        f"dhcp-range={dhcp_range_start},{dhcp_range_end},{netmask},{dhcp_lease_hours}h",
        f"dhcp-option=3,{lan_ip}",
        f"dhcp-option=6,{lan_ip}",
        f"dhcp-option=66,{provisioning_url}",
        "",  # 末尾改行のための空行
    ]
    return "\n".join(lines)


def render_nftables_ruleset(
    *,
    enabled: bool,
    lan_ip: str,
    lan_prefix: int,
    wan_interface: str,
    table_name: str = "millicall_nat",
) -> str:
    """nftables マスカレードルールセット文字列を生成する。

    ``nft -f -`` の標準入力に渡すことを想定した nftables ルールセットを返す。
    enabled=False の場合はテーブルのフラッシュのみ行う安全なルールセットを返す。

    ip_forward の有効化は commands.py 側で sysctl 経由で行うため、
    このモジュールでは nftables ルールのみを生成する。

    Args:
        enabled: True のとき NAT マスカレードを有効化するルールを生成する。
                 False のとき既存テーブルをフラッシュ（削除）するルールを生成する。
        lan_ip: LAN IP アドレス（CIDR のホスト部）。
        lan_prefix: CIDR プレフィックス長。
        wan_interface: WAN インターフェイス名（マスカレード出口）。
        table_name: nftables テーブル名（デフォルト: "millicall_nat"）。

    Returns:
        nftables ルールセット文字列（``nft -f -`` への stdin として使用）。

    Raises:
        ValueError: いずれかの引数が不正な場合。
    """
    # --- 入力検証 ---
    validate_ipv4(lan_ip)
    validate_cidr_prefix(lan_prefix)
    if not is_valid_interface(wan_interface):
        raise ValueError(f"不正な WAN インターフェイス名: {wan_interface!r}")

    # ipaddress モジュールを使って CIDR を安全に構築する（文字列結合でのインジェクション防止）
    network = ipaddress.IPv4Network(f"{lan_ip}/{lan_prefix}", strict=False)
    cidr_str = str(network)  # 例: "172.20.0.0/16"

    if enabled:
        lines = [
            "# Millicall netd が自動生成した nftables ルールセットです。",
            f"table ip {table_name} {{",
            "  chain postrouting {",
            "    type nat hook postrouting priority 100; policy accept;",
            f"    ip saddr {cidr_str} oif {wan_interface!r} masquerade",
            "  }",
            "}",
            "",
        ]
    else:
        # NAT 無効時はテーブルを削除する
        lines = [
            "# Millicall netd: NAT 無効 — テーブルを削除します。",
            f"delete table ip {table_name}",
            "",
        ]
    return "\n".join(lines)
