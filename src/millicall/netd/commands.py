"""netd コマンドハンドラ。

各ハンドラは ``async (payload: dict, ops: SystemOps, settings) -> dict`` の形式。
リクエストの JSON ペイロードを受け取り、処理結果を dict で返す。

**セキュリティ原則**:
- すべての入力は network/validation.py の関数で再検証する（core が検証済みでも信頼しない）。
- 検証失敗時は ops を一切呼ばず {"ok": false, "error": "..."} を返す。
- Tailscale 認証キーはエラーメッセージ・ログに絶対に含めない。
- シェルメタ文字・インジェクションを防ぐため、コマンドは常に argv リストで渡す。
"""

import json
import logging
import re
import shlex
from typing import Any

from millicall.netd.config_gen import render_dnsmasq_conf, render_nftables_ruleset
from millicall.netd.system import SystemOps
from millicall.network.validation import (
    is_valid_hostname,
    is_valid_interface,
    is_valid_tailscale_authkey,
    normalize_mac,
    validate_cidr_prefix,
    validate_ipv4,
    validate_ipv4_range,
)

logger = logging.getLogger("millicall.netd.commands")

# tailscale の出力に混入し得る認証キーを除去する（秘密情報漏洩防止）。
_TSKEY_REDACT_RE = re.compile(r"tskey-\S+")


def _err(msg: str) -> dict:
    """エラーレスポンスを返すヘルパ。"""
    return {"ok": False, "error": msg}


def _ok(**kwargs: Any) -> dict:
    """成功レスポンスを返すヘルパ。"""
    return {"ok": True, **kwargs}


async def apply_dhcp(
    payload: dict,
    ops: SystemOps,
    settings: Any,
) -> dict:
    """DHCP サーバ設定を適用する。

    dnsmasq.conf を生成して書き込み、dnsmasq を再起動する。

    ペイロードフィールド:
        lan_interface (str): LAN インターフェイス名。
        lan_ip (str): LAN IP アドレス。
        dhcp_range_start (str): DHCP 払い出し開始 IP。
        dhcp_range_end (str): DHCP 払い出し終了 IP。
        dhcp_lease_hours (int): リース時間（時間）。デフォルト 12。
        provisioning_url (str): プロビジョニング URL (http://<lan_ip>:<port>/...)。
        lan_prefix (int): LAN サブネット CIDR プレフィックス長。デフォルト 16。
    """
    lan_interface = payload.get("lan_interface", "")
    lan_ip = payload.get("lan_ip", "")
    dhcp_range_start = payload.get("dhcp_range_start", "")
    dhcp_range_end = payload.get("dhcp_range_end", "")
    dhcp_lease_hours = payload.get("dhcp_lease_hours", 12)
    provisioning_url = payload.get("provisioning_url", "")
    lan_prefix = payload.get("lan_prefix", 16)

    # --- 入力再検証 (defense in depth) ---
    if not is_valid_interface(str(lan_interface)):
        return _err(f"不正なインターフェイス名: {lan_interface!r}")
    try:
        validate_ipv4(str(lan_ip))
        validate_ipv4_range(str(dhcp_range_start), str(dhcp_range_end))
        validate_cidr_prefix(int(lan_prefix))
    except (ValueError, TypeError) as exc:
        return _err(f"入力検証エラー: {exc}")

    if not isinstance(dhcp_lease_hours, int) or dhcp_lease_hours < 1:
        return _err(f"dhcp_lease_hours は 1 以上の整数でなければなりません: {dhcp_lease_hours!r}")

    if not provisioning_url:
        return _err("provisioning_url は必須です")

    # config_gen 内でも再検証されるが、ここでも明示的に呼ぶ
    try:
        conf = render_dnsmasq_conf(
            lan_interface=str(lan_interface),
            lan_ip=str(lan_ip),
            dhcp_range_start=str(dhcp_range_start),
            dhcp_range_end=str(dhcp_range_end),
            dhcp_lease_hours=int(dhcp_lease_hours),
            provisioning_url=str(provisioning_url),
            lan_prefix=int(lan_prefix),
        )
    except ValueError as exc:
        return _err(f"設定生成エラー: {exc}")

    try:
        ops.write_file(settings.dnsmasq_conf_path, conf)
    except OSError as exc:
        return _err(f"設定ファイル書き込みエラー: {exc}")

    reload_argv = shlex.split(settings.dnsmasq_reload_cmd)
    if not reload_argv:
        return _err("dnsmasq_reload_cmd が空です")

    rc, _stdout, stderr = await ops.run(reload_argv)
    if rc != 0:
        return _err(f"dnsmasq 再起動失敗 (rc={rc}): {stderr[:200]}")

    return _ok()


