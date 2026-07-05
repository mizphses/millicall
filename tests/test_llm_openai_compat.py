import httpx
import pytest

from millicall.ai.llm.base import ChatMessage
from millicall.ai.llm.openai_compat import OpenAICompatibleLLM

_SSE = (
    'data: {"choices":[{"delta":{"content":"こん"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"にちは"}}]}\n\n'
    'data: {"choices":[{"delta":{}}]}\n\n'
    "data: [DONE]\n\n"
)


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == "Bearer sk-test"
    body = request.read().decode()
    assert '"stream": true' in body or '"stream":true' in body
    return httpx.Response(200, text=_SSE, headers={"content-type": "text/event-stream"})


@pytest.mark.asyncio
async def test_stream_yields_tokens():
    llm = OpenAICompatibleLLM(
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        model="gpt-4o-mini",
        transport=httpx.MockTransport(_handler),
    )
    tokens = [t async for t in llm.stream_chat([ChatMessage("user", "hi")])]
    assert tokens == ["こん", "にちは"]


@pytest.mark.asyncio
async def test_base_url_trailing_slash_normalized():
    llm = OpenAICompatibleLLM(
        base_url="https://x/v1/",
        api_key="sk-test",
        model="m",
        transport=httpx.MockTransport(_handler),
    )
    tokens = [t async for t in llm.stream_chat([ChatMessage("user", "hi")])]
    assert tokens == ["こん", "にちは"]
