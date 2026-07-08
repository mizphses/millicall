import httpx
import pytest

from millicall.ai.llm.base import ChatMessage
from millicall.ai.llm.gemini import GeminiLLM

_SSE = (
    'data: {"candidates":[{"content":{"parts":[{"text":"こん"}]}}]}\n\n'
    'data: {"candidates":[{"content":{"parts":[{"text":"にちは"}]}}]}\n\n'
)


def _handler(request: httpx.Request) -> httpx.Response:
    assert "streamGenerateContent" in str(request.url)
    assert request.url.params.get("alt") == "sse"
    # api_key はヘッダで渡す（URL クエリに載せると例外の str に漏れるため）
    assert request.headers["x-goog-api-key"] == "gk"
    assert "gk" not in str(request.url)
    body = request.read().decode()
    assert '"systemInstruction"' in body
    return httpx.Response(200, text=_SSE, headers={"content-type": "text/event-stream"})


@pytest.mark.asyncio
async def test_gemini_stream():
    llm = GeminiLLM(api_key="gk", transport=httpx.MockTransport(_handler))
    tokens = [
        t async for t in llm.stream_chat([ChatMessage("system", "sys"), ChatMessage("user", "hi")])
    ]
    assert tokens == ["こん", "にちは"]


def test_gemini_repr_masks_api_key():
    llm = GeminiLLM(api_key="super-secret")
    assert "super-secret" not in repr(llm)
    assert "***" in repr(llm)


@pytest.mark.asyncio
async def test_gemini_error_does_not_leak_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text='{"error":"forbidden"}')

    llm = GeminiLLM(api_key="super-secret", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        async for _ in llm.stream_chat([ChatMessage("user", "hi")]):
            pass
    assert "super-secret" not in str(exc_info.value)
