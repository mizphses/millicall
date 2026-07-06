"""Vertex AI 経由の Gemini streamGenerateContent (SSE) ストリーミングクライアント。

Gemini (generativelanguage, API キー) と異なり Vertex AI は GCP プロジェクト配下の
エンドポイントを OAuth Bearer トークンで叩く。トークンはサービスアカウント JSON から
``google.oauth2.service_account.Credentials`` を作り refresh して得る（同期処理なので
``asyncio.to_thread`` で実行）。テストでは ``token_provider`` を注入して実 GCP を回避する。

SA JSON・トークンは repr / 例外 / URL / ヘッダ露出（Bearer は body/URL に載せない）へ
漏らさない。ワイヤ形式（payload / SSE）は Gemini と共通で ``_google_genai`` を用いる。
"""

import asyncio
import inspect
from collections.abc import AsyncIterator

import httpx

from millicall.ai.llm._google_genai import (
    build_generate_content_payload,
    parse_sse_texts,
)
from millicall.ai.llm.base import ChatMessage

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class VertexAILLM:
    """Vertex AI publishers/google/models/*:streamGenerateContent クライアント。

    ``token_provider`` を渡さない場合は SA JSON から OAuth トークンを取得する
    （google-auth 必須）。テストでは ``token_provider`` を差し替えて実 GCP を回避する。
    """

    def __init__(
        self,
        sa_json: str | None,
        project: str,
        location: str = "us-central1",
        model: str = "gemini-2.0-flash",
        temperature: float = 0.7,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        token_provider=None,
    ) -> None:
        self._sa_json = sa_json
        self._project = project
        self._location = location
        self._model = model
        self._temperature = temperature
        self._timeout = timeout
        self._transport = transport
        self._token_provider = token_provider

    def __repr__(self) -> str:
        # SA JSON（秘密鍵を含む）を平文で漏らさない。
        return (
            f"VertexAILLM(project={self._project!r}, location={self._location!r}, "
            f"model={self._model!r}, sa_json={'***' if self._sa_json else None})"
        )

    @property
    def _endpoint(self) -> str:
        return (
            f"https://{self._location}-aiplatform.googleapis.com/v1/"
            f"projects/{self._project}/locations/{self._location}/"
            f"publishers/google/models/{self._model}:streamGenerateContent"
        )

    def _fetch_token_sync(self) -> str:
        """SA JSON から OAuth Bearer トークンを取得する（同期・ブロッキング）。"""
        import json

        import google.auth.transport.requests
        from google.oauth2 import service_account

        if not self._sa_json:
            raise RuntimeError(
                "Vertex AI にはサービスアカウント JSON が必要です（api_key に SA JSON を設定してください）。"
            )
        info = json.loads(self._sa_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=_SCOPES
        )
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token

    async def _access_token(self) -> str:
        if self._token_provider is not None:
            result = self._token_provider()
            if inspect.isawaitable(result):
                result = await result
            return result
        return await asyncio.to_thread(self._fetch_token_sync)

    async def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        payload = build_generate_content_payload(messages, self._temperature)
        token = await self._access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        params = {"alt": "sse"}
        async with (
            httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client,
            client.stream(
                "POST", self._endpoint, params=params, json=payload, headers=headers
            ) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                for text in parse_sse_texts(line):
                    yield text