async def apply_nat(
    payload: dict,
    ops: SystemOps,
    settings: Any,
) -> dict:
    """NAT マスカレード設定 + HTTP INPUT フィルタを適用する。

    nftables ルールセットを生成して ``nft -f -`` に渡す。
    enabled=True の場合は ip_forward も有効化する。
    INPUT フィルタ（millicall_filter テーブル）は enabled 値にかかわらず常に適用する。

    ペイロードフィールド:
        enabled (bool): True で NAT 有効、False で無効（NAT テーブル削除）。
        lan_ip (str): LAN IP アドレス。
        lan_prefix (int): LAN CIDR プレフィックス長。
        wan_interface (str): WAN インターフェイス名。
        http_port (int): core の HTTP ポート番号（省略時: 80）。
                        INPUT フィルタの LAN 許可 / WAN DROP に使用する。
    """
    enabled = payload.get("enabled", True)
    lan_ip = payload.get("lan_ip", "")
    lan_prefix = payload.get("lan_prefix", 16)
    wan_interface = payload.get("wan_interface", "")
    # http_port は省略可能（後方互換性のため既定値 80 にフォールバック）
    http_port = payload.get("http_port", 80)

    # --- 入力再検証 ---
    try:
        validate_ipv4(str(lan_ip))
        validate_cidr_prefix(int(lan_prefix))
    except (ValueError, TypeError) as exc:
        return _err(f"入力検証エラー: {exc}")

    if not is_valid_interface(str(wan_interface)):
        return _err(f"不正な WAN インターフェイス名: {wan_interface!r}")

    if not isinstance(enabled, bool):
        return _err(f"enabled は bool でなければなりません: {enabled!r}")

    try:
        http_port_int = int(http_port)
    except (TypeError, ValueError):
        return _err(f"http_port は整数でなければなりません: {http_port!r}")
    if not (1 <= http_port_int <= 65535):
        return _err(f"http_port は 1–65535 の範囲でなければなりません: {http_port_int!r}")

    try:
        ruleset = render_nftables_ruleset(
            enabled=bool(enabled),
            lan_ip=str(lan_ip),
            lan_prefix=int(lan_prefix),
            wan_interface=str(wan_interface),
            http_port=http_port_int,
            table_name=settings.nftables_table,
        )
    except ValueError as exc:
        return _err(f"ルールセット生成エラー: {exc}")

    # NAT 有効時は ip_forward を先に有効化する
    if enabled:
        rc_fwd, _out, _err_str = await ops.run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
        if rc_fwd != 0:
            logger.warning("ip_forward の有効化に失敗しました (rc=%d)", rc_fwd)

    rc, _stdout, stderr = await ops.run(["nft", "-f", "-"], input_text=ruleset)
    if rc != 0:
        return _err(f"nft 適用失敗 (rc={rc}): {stderr[:200]}")

    return _ok()


