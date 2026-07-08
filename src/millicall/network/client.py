"""netd UNIX ドメインソケットクライアント。

netd サーバー（src/millicall/netd/server.py）が提供する改行区切り JSON プロトコルへ
の非同期クライアント。1リクエスト1接続モデルに合わせて呼び出しごとに新規接続を張り、
応答受信後にソケットを閉じる。

プロトコル概要:
    接続 → JSON リクエスト行（末尾 \\n）送信 → JSON レスポンス行受信 → 接続終了

主なエラーは NetdError として上位へ伝播し、秘密情報（auth_key 等）を含まない
メッセージを保証する。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

logger = logging.getLogger("millicall.network.client")


class NetdError(Exception):
    """netd クライアント操作の失敗を表す例外。

    接続失敗・タイムアウト・プロトコルエラー・サーバー側エラー応答のいずれも
    この型で送出される。メッセージに認証キー等の秘密情報は含めない。
    """


class NetdClient:
    """netd UNIX ドメインソケットへの非同期クライアント。

    1 呼び出しごとに新規ソケット接続を確立し（netd の 1接続1コマンドモデルに準拠）、
    レスポンス受信後に接続を閉じる。接続は遅延生成のため、インスタンス生成時点で
    netd が起動していなくてもよい（テスト・開発環境での起動順を問わない）。

    Args:
        socket_path: netd が listen する UNIX ドメインソケットのパス。
        timeout: 1 コマンドあたりの最大待ち時間（秒）。既定 10.0 秒。
    """

    def __init__(self, socket_path: str, *, timeout: float = 10.0) -> None:
        self._socket_path = socket_path
        self._timeout = timeout

    async def _call(self, request: dict) -> dict:
        """netd へコマンドを送信し、レスポンス辞書を返す。

        接続失敗・タイムアウト・不正 JSON の場合は NetdError を送出する。
        秘密情報をエラーメッセージに含めないよう、リクエスト内容はログ・例外
        メッセージに露出させない。

        Args:
            request: 送信する JSON シリアライズ可能な辞書。``cmd`` キー必須。

        Returns:
            netd から受信した JSON 辞書。

        Raises:
            NetdError: 接続失敗・タイムアウト・プロトコルエラーのいずれか。
        """
        writer: asyncio.StreamWriter | None = None
        cmd = request.get("cmd", "<unknown>")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(path=self._socket_path),
                timeout=self._timeout,
            )
        except FileNotFoundError:
            raise NetdError(f"netd ソケットが見つかりません: {self._socket_path!r}") from None
        except ConnectionRefusedError:
            raise NetdError(
                f"netd ソケットへの接続が拒否されました: {self._socket_path!r}"
            ) from None
        except OSError as exc:
            raise NetdError(f"netd 接続エラー (cmd={cmd!r}): {exc}") from exc
        except TimeoutError:
            raise NetdError(
                f"netd 接続タイムアウト (cmd={cmd!r}, timeout={self._timeout}s)"
            ) from None

        try:
            line = json.dumps(request) + "\n"
            writer.write(line.encode())
            await asyncio.wait_for(writer.drain(), timeout=self._timeout)

            raw = await asyncio.wait_for(reader.readline(), timeout=self._timeout)
        except TimeoutError:
            raise NetdError(
                f"netd 応答タイムアウト (cmd={cmd!r}, timeout={self._timeout}s)"
            ) from None
        except OSError as exc:
            raise NetdError(f"netd 通信エラー (cmd={cmd!r}): {exc}") from exc
        finally:
            writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)

        if not raw:
            raise NetdError(f"netd から空の応答を受信しました (cmd={cmd!r})")

        try:
            resp: dict = json.loads(raw.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise NetdError(f"netd 応答の JSON パース失敗 (cmd={cmd!r}): {exc}") from exc

        return resp

    # ------------------------------------------------------------------
    # 便利メソッド（各コマンドのペイロードを組み立て、レスポンスを整形して返す）
    # ------------------------------------------------------------------

    async def apply_dhcp(
        self,
        *,
        lan_interface: str,
        lan_ip: str,
        lan_prefix: int,
        dhcp_range_start: str,
        dhcp_range_end: str,
        dhcp_lease_hours: int,
        provisioning_url: str,
    ) -> None:
        """DHCP サーバー設定を netd 経由で適用する。

        Args:
            lan_interface: LAN 側ネットワークインタフェース名（例: "eth0"）。
            lan_ip: LAN 側 IP アドレス（例: "192.168.100.1"）。
            lan_prefix: CIDR プレフィックス長（例: 24）。
            dhcp_range_start: DHCP 払い出し開始アドレス。
            dhcp_range_end: DHCP 払い出し終了アドレス。
            dhcp_lease_hours: リース時間（時間単位）。
            provisioning_url: プロビジョニング URL（DHCP option 43）。

        Raises:
            NetdError: 通信失敗またはサーバー側エラーの場合。
        """
        resp = await self._call(
            {
                "cmd": "apply_dhcp",
                "lan_interface": lan_interface,
                "lan_ip": lan_ip,
                "dhcp_range_start": dhcp_range_start,
                "dhcp_range_end": dhcp_range_end,
                "dhcp_lease_hours": dhcp_lease_hours,
                "provisioning_url": provisioning_url,
                "lan_prefix": lan_prefix,
            }
        )
        if not resp.get("ok"):
            raise NetdError(f"apply_dhcp 失敗: {resp.get('error', '不明なエラー')}")

    async def apply_nat(
        self,
        *,
        enabled: bool,
        lan_ip: str,
        lan_prefix: int,
        wan_interface: str,
        http_port: int = 80,
    ) -> None:
        """NAT（マスカレード）設定 + HTTP INPUT フィルタを netd 経由で適用する。

        Args:
            enabled: NAT を有効にするか否か。
            lan_ip: LAN 側 IP アドレス。
            lan_prefix: CIDR プレフィックス長。
            wan_interface: WAN 側ネットワークインタフェース名。
            http_port: core の HTTP ポート番号（デフォルト: 80）。
                      nftables INPUT フィルタで LAN CIDR からのみ許可し WAN から DROP する。
                      省略時は 80 にフォールバック（後方互換性）。

        Raises:
            NetdError: 通信失敗またはサーバー側エラーの場合。
        """
        resp = await self._call(
            {
                "cmd": "apply_nat",
                "enabled": enabled,
                "lan_ip": lan_ip,
                "lan_prefix": lan_prefix,
                "wan_interface": wan_interface,
                "http_port": http_port,
            }
        )
        if not resp.get("ok"):
            raise NetdError(f"apply_nat 失敗: {resp.get('error', '不明なエラー')}")

    async def tailscale_up(self, *, auth_key: str) -> None:
        """Tailscale VPN を有効化する。

        auth_key は認証情報のため、エラーメッセージには一切含めない。

        Args:
            auth_key: Tailscale 認証キー（tskey-... 形式）。

        Raises:
            NetdError: 通信失敗またはサーバー側エラーの場合（auth_key は含まない）。
        """
        resp = await self._call({"cmd": "tailscale_up", "auth_key": auth_key})
        if not resp.get("ok"):
            # auth_key をエラーメッセージに含めない
            raise NetdError(f"tailscale_up 失敗: {resp.get('error', '不明なエラー')}")

    async def tailscale_down(self) -> None:
        """Tailscale VPN を無効化する。

        Raises:
            NetdError: 通信失敗またはサーバー側エラーの場合。
        """
        resp = await self._call({"cmd": "tailscale_down"})
        if not resp.get("ok"):
            raise NetdError(f"tailscale_down 失敗: {resp.get('error', '不明なエラー')}")

    async def tailscale_status(self) -> dict:
        """Tailscale VPN の現在ステータスを取得する。

        Returns:
            netd から返された status 辞書。netd 側で安全なサブセットに
            整形済み（backend_state / self / peers、キーは snake_case）。

        Raises:
            NetdError: 通信失敗またはサーバー側エラーの場合。
        """
        resp = await self._call({"cmd": "tailscale_status"})
        if not resp.get("ok"):
            raise NetdError(f"tailscale_status 失敗: {resp.get('error', '不明なエラー')}")
        return resp.get("status", {})

    async def get_dhcp_leases(self) -> list[dict]:
        """現在の DHCP リース一覧を取得する。

        Returns:
            リース辞書のリスト。各辞書は ``mac``・``ip``・``hostname`` キーを持つ。

        Raises:
            NetdError: 通信失敗またはサーバー側エラーの場合。
        """
        resp = await self._call({"cmd": "get_dhcp_leases"})
        if not resp.get("ok"):
            raise NetdError(f"get_dhcp_leases 失敗: {resp.get('error', '不明なエラー')}")
        return resp.get("leases", [])

    async def get_nat_status(self) -> bool:
        """NAT の有効/無効状態を取得する。

        Returns:
            NAT が有効なら True、無効なら False。

        Raises:
            NetdError: 通信失敗またはサーバー側エラーの場合。
        """
        resp = await self._call({"cmd": "get_nat_status"})
        if not resp.get("ok"):
            raise NetdError(f"get_nat_status 失敗: {resp.get('error', '不明なエラー')}")
        return bool(resp.get("enabled", False))
