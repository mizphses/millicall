"""netd システム操作レイヤ。

副作用（サブプロセス実行・ファイル書き込み）をすべてこのモジュールの
インターフェイス経由に集約し、テスト時はフェイク実装に差し替えられるようにする。

**セキュリティ注意**:
- run() は常に argv リストを使い asyncio.create_subprocess_exec を呼ぶ。
  shell=True は絶対に使用しない。コマンドインジェクション根絶のため。
- write_file() はアトミック書き込み（temp ファイル → rename）を行い、
  部分書き込みによる設定ファイル破損を防ぐ。
"""

import asyncio
import contextlib
import logging
import os
import tempfile
from typing import Protocol, runtime_checkable

logger = logging.getLogger("millicall.netd.system")

# サブプロセスのデフォルトタイムアウト秒数
_DEFAULT_TIMEOUT: float = 30.0


@runtime_checkable
class SystemOps(Protocol):
    """システム操作の抽象インターフェイス。テスト時はフェイクに差し替える。"""

    async def run(
        self,
        argv: list[str],
        *,
        input_text: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> tuple[int, str, str]:
        """コマンドを実行して (returncode, stdout, stderr) を返す。

        Args:
            argv: コマンドと引数のリスト。シェル文字列は使用しない。
            input_text: 標準入力に渡す文字列。None なら stdin を閉じる。
            timeout: タイムアウト秒数。超過した場合は returncode=-1 を返す。

        Returns:
            (returncode, stdout, stderr) のタプル。
        """
        ...

    def write_file(self, path: str, content: str) -> None:
        """ファイルへアトミックに書き込む（temp + rename）。

        Args:
            path: 書き込み先のファイルパス。
            content: 書き込む文字列。

        Raises:
            OSError: ファイルシステム操作が失敗した場合。
        """
        ...

    def read_file(self, path: str) -> str:
        """ファイルを読み込んで文字列として返す。

        Args:
            path: 読み込むファイルパス。

        Returns:
            ファイルの内容文字列。

        Raises:
            FileNotFoundError: ファイルが存在しない場合。
            OSError: 読み込みが失敗した場合。
        """
        ...


class RealSystemOps:
    """本番用 SystemOps 実装。実際のサブプロセスとファイルシステムを操作する。"""

    async def run(
        self,
        argv: list[str],
        *,
        input_text: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> tuple[int, str, str]:
        """asyncio.create_subprocess_exec でコマンドを実行する。

        shell=True は使用しない。argv は必ずリスト形式で渡すこと。
        タイムアウト時はプロセスをkillして returncode=-1 を返す。

        Args:
            argv: コマンドと引数のリスト。
            input_text: 標準入力に渡す文字列。
            timeout: タイムアウト秒数（デフォルト30秒）。

        Returns:
            (returncode, stdout, stderr) のタプル。
        """
        if not argv:
            raise ValueError("argv は空にできません")

        stdin_data = input_text.encode() if input_text is not None else None
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_data),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("コマンドタイムアウト: %s", argv[0])
            try:
                proc.kill()
                await proc.communicate()
            except OSError:
                pass
            return (-1, "", "タイムアウト")

        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")
        rc = proc.returncode if proc.returncode is not None else -1

        if rc != 0:
            logger.warning(
                "コマンド失敗 rc=%d: %s (stderr=%r)",
                rc,
                argv[0],
                stderr[:200],
            )
        return (rc, stdout, stderr)

    def write_file(self, path: str, content: str) -> None:
        """ファイルへアトミックに書き込む（同ディレクトリの temp + rename）。

        同ディレクトリに一時ファイルを作成し、rename で置き換えることで
        部分書き込みを防ぐ。モードは 0644 固定。

        Args:
            path: 書き込み先のファイルパス。
            content: 書き込む文字列（UTF-8）。
        """
        dir_path = os.path.dirname(os.path.abspath(path))
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, prefix=".netd_tmp_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(tmp_path, 0o644)
            os.rename(tmp_path, path)
            logger.debug("ファイル書き込み完了: %s", path)
        except Exception:
            # 失敗時は一時ファイルを削除する
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    def read_file(self, path: str) -> str:
        """ファイルを読み込んで文字列として返す。

        Args:
            path: 読み込むファイルパス。

        Returns:
            ファイルの内容文字列。
        """
        with open(path, encoding="utf-8") as f:
            return f.read()
