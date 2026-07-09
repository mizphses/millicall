"""Vertex AI 経由 Gemini (streamGenerateContent SSE) のユニットテスト。

認証は SA JSON → OAuth Bearer トークン。実 GCP を叩かないよう ``token_provider`` を
注入し、HTTP は httpx.MockTransport で差し替える（gemini テストと同型）。
SA JSON / トークンが repr・例外・URL に漏れないことを検証する。
"""

import json

import httpx
import pytest

from millicall.ai.llm.base import ChatMessage
from millicall.ai.llm.vertex import VertexAILLM

# 実 SA JSON を模した最小構造（秘密鍵は当然ダミー）。
_SA_JSON = json.dumps(
    {
        "type": "service_account",
        "project_id": "proj-x",
        "private_key_id": "kid",
        "private_key": "-----BEGIN PRIVATE KEY-----\nSUPERSECRETKEY\n-----END PRIVATE KEY-----\n",
        "client_email": "sa@proj-x.iam.gserviceaccount.com",
    }
)

_SSE = (
    'data: {"candidates":[{"content":{"parts":[{"text":"こん"}]}}]}\n\n'
    'data: {"candidates":[{"content":{"parts":[{"text":"にちは"}]}}]}\n\n'
)


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    # location + project + model がエンドポイントに正しく組まれている
    assert "us-central1-aiplatform.googleapis.com" in url
    assert "projects/proj-x/locations/us-central1" in url
    assert "publishers/google/models/gemini-2.0-flash:streamGenerateContent" in url
    assert request.url.params.get("alt") == "sse"
    # Bearer トークンは token_provider が返した値
    assert request.headers["authorization"] == "Bearer test-token"
    body = request.read().decode()
    assert '"systemInstruction"' in body
    # SA JSON の秘密が URL / body に漏れていない
    assert "SUPERSECRETKEY" not in url
    assert "SUPERSECRETKEY" not in body
    return httpx.Response(200, text=_SSE, headers={"content-type": "text/event-stream"})


@pytest.mark.asyncio
async def test_vertex_stream():
    llm = VertexAILLM(
        sa_json=_SA_JSON,
        project="proj-x",
        transport=httpx.MockTransport(_handler),
        token_provider=lambda: "test-token",
    )
    tokens = [
        t async for t in llm.stream_chat([ChatMessage("system", "sys"), ChatMessage("user", "hi")])
    ]
    assert tokens == ["こん", "にちは"]


@pytest.mark.asyncio
async def test_vertex_accepts_async_token_provider():
    async def provider() -> str:
        return "test-token"

    llm = VertexAILLM(
        sa_json=_SA_JSON,
        project="proj-x",
        transport=httpx.MockTransport(_handler),
        token_provider=provider,
    )
    tokens = [
        t async for t in llm.stream_chat([ChatMessage("system", "sys"), ChatMessage("user", "hi")])
    ]
    assert tokens == ["こん", "にちは"]


def test_vertex_repr_masks_sa_json():
    llm = VertexAILLM(sa_json=_SA_JSON, project="proj-x")
    text = repr(llm)
    assert "SUPERSECRETKEY" not in text
    assert "service_account" not in text
    assert "***" in text
    assert "proj-x" in text  # 非秘密のプロジェクトは出てよい


@pytest.mark.asyncio
async def test_vertex_error_does_not_leak_secrets():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text='{"error":"forbidden"}')

    llm = VertexAILLM(
        sa_json=_SA_JSON,
        project="proj-x",
        transport=httpx.MockTransport(handler),
        token_provider=lambda: "test-token",
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        async for _ in llm.stream_chat([ChatMessage("user", "hi")]):
            pass
    msg = str(exc_info.value)
    assert "SUPERSECRETKEY" not in msg
    assert "test-token" not in msg


@pytest.mark.asyncio
async def test_vertex_custom_location_and_model():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "asia-northeast1-aiplatform.googleapis.com" in url
        assert "locations/asia-northeast1" in url
        assert "models/gemini-1.5-pro:streamGenerateContent" in url
        return httpx.Response(200, text=_SSE, headers={"content-type": "text/event-stream"})

    llm = VertexAILLM(
        sa_json=_SA_JSON,
        project="proj-x",
        location="asia-northeast1",
        model="gemini-1.5-pro",
        transport=httpx.MockTransport(handler),
        token_provider=lambda: "test-token",
    )
    tokens = [t async for t in llm.stream_chat([ChatMessage("user", "hi")])]
    assert tokens == ["こん", "にちは"]


# ---------------------------------------------------------------------------
# auth_method="api_key" (Vertex AI express mode)
# ---------------------------------------------------------------------------


def _express_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    # express mode: プロジェクト/ロケーション無しのグローバルエンドポイント
    assert url.startswith("https://aiplatform.googleapis.com/v1/publishers/google/models/")
    assert "projects/" not in url
    assert "gemini-2.0-flash:streamGenerateContent" in url
    assert request.url.params.get("alt") == "sse"
    # API キーはヘッダーで渡し、URL クエリには載せない
    assert request.headers["x-goog-api-key"] == "AIzaTESTKEY"
    assert "AIzaTESTKEY" not in url
    assert "authorization" not in request.headers
    return httpx.Response(200, text=_SSE, headers={"content-type": "text/event-stream"})


@pytest.mark.asyncio
async def test_vertex_api_key_express_mode():
    llm = VertexAILLM(
        sa_json=None,
        api_key="AIzaTESTKEY",
        auth_method="api_key",
        project="",
        transport=httpx.MockTransport(_express_handler),
    )
    tokens = [
        t async for t in llm.stream_chat([ChatMessage("system", "sys"), ChatMessage("user", "hi")])
    ]
    assert tokens == ["こん", "にちは"]


def test_vertex_repr_does_not_leak_api_key():
    llm = VertexAILLM(sa_json=None, api_key="AIzaSECRET", auth_method="api_key", project="")
    assert "AIzaSECRET" not in repr(llm)
