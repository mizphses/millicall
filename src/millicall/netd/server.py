"""netd UNIX ソケットサーバ。

改行区切り JSON プロトコル (NDJSON) over UNIX SOCK_STREAM を提供する。
1 接続 = 1 リクエスト行 + 1 レスポンス行。

**セキュリティ注意**:
- リクエスト行の最大長を制限してメモリ枯渇を防ぐ。
- ソケットのパーミッションを 0o660 に設定し、適切なグループのみアクセス可能にする。
- クライアントへのエラー応答にはスタックトレース・秘密情報を含めない。
- 実際のエラーはサーバ側ログにのみ記録する。
"""

import asyncio
import json
import logging
import os
from typing import Any

from millicall.netd.commands import dispatch
from millicall.netd.system import RealSystemOps, SystemOps

logger = logging.getLogger("millicall.netd.server")

# リクエスト行の最大バイト数（メモリ枯渇防止）
_MAX_LINE_BYTES = 65536  # 64 KiB


async def _handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    ops: SystemOps,
    settings: Any,
) -> None:
    """1 接続の処理ハンドラ。

    1 行読み込み → JSON パース → コマンドディスパッチ → JSON 応答書き込み。
    例外はすべてキャッチして安全なエラー応答を返す。

    Args:
        reader: ストリームリーダ。
        writer: ストリームライタ。
        ops: SystemOps 実装。
        settings: Settings インスタンス。
    """
    peer = writer.get_extra_info("peername", "(unknown)")
    try:
        # 最大行長を制限して読み込む
        try:
            line = await asyncio.wait_for(
                reader.readline(),
                timeout=30.0,
            )
        except TimeoutError:
            logger.warning("接続タイムアウト (peer=%s)", peer)
            _write_error(writer, "timeout")
            return

        if not line:
            # 接続が即座に閉じられた場合
            return

        if len(line) > _MAX_LINE_BYTES:
            logger.warning("リクエスト行が最大長を超えました (peer=%s, len=%d)", peer, len(line))
            _write_error(writer, "request too large")
            return

        # JSON パース
        try:
            payload = json.loads(line.decode(errors="replace"))
        except json.JSONDecodeError as exc:
            logger.debug("JSON パース失敗 (peer=%s): %s", peer, exc)
            _write_error(writer, "invalid JSON")
            return

        if not isinstance(payload, dict):
            _write_error(writer, "request must be a JSON object")
            return

        # コマンドディスパッチ（例外はすべて dispatch 内でキャッチ済み）
        response = await dispatch(payload, ops, settings)

        # レスポンスを JSON 行として書き込む
        resp_line = json.dumps(response, ensure_ascii=False) + "\n"
        writer.write(resp_line.encode())
        await writer.drain()

    except Exception:
        # 予期しない例外 — スタックトレースをログに記録し、安全なエラーをクライアントへ返す
        logger.exception("接続処理中に予期しないエラーが発生しました (peer=%s)", peer)
        try:
            _write_error(writer, "internal error")
            await writer.drain()
        except Exception:
            pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def _write_error(writer: asyncio.StreamWriter, message: str) -> None:
    """エラー応答を書き込む（drain は呼び出し元が行う）。

    Args:
        writer: ストリームライタ。
        message: エラーメッセージ文字列（スタックトレース・秘密情報を含めないこと）。
    """
    resp = json.dumps({"ok": False, "error": message}, ensure_ascii=False) + "\n"
    writer.write(resp.encode())


async def serve(settings: Any, ops: SystemOps | None = None) -> None:
    """netd UNIX ソケットサーバを起動して接続を受け付ける。

    バインド前に古いソケットファイルが存在すれば削除する。
    バインド後にソケットのパーミッションを 0o660 に設定する。

    Args:
        settings: Settings インスタンス（netd_socket_path を使用）。
        ops: SystemOps 実装。None の場合は RealSystemOps を使用する。
    """
    if ops is None:
        ops = RealSystemOps()

    socket_path = settings.netd_socket_path

    # 古いソケットファイルが残っていれば削除する
    try:
        os.unlink(socket_path)
        logger.debug("古いソケットファイルを削除しました: %s", socket_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("古いソケットファイルの削除に失敗しました: %s (%s)", socket_path, exc)

    # ソケットのディレクトリが存在しない場合は作成する
    socket_dir = os.path.dirname(socket_path)
    if socket_dir:
        os.makedirs(socket_dir, exist_ok=True)

    server = await asyncio.start_unix_server(
        lambda r, w: _handle_connection(r, w, ops, settings),
        path=socket_path,
    )

    # ソケットのパーミッションを制限する（660: 所有者+グループのみ読み書き可）
    try:
        os.chmod(socket_path, 0o660)
    except OSError as exc:
        logger.warning("ソケットのパーミッション設定に失敗しました: %s", exc)

    logger.info("netd サーバ起動: %s", socket_path)

    async with server:
        await server.serve_forever()
