import json
from collections.abc import AsyncIterator

import httpx

from millicall.ai.llm.base import ChatMessage


class OpenAICompatibleLLM:
    """OpenAI Chat Completions 互換 API のストリーミングクライアント。

    base_url にはベンダーのエンドポイント（.../v1）を渡す。DeepSeek/Qwen/GLM 等
    互換エンドポイントを config で切り替え可能。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 500,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._transport = transport

    def __repr__(self) -> str:
        # api_key を平文で漏らさない
        return (
            f"OpenAICompatibleLLM(base_url={self._base_url!r}, model={self._model!r}, "
            f"api_key={'***' if self._api_key else None})"
        )

    async def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        async with (
            httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client,
            client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                if delta:
                    yield delta
