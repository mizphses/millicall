import wave
from io import BytesIO

from millicall.ai.audio import pcm8k_to_wav, resample_pcm, wav_to_pcm8k


def _make_wav(pcm: bytes, rate: int, channels: int) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def test_resample_halves_length_when_rate_halved():
    pcm = b"\x01\x00" * 1600  # 1600 samples @16k = 100ms
    out = resample_pcm(pcm, 16000, 8000, 1)
    assert 780 * 2 <= len(out) <= 820 * 2  # ~800 samples


def test_wav_to_pcm8k_from_24k_stereo():
    src = b"\x02\x00\x03\x00" * 2400  # stereo 24k, 100ms
    wav = _make_wav(src, 24000, 2)
    pcm = wav_to_pcm8k(wav)
    # 100ms @8k mono 16bit ≈ 800 samples = 1600 bytes (±許容)
    assert 1500 <= len(pcm) <= 1700


def test_pcm8k_to_wav_roundtrip_header():
    pcm = b"\x04\x00" * 800
    wav = pcm8k_to_wav(pcm)
    with wave.open(BytesIO(wav), "rb") as w:
        assert w.getframerate() == 8000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.readframes(w.getnframes()) == pcm
