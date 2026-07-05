import httpx
import pytest

from millicall.ai.llm.anthropic import AnthropicLLM
from millicall.ai.llm.base import ChatMessage

_SSE = (
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"こん"}}\n\n'
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"にちは"}}\n\n'
    "event: message_stop\n"
    'data: {"type":"message_stop"}\n\n'
)


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["x-api-key"] == "ak"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = request.read().decode()
    assert '"system": "sys"' in body or '"system":"sys"' in body
    return httpx.Response(200, text=_SSE, headers={"content-type": "text/event-stream"})


@pytest.mark.asyncio
async def test_anthropic_stream():
    llm = AnthropicLLM(api_key="ak", transport=httpx.MockTransport(_handler))
    tokens = [
        t
        async for t in llm.stream_chat(
            [ChatMessage("system", "sys"), ChatMessage("user", "hi")]
        )
    ]
    assert tokens == ["こん", "にちは"]


def test_anthropic_repr_masks_api_key():
    llm = AnthropicLLM(api_key="super-secret")
    assert "super-secret" not in repr(llm)
    assert "***" in repr(llm)


@pytest.mark.asyncio
async def test_anthropic_error_does_not_leak_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"error":"unauthorized"}')

    llm = AnthropicLLM(api_key="super-secret", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        async for _ in llm.stream_chat([ChatMessage("user", "hi")]):
            pass
    assert "super-secret" not in str(exc_info.value)
