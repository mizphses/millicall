import httpx
import pytest

from millicall.ai.stt.whisper import WhisperSTT, is_hallucination


def test_hallucination_filter():
    assert is_hallucination("ご視聴ありがとうございました")
    assert is_hallucination("ご視聴ありがとうございました。")  # 句点付きも正規化
    assert is_hallucination("Thanks for watching")
    assert not is_hallucination("味噌ラーメンを一つください")


@pytest.mark.asyncio
async def test_whisper_transcribes_and_filters_hallucination():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/audio/transcriptions")
        assert request.headers["authorization"] == "Bearer sk-w"
        body = request.read()
        assert b"audio.wav" in body  # multipart で WAV をアップロードしている
        assert b"whisper-1" in body
        return httpx.Response(200, text="ご視聴ありがとうございました")

    stt = WhisperSTT(api_key="sk-w", transport=httpx.MockTransport(handler))
    sess = stt.open_session()
    await sess.feed(b"\x01\x00" * 800)
    assert await sess.finish() == ""  # 幻聴は空文字に


@pytest.mark.asyncio
async def test_whisper_returns_real_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="こんばんは")

    stt = WhisperSTT(api_key="sk-w", transport=httpx.MockTransport(handler))
    sess = stt.open_session()
    await sess.feed(b"\x01\x00" * 800)
    assert await sess.finish() == "こんばんは"


@pytest.mark.asyncio
async def test_whisper_empty_buffer_returns_empty_without_request():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("空バッファでは transcribe を呼ばない")

    stt = WhisperSTT(api_key="sk-w", transport=httpx.MockTransport(handler))
    sess = stt.open_session()
    assert await sess.finish() == ""


def test_whisper_repr_masks_api_key():
    stt = WhisperSTT(api_key="super-secret")
    assert "super-secret" not in repr(stt)
    assert "***" in repr(stt)


@pytest.mark.asyncio
async def test_whisper_error_does_not_leak_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"error":"unauthorized"}')

    stt = WhisperSTT(api_key="super-secret", transport=httpx.MockTransport(handler))
    sess = stt.open_session()
    await sess.feed(b"\x01\x00" * 800)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await sess.finish()
    assert "super-secret" not in str(exc_info.value)
