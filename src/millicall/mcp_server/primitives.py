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

import wave
from collections.abc import Awaitable, Callable
from io import BytesIO
from pathlib import Path
from typing import Protocol

from millicall.ai.audio import pcm8k_to_wav, wav_to_pcm8k

_SAMPLE_RATE = 8000
_BYTES_PER_SAMPLE = 2


class _EslLike(Protocol):
    async def bgapi(self, command: str) -> str:
        ...


class _CallControlLike(Protocol):
    async def play_file(self, path: str) -> None:
        ...


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
    ) -> None:
        self._esl = esl
        self._call_uuid = call_uuid
        self._call_control = call_control
        self._tts = tts
        self._stt = stt
        self._tts_dir = Path(tts_dir)
        self._tts_dir.mkdir(parents=True, exist_ok=True)
        if sleep is None:
            import asyncio

            sleep = asyncio.sleep
        self._sleep = sleep
        self._read_recording = read_recording or _default_read_recording
        self._seq = 0

    async def say(self, text: str) -> float:
        """text を合成して再生し、再生秒数を返す。"""
        pcm = await self._tts.synthesize(text)
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
        await self._esl.bgapi(
            f"uuid_record {self._call_uuid} start {path_str} {max_seconds}"
        )
        try:
            # max_seconds を尊重して録音完了を待つ（テストは injectable sleep で即時）。
            await self._sleep(max_seconds)
        finally:
            # 録音を必ず停止し、mod ストリーム/ファイルハンドルをリークさせない。
            await self._esl.bgapi(f"uuid_record {self._call_uuid} stop {path_str}")

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

    async def say_and_listen(self, text: str, max_seconds: int = 15) -> tuple[str, str]:
        """say → listen を 1 ターンで行い、(話した内容, 聞き取り) を返す。"""
        await self.say(text)
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
