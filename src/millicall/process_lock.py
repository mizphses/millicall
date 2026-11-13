"""core の単一プロセス起動ガード（issue #86）。

認証状態（MCP OAuth / SAML リプレイ）は DB に保存されプロセス間で共有できるが、
core 全体には本質的にプロセスローカルな状態が残る:

  - ESL イベント購読: 各プロセスが独立に購読するため、CDR 記録・着信 AI 応対
    （uuid_audio_stream 発行）・ワークフロー実行が通話イベントごとにプロセス数だけ重複する。
  - メディア WS: 着信 AI のワンタイムチケット（MediaStreamTickets）・SessionRegistry・
    MCP converse の一時エージェント（EphemeralAgentStore）・DTMF 収集・応答/終話待ちは
    メモリ上にあり、FreeSWITCH の WS 接続が別プロセスに届くと照合できない。
  - MCP Streamable HTTP のセッション（mcp-session-id）はプロセス内に保持される。
  - 管理画面の設定キャッシュ（SettingsService）は書き込んだプロセスでしか無効化されない。

このため対応構成は「1 つの data_dir につき core 1 プロセス」。data_dir 上のロック
ファイルに排他 flock を取り、同じ data_dir で 2 つ目の core（uvicorn --workers N や
同じボリュームを共有する 2 つ目のコンテナ）が起動したら起動を拒否する。flock はプロセス
終了時にカーネルが解放するため、異常終了後の再起動で誤検出しない。
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger("millicall")

LOCK_FILENAME = "core.lock"


class SingleProcessLock:
    """data_dir 単位の排他ロック。release() まで保持する。"""

    def __init__(self, fd: int | None) -> None:
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            import fcntl

            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


def acquire_single_process_lock(data_dir: Path) -> SingleProcessLock:
    """data_dir のロックを取得する。他プロセスが保持中なら RuntimeError。

    flock 非対応環境（fcntl の無い OS、ロック非対応のネットワーク FS 等）では
    検出できないため警告のみで続行する（誤検出で起動不能にしない）。
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover — 非 POSIX
        logger.warning("単一プロセスガードを利用できません（fcntl なし）")
        return SingleProcessLock(None)

    path = data_dir / LOCK_FILENAME
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RuntimeError(
            f"別の millicall core プロセスが同じ data_dir（{data_dir}）で稼働中です。"
            "core は 1 プロセスのみ対応です（uvicorn --workers は 1、"
            "同じデータボリュームを複数コンテナで共有しない）。"
        ) from None
    except OSError:
        os.close(fd)
        logger.warning(
            "data_dir のロックを取得できません（flock 非対応の FS?）。"
            "単一プロセスガードを無効にして続行します",
            exc_info=True,
        )
        return SingleProcessLock(None)
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
    except OSError:
        pass  # PID 記録は参考情報のみ
    return SingleProcessLock(fd)
