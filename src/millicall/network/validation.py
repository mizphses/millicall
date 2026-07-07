"""ネットワーク設定の入力バリデーションヘルパ。

ネットワーク呼び出しを一切行わない純粋関数群。
API (Phase 5 T4) と netd (Phase 5 T2) の双方から利用する単一の真実の源泉。
"""
import ipaddress
import re

# Linux IFNAMSIZ = 16。有効文字: 英数・アンダースコア・ハイフン・ドット。
_IF_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,15}$")

# Tailscale 認証キーのプレフィックスパターン。
_TSKEY_RE = re.compile(r"^tskey-[A-Za-z0-9\-]+$")


def is_valid_interface(name: str) -> bool:
    """Linux ネットワークインタフェース名として有効かどうかを返す。

    IFNAMSIZ = 16 なので最大 15 文字。シェルメタ文字・スペース等は拒否する。
    """
    return bool(_IF_RE.match(name))


def validate_ipv4(addr: str) -> None:
    """有効な IPv4 アドレスでなければ ValueError を送出する。

    Args:
        addr: 検証するアドレス文字列。

    Raises:
        ValueError: 有効な IPv4 アドレスでない場合。
    """
    try:
        ipaddress.IPv4Address(addr)
    except ValueError as exc:
        raise ValueError(f"無効な IPv4 アドレス: {addr!r}") from exc


def validate_ipv4_range(start: str, end: str) -> None:
    """start ≤ end であることを含め DHCP レンジとして有効か検証する。

    Args:
        start: レンジ開始 IP アドレス文字列。
        end: レンジ終了 IP アドレス文字列。

    Raises:
        ValueError: どちらかが無効な IPv4 か、start > end の場合。
    """
    validate_ipv4(start)
    validate_ipv4(end)
    int_start = int(ipaddress.IPv4Address(start))
    int_end = int(ipaddress.IPv4Address(end))
    if int_start > int_end:
        raise ValueError(
            f"DHCP レンジの開始アドレス {start!r} が終了アドレス {end!r} より大きい"
        )


def validate_cidr_prefix(prefix: int) -> None:
    """CIDR プレフィックス長として有効（0〜32）かどうかを検証する。

    Args:
        prefix: 検証するプレフィックス長。

    Raises:
        ValueError: 0〜32 の範囲外の場合。
    """
    if not (0 <= prefix <= 32):
        raise ValueError(f"CIDR プレフィックス長は 0〜32 でなければなりません: {prefix}")


def is_valid_tailscale_authkey(key: str) -> bool:
    """Tailscale 認証キーとして有効な形式かどうかを返す。

    期待フォーマット: ``tskey-`` で始まり英数字とハイフンのみ。
    """
    return bool(_TSKEY_RE.match(key))


def normalize_mac(mac: str) -> str:
    """MAC アドレスを大文字コロン区切り形式（AA:BB:CC:DD:EE:FF）に正規化する。

    セパレータとして ``:`` / ``-`` / ``.`` を受け付け、12 桁の16進数を期待する。

    Args:
        mac: 正規化前の MAC アドレス文字列。

    Returns:
        大文字コロン区切り形式の MAC アドレス（例: ``AA:BB:CC:DD:EE:FF``）。

    Raises:
        ValueError: 形式が不正な場合。
    """
    # セパレータを除去して純粋な16進数列を得る
    stripped = mac.replace(":", "").replace("-", "").replace(".", "")
    if len(stripped) != 12 or not all(c in "0123456789abcdefABCDEF" for c in stripped):
        raise ValueError(f"不正な MAC アドレス形式: {mac!r}")
    # 2文字ずつコロンで結合して大文字化
    return ":".join(stripped[i : i + 2].upper() for i in range(0, 12, 2))
