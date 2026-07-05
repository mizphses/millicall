import wave
from io import BytesIO

import httpx
import pytest

from millicall.ai.tts.voicevox import VoicevoxTTS


def _wav_24k(ms: int) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x01\x00" * (24000 * ms // 1000))
    return buf.getvalue()


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/audio_query":
        assert request.url.params.get("speaker") == "3"
        assert request.url.params.get("text") == "テスト"
        return httpx.Response(200, json={"accent_phrases": [], "speedScale": 1.0})
    if request.url.path == "/synthesis":
        assert request.url.params.get("speaker") == "3"
        return httpx.Response(200, content=_wav_24k(100), headers={"content-type": "audio/wav"})
    return httpx.Response(404)


@pytest.mark.asyncio
async def test_voicevox_returns_8k_pcm():
    tts = VoicevoxTTS(
        engine_url="http://voicevox:50021",
        speaker=3,
        transport=httpx.MockTransport(_handler),
    )
    pcm = await tts.synthesize("テスト")
    # 100ms @8k mono 16bit ≈ 1600 bytes
    assert 1500 <= len(pcm) <= 1700
