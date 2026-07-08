import hashlib
import hmac
import json
import wave
from io import BytesIO

import httpx
import pytest

from millicall.ai.tts.coefont import CoefontTTS

_ACCESS_KEY = "test-access-key"
_ACCESS_SECRET = "test-access-secret"
_COEFONT_ID = "12345678-1234-1234-1234-123456789abc"
_SIGNED_URL = "https://signed.example.com/audio.wav"


def _wav_44k(ms: int) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x01\x00" * (44100 * ms // 1000))
    return buf.getvalue()


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url == "https://api.coefont.cloud/v2/text2speech":
        # 認証ヘッダー3点。署名は受信ボディのバイト列から再計算して一致を確認する。
        assert request.headers["authorization"] == _ACCESS_KEY
        date = request.headers["x-coefont-date"]
        assert date.isdigit()
        expected = hmac.new(
            _ACCESS_SECRET.encode(),
            date.encode() + request.content,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["x-coefont-content"] == expected
        assert request.headers["content-type"] == "application/json"
        body = json.loads(request.content)
        assert body["coefont"] == _COEFONT_ID
        assert body["text"] == "テスト"
        assert body["format"] == "wav"
        assert body["speed"] == 1.2
        assert body["pitch"] == 100
        return httpx.Response(302, headers={"location": _SIGNED_URL})
    if str(request.url) == _SIGNED_URL:
        return httpx.Response(200, content=_wav_44k(100), headers={"content-type": "audio/wav"})
    return httpx.Response(404)


@pytest.mark.asyncio
async def test_coefont_signs_request_and_returns_8k_pcm():
    tts = CoefontTTS(
        access_key=_ACCESS_KEY,
        access_secret=_ACCESS_SECRET,
        coefont=_COEFONT_ID,
        speed=1.2,
        pitch=100,
        transport=httpx.MockTransport(_handler),
    )
    pcm = await tts.synthesize("テスト")
    # 100ms @8k mono 16bit ≈ 1600 bytes
    assert 1500 <= len(pcm) <= 1700


@pytest.mark.asyncio
async def test_coefont_raises_on_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid signature"})

    tts = CoefontTTS(
        access_key=_ACCESS_KEY,
        access_secret="wrong",
        coefont=_COEFONT_ID,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await tts.synthesize("テスト")