async def tailscale_up(
    payload: dict,
    ops: SystemOps,
    settings: Any,
) -> dict:
    """Tailscale VPN を起動する。

    認証キーを検証し、tailscale up を実行する。
    認証キーはエラーメッセージ・ログに絶対に含めない。

    ペイロードフィールド:
        auth_key (str): Tailscale 認証キー（tskey- プレフィックス必須）。
    """
    auth_key = payload.get("auth_key", "")

    # ログイン状態を確認し、未ログイン時のみ auth key を渡す。
    # one-time キーは一度使うと無効になるため、切断→再接続のたびにキーを渡すと
    # 二度と繋がらなくなる（「一回切れるとずっと切断中」）。ログイン済み
    # (state 保持) なら `tailscale up` のみで再接続でき、キーを消費しない。
    # status が読めない場合は安全側（未ログイン扱い）でキーを渡す。
    needs_login = True
    st_rc, st_out, _st_err = await ops.run(["tailscale", "status", "--json"])
    if st_rc == 0:
        try:
            needs_login = json.loads(st_out).get("BackendState") == "NeedsLogin"
        except json.JSONDecodeError:
            needs_login = True

    up_args = ["tailscale", "up", "--accept-dns=false"]
    if needs_login:
        # 認証キーの検証（失敗時もキーをエラーに含めない）
        if not isinstance(auth_key, str) or not is_valid_tailscale_authkey(auth_key):
            # キーの値をエラーメッセージに含めない（秘密情報の漏洩防止）
            return _err("無効な Tailscale 認証キー形式です")
        up_args += ["--authkey", auth_key]

    rc, _stdout, stderr = await ops.run(up_args)
    if rc != 0:
        # 秘密情報保護: 切り詰める前に完全な stderr から tskey を除去する
        # （境界を跨ぐ部分一致漏洩を防ぐ。exact 一致だけでなく tskey-\S+ を正規表現で除去）。
        redacted = _TSKEY_REDACT_RE.sub("(redacted)", stderr or "")
        safe_stderr = redacted[:200]
        return _err(f"tailscale up 失敗 (rc={rc}): {safe_stderr}")

    # tailscale_serve_enabled のとき、up 成功後に tailnet 上で HTTPS を張り
    # http://localhost:<http_port> を公開する（管理画面/MCP のリモート公開）。
    # serve の失敗は up 自体の成功を覆さない（警告のみ。auth key は serve コマンドに渡さない）。
    if getattr(settings, "tailscale_serve_enabled", False):
        port = getattr(settings, "http_port", 80)
        s_rc, _s_out, s_err = await ops.run(
            ["tailscale", "serve", "--bg", "--https=443", f"http://localhost:{port}"]
        )
        if s_rc != 0:
            logger.warning("tailscale serve 失敗 (rc=%d): %s", s_rc, (s_err or "")[:200])

    return _ok()


async def tailscale_down(
    payload: dict,
    ops: SystemOps,
    settings: Any,
) -> dict:
    """Tailscale VPN を停止する。

    ペイロードフィールド: なし
    """
    rc, _stdout, stderr = await ops.run(["tailscale", "down"])
    if rc != 0:
        return _err(f"tailscale down 失敗 (rc={rc}): {stderr[:200]}")
    return _ok()


async def tailscale_status(
    payload: dict,
    ops: SystemOps,
    settings: Any,
) -> dict:
    """Tailscale の現在ステータスを取得する。

    tailscale status --json の出力をパースし、安全なサブセットのみ返す。
    認証キーを含む可能性のあるフィールドは除外する。

    ペイロードフィールド: なし

    レスポンス:
        status.backend_state (str): バックエンド状態。
        status.self (dict): 自ノード情報（ID・ホスト名・IP）。
        status.peers (list): ピア一覧（ID・ホスト名・IP）。
    """
    rc, stdout, stderr = await ops.run(["tailscale", "status", "--json"])
    if rc != 0:
        return _err(f"tailscale status 失敗 (rc={rc}): {stderr[:200]}")

    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return _err(f"tailscale status の JSON パース失敗: {exc}")

    # 認証キー等を含む可能性のある生データを直接返さず、
    # 安全なサブセットのみを返す
    def _safe_peer(p: dict) -> dict:
        """ピア情報から安全なフィールドのみ抽出する。"""
        return {
            "id": p.get("ID", ""),
            "hostname": p.get("HostName", ""),
            "dns_name": p.get("DNSName", ""),
            "ips": p.get("TailscaleIPs", []),
            "online": p.get("Online", False),
        }

    self_info: dict = raw.get("Self", {})
    safe_self = {
        "id": self_info.get("ID", ""),
        "hostname": self_info.get("HostName", ""),
        "dns_name": self_info.get("DNSName", ""),
        "ips": self_info.get("TailscaleIPs", []),
        "online": self_info.get("Online", False),
    }

    peers_raw: dict = raw.get("Peer", {}) or {}
    safe_peers = [_safe_peer(p) for p in peers_raw.values()]

    return _ok(
        status={
            "backend_state": raw.get("BackendState", ""),
            "self": safe_self,
            "peers": safe_peers,
        }
    )


