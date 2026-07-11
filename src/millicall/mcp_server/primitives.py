"""手動音声プリミティブ（MCP say/listen/say_and_listen の下回りサービス）。

parked チャネル上で:
    - `say(text)`: 既定 TTS で合成 → tts_dir に WAV → CallControl.play_file（uuid_broadcast）。
      再生秒数（PCM 長 / (8000*2)）を返す。書き出した WAV は再生後に削除する。
    - `listen(max_seconds)`: `uuid_record <uuid> start <path> <limit>` で一定時間録音し、
      停止後に WAV を読んで STT で 1 発話をテキスト化する（旧 listen 相当）。
      audio_stream 経由の WS 受け（会話用・重い）は使わない。
    - `say_and_listen(text, max_seconds)`: say → listen の合成。1 ターン。

voice 引数はコントローラ裁定#2 により受理して無視（互換維持）— ツール層（Task 6）で
シグネチャに残す。ここでは既定プロバイダの声を使う。
"""

import asyncio
import re
import wave
from collections.abc import Awaitable, Callable
from io import BytesIO
from pathlib import Path
from typing import Protocol

from millicall.ai.audio import pcm8k_to_wav, wav_to_pcm8k
from millicall.media.service import locked_bgapi

# 録音パス: bgapi コマンドに補間されるため厳格 allowlist で検証し、空白/改行/
# ESL 区切り（&;|`）を排してコマンドインジェクションを塞ぐ。英数・ドット・
# アンダースコア・ハイフン・スラッシュのみ許可（絶対パス/ディレクトリ可）。
_VALID_RECORD_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,512}$")

_SAMPLE_RATE = 8000
_BYTES_PER_SAMPLE = 2


class _EslLike(Protocol):
    async def bgapi(self, command: str) -> str: ...


class _CallControlLike(Protocol):
    async def play_file(self, path: str) -> None: ...


async def _default_read_recording(path: str) -> bytes:
    """録音 WAV をディスクから読む（FreeSWITCH が書いたファイル）。"""
    return Path(path).read_bytes()


class CallPrimitives:
    def __init__(
        self,
        *,
        esl: _EslLike,
        call_uuid: str,
        call_control: _CallControlLike,
        tts,
        stt,
        tts_dir: Path,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        read_recording: Callable[[str], Awaitable[bytes]] | None = None,
        lock: asyncio.Lock | None = None,
        reconnect: Callable[[], Awaitable[_EslLike]] | None = None,
    ) -> None:
        self._esl = esl
        self._call_uuid = call_uuid
        self._call_control = call_control
        self._tts = tts
        self._stt = stt
        self._tts_dir = Path(tts_dir)
        self._tts_dir.mkdir(parents=True, exist_ok=True)
        if sleep is None:
            sleep = asyncio.sleep
        self._sleep = sleep
        self._read_recording = read_recording or _default_read_recording
        self._seq = 0
        # 共有 ESL 接続の直列化（I6）: 未注入時は per-instance lock・再接続なしに
        # フォールバックする（後方互換。接続断は呼び出し元へ伝播）。
        self._lock = lock if lock is not None else asyncio.Lock()
        self._reconnect = reconnect

    async def _bgapi(self, command: str) -> None:
        """共有 ESL 接続を lock で直列化し、接続断時は reconnect で張り直して再送する。"""
        self._esl = await locked_bgapi(
            self._esl, command, lock=self._lock, reconnect=self._reconnect
        )

    async def say(self, text: str, *, tts=None) -> float:
        """text を合成して再生し、再生秒数を返す。

        tts が指定された場合は既定プロバイダの代わりにそのプロバイダで合成する
        （ワークフローノードの tts_provider_id オーバーライド用）。
        """
        pcm = await (tts if tts is not None else self._tts).synthesize(text)
        self._seq += 1
        path = self._tts_dir / f"mcp_say_{self._call_uuid}_{self._seq}.wav"
        path.write_bytes(pcm8k_to_wav(pcm))
        try:
            await self._call_control.play_file(str(path))
        finally:
            path.unlink(missing_ok=True)
        return len(pcm) / (_SAMPLE_RATE * _BYTES_PER_SAMPLE)

    async def listen(self, max_seconds: int = 15) -> str:
        """一定時間録音し、STT で 1 発話をテキスト化して返す（無発話は空文字）。"""
        self._seq += 1
        path = self._tts_dir / f"mcp_listen_{self._call_uuid}_{self._seq}.wav"
        path_str = str(path)
        await self._bgapi(f"uuid_record {self._call_uuid} start {path_str} {max_seconds}")
        try:
            # max_seconds を尊重して録音完了を待つ（テストは injectable sleep で即時）。
            await self._sleep(max_seconds)
        finally:
            # 録音を必ず停止し、mod ストリーム/ファイルハンドルをリークさせない。
            await self._bgapi(f"uuid_record {self._call_uuid} stop {path_str}")

        try:
            wav = await self._read_recording(path_str)
        except OSError:
            return ""
        finally:
            path.unlink(missing_ok=True)

        if not wav or not _wav_has_frames(wav):
            return ""
        pcm = wav_to_pcm8k(wav)
        return await self._transcribe(pcm)

    async def record(self, path: str, max_seconds: int) -> str:
        """指定パスに録音し、ファイルを保存する（STT なし、ファイル削除なし）。

        voicemail 等の録音保存用。:meth:`listen` と異なり、STT 変換・ファイル削除を行わない。
        ファイルの作成は FreeSWITCH に委ねる（呼び出し元がディレクトリを事前に作成すること）。

        bgapi コマンド:
            ``uuid_record <uuid> start <path> <max_seconds>``
            ``uuid_record <uuid> stop <path>``   (finally で確実に停止)

        Returns:
            path: 録音ファイルのパス（格納先の確認用）。

        Raises:
            ValueError: path が allowlist を満たさない、または max_seconds が非正の整数。
        """
        if not path or not _VALID_RECORD_PATH_RE.match(path):
            raise ValueError(f"invalid record path: {path!r}")
        if not isinstance(max_seconds, int) or isinstance(max_seconds, bool) or max_seconds <= 0:
            raise ValueError(f"invalid record duration: {max_seconds!r}")
        await self._bgapi(f"uuid_record {self._call_uuid} start {path} {max_seconds}")
        try:
            await self._sleep(max_seconds)
        finally:
            await self._bgapi(f"uuid_record {self._call_uuid} stop {path}")
        return path

    async def say_and_listen(
        self, text: str, max_seconds: int = 15, *, tts=None
    ) -> tuple[str, str]:
        """say → listen を 1 ターンで行い、(話した内容, 聞き取り) を返す。"""
        await self.say(text, tts=tts)
        heard = await self.listen(max_seconds)
        return text, heard

    async def _transcribe(self, pcm: bytes) -> str:
        sess = self._stt.open_session()
        finished = False
        try:
            await sess.feed(pcm)
            text = await sess.finish()
            finished = True
            return (text or "").strip()
        finally:
            if not finished:
                import contextlib

                with contextlib.suppress(Exception):
                    await sess.finish()


def _wav_has_frames(wav: bytes) -> bool:
    try:
        with wave.open(BytesIO(wav), "rb") as w:
            return w.getnframes() > 0
    except (wave.Error, EOFError):
        return False
