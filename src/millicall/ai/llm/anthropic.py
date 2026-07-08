import json
from collections.abc import AsyncIterator

import httpx

from millicall.ai.llm.base import ChatMessage

_API_URL = "https://api.anthropic.com/v1/messages"


class AnthropicLLM:
    """Anthropic Messages API のストリーミングクライアント。

    system ロールは Messages API の ``system`` 引数へ振り分ける。api_key は
    x-api-key ヘッダで送るため URL/例外メッセージには漏れない。
    """

    def __init__(
        self,
        api_key: str | None,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 500,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._transport = transport

    def __repr__(self) -> str:
        # api_key を平文で漏らさない
        return f"AnthropicLLM(model={self._model!r}, api_key={'***' if self._api_key else None})"

    async def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        system = "\n".join(m.content for m in messages if m.role == "system")
        turns = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": turns,
            "stream": True,
        }
        headers = {
            "x-api-key": self._api_key or "",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        async with (
            httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client,
            client.stream("POST", _API_URL, json=payload, headers=headers) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "content_block_delta":
                    text = obj.get("delta", {}).get("text")
                    if text:
                        yield text
