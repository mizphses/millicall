from collections.abc import AsyncIterator

import httpx

from millicall.ai.llm._google_genai import (
    build_generate_content_payload,
    parse_sse_texts,
)
from millicall.ai.llm.base import ChatMessage

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiLLM:
    """Gemini streamGenerateContent (SSE) のストリーミングクライアント。

    system ロールは ``systemInstruction`` へ振り分ける。api_key は URL クエリ
    (?key=) ではなく x-goog-api-key ヘッダで送る。URL クエリに載せると
    ``HTTPStatusError`` の str に api_key が含まれて漏洩するため。
    """

    def __init__(
        self,
        api_key: str | None,
        model: str = "gemini-2.5-flash",
        temperature: float = 0.7,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._timeout = timeout
        self._transport = transport

    def __repr__(self) -> str:
        # api_key を平文で漏らさない
        return f"GeminiLLM(model={self._model!r}, api_key={'***' if self._api_key else None})"

    async def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        payload = build_generate_content_payload(messages, self._temperature)
        url = f"{_BASE_URL}/{self._model}:streamGenerateContent"
        params = {"alt": "sse"}
        headers = {
            "x-goog-api-key": self._api_key or "",
            "Content-Type": "application/json",
        }
        async with (
            httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client,
            client.stream("POST", url, params=params, json=payload, headers=headers) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                for text in parse_sse_texts(line):
                    yield text
