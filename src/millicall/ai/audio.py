"""PCM リサンプリング / WAV 変換の共通ユーティリティ。

音声の正準形は L16 モノラル 8000Hz ヘッダレス PCM (bytes)。
プロバイダ間の受け渡しは全てこの形式で行い、WAV ヘッダはファイル再生時のみ付与する。
"""

import audioop  # 3.12 stdlib; 3.13+ は audioop-lts パッケージが同名モジュールを提供
import wave
from io import BytesIO

_TARGET_RATE = 8000


def resample_pcm(pcm: bytes, src_rate: int, dst_rate: int, src_channels: int = 1) -> bytes:
    """16bit PCM をリサンプルする。多チャネルはモノへダウンミックスしてから変換。"""
    if src_channels > 1:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    if src_rate == dst_rate:
        return pcm
    converted, _ = audioop.ratecv(pcm, 2, 1, src_rate, dst_rate, None)
    return converted


def wav_to_pcm8k(wav: bytes) -> bytes:
    """任意サンプルレート/チャネル/ビット幅の WAV を L16 モノ 8k PCM に正規化する。"""
    with wave.open(BytesIO(wav), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        frames = audioop.lin2lin(frames, width, 2)
    return resample_pcm(frames, rate, _TARGET_RATE, channels)


def pcm8k_to_wav(pcm: bytes) -> bytes:
    """L16 モノ 8k PCM を WAV バイト列に包む（FreeSWITCH 再生用ファイルの中身）。"""
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_TARGET_RATE)
        w.writeframes(pcm)
    return buf.getvalue()
