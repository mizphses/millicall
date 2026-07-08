"""open_jtalk バイナリを subprocess で叩くローカル最軽量 TTS。

ランタイム Python 依存ゼロ・決定論的。VOICEVOX 不在環境の保険として core
コンテナに常時同梱する。テキストは stdin 経由で渡すためシェル解釈されず、
辞書/音響モデルパスは config 由来のため、コマンドインジェクション経路は無い。
"""

import asyncio
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from millicall.ai.audio import wav_to_pcm8k

Runner = Callable[[str], bytes]

_DEFAULT_DICT_DIR = "/var/lib/mecab/dic/open-jtalk/naist-jdic"
_DEFAULT_VOICE_PATH = "/usr/share/hts-voice/nitech-jp-atr503-m001/nitech_jp_atr503_m001.htsvoice"


class OpenJTalkTTS:
    """open_jtalk バイナリ + HTS 音声で WAV を生成し 8k PCM に変換して返す。"""

    def __init__(
        self,
        dict_dir: str = _DEFAULT_DICT_DIR,
        voice_path: str = _DEFAULT_VOICE_PATH,
        runner: Runner | None = None,
    ) -> None:
        self._dict_dir = dict_dir
        self._voice_path = voice_path
        self._runner = runner or self._default_runner

    def _default_runner(self, text: str) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.wav"
            proc = subprocess.run(
                [
                    "open_jtalk",
                    "-x",
                    self._dict_dir,
                    "-m",
                    self._voice_path,
                    "-ow",
                    str(out),
                ],
                input=text.encode("utf-8"),
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"open_jtalk failed: {proc.stderr.decode(errors='replace')}")
            return out.read_bytes()

    async def synthesize(self, text: str) -> bytes:
        wav = await asyncio.to_thread(self._runner, text)
        return wav_to_pcm8k(wav)