async def get_dhcp_leases(
    payload: dict,
    ops: SystemOps,
    settings: Any,
) -> dict:
    """dnsmasq の DHCP リース一覧を取得する。

    ``/var/lib/misc/dnsmasq.leases`` を読み込んでパースする。
    フォーマット: ``<timestamp> <mac> <ip> <hostname> <client-id>``

    不正な行は無視する（parse-skip）。

    ペイロードフィールド: なし

    レスポンス:
        leases (list): {"mac": "...", "ip": "...", "hostname": "..."} のリスト。
    """
    try:
        content = ops.read_file(settings.dnsmasq_leases_path)
    except FileNotFoundError:
        # リースファイルが存在しない場合は空リストを返す
        return _ok(leases=[])
    except OSError as exc:
        return _err(f"リースファイル読み込みエラー: {exc}")

    leases = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4:
            logger.debug("リース行 %d をスキップ (フィールド不足): %r", line_no, line)
            continue

        _timestamp, mac_raw, ip_raw, hostname_raw = parts[0], parts[1], parts[2], parts[3]

        # MAC アドレスを正規化（不正な場合はスキップ）
        try:
            mac = normalize_mac(mac_raw)
        except ValueError:
            logger.debug("リース行 %d をスキップ (不正な MAC): %r", line_no, mac_raw)
            continue

        # IP アドレスを検証（不正な場合はスキップ）
        try:
            validate_ipv4(ip_raw)
        except ValueError:
            logger.debug("リース行 %d をスキップ (不正な IP): %r", line_no, ip_raw)
            continue

        # ホスト名は "*" の場合は空文字に置き換える
        # ホスト名は信頼できない LAN 端末が書くため境界で検証する。"*" や
        # RFC 1123 非準拠（制御文字・過長・区切り注入）の値は空文字へ落とし、
        # 下流（core プロビジョニング/GUI/ログ）へ汚染データを渡さない。
        hostname = hostname_raw if (hostname_raw != "*" and is_valid_hostname(hostname_raw)) else ""

        leases.append({"mac": mac, "ip": ip_raw, "hostname": hostname})

    return _ok(leases=leases)


async def get_nat_status(
    payload: dict,
    ops: SystemOps,
    settings: Any,
) -> dict:
    """現在の NAT ステータスを取得する。

    nftables テーブルを確認してマスカレードが有効かどうかを返す。

    ペイロードフィールド: なし

    レスポンス:
        enabled (bool): マスカレードが有効かどうか。
    """
    rc, stdout, _stderr = await ops.run(["nft", "list", "table", "ip", settings.nftables_table])

    if rc != 0:
        # テーブルが存在しない場合は NAT 無効とみなす
        return _ok(enabled=False)

    # マスカレードルールの存在を確認する
    enabled = "masquerade" in stdout.lower()
    return _ok(enabled=enabled)


# コマンド名 → ハンドラ関数のマッピング
_COMMAND_MAP = {
    "apply_dhcp": apply_dhcp,
    "apply_nat": apply_nat,
    "tailscale_up": tailscale_up,
    "tailscale_down": tailscale_down,
    "tailscale_status": tailscale_status,
    "get_dhcp_leases": get_dhcp_leases,
    "get_nat_status": get_nat_status,
}


async def dispatch(payload: dict, ops: SystemOps, settings: Any) -> dict:
    """コマンドを対応するハンドラにディスパッチする。

    Args:
        payload: リクエスト JSON ペイロード（"cmd" フィールドを含む）。
        ops: SystemOps 実装（本番時は RealSystemOps、テスト時はフェイク）。
        settings: Settings インスタンス。

    Returns:
        レスポンス dict。
    """
    cmd = payload.get("cmd", "")
    handler = _COMMAND_MAP.get(str(cmd))
    if handler is None:
        return _err("unknown command")

    try:
        return await handler(payload, ops, settings)
    except Exception:
        logger.exception("コマンド %r の処理中に予期しないエラーが発生しました", cmd)
        return _err("internal error")
