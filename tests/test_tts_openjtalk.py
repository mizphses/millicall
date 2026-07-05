import wave
from io import BytesIO

import pytest

from millicall.ai.tts.openjtalk import OpenJTalkTTS


def _wav_48k(ms: int) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x02\x00" * (48000 * ms // 1000))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_openjtalk_synthesize_with_injected_runner():
    captured = {}

    def fake_runner(text: str) -> bytes:
        captured["text"] = text
        return _wav_48k(100)

    tts = OpenJTalkTTS(dict_dir="/x", voice_path="/y.htsvoice", runner=fake_runner)
    pcm = await tts.synthesize("こんにちは")
    assert captured["text"] == "こんにちは"
    assert 1500 <= len(pcm) <= 1700  # 100ms @8k
