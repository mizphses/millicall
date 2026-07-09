"""Workflow persistence helpers: definition validation,
and LLM-backed definition generation (Phase 4b Task 2).

統一番号プラン移行後: workflow.number は番号プランの実体で、dialplan は
workflows テーブルから直接生成される（Route プロビジョニングは廃止）。
"""

import json
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.ai import registry
from millicall.crypto import SecretBox
from millicall.models import Provider
from millicall.workflows.errors import WorkflowValidationError
from millicall.workflows.nodes import node_type_catalog
from millicall.workflows.schema import WorkflowDefinition, validate_graph


class NoLlmProviderError(Exception):
    """No enabled LLM provider is configured for AI generation (maps to 503)."""


def validate_definition(definition: dict[str, Any], workflow_id: int | None = None) -> list[str]:
    """Parse + graph-validate a raw definition dict.

    Raises ``WorkflowValidationError`` (hard violations -> HTTP 422) on typed
    parse failure or graph errors. Returns the advisory warnings list on success.
    """
    try:
        defn = WorkflowDefinition.model_validate(definition)
    except ValidationError as exc:
        raise WorkflowValidationError([_format_pydantic_error(e) for e in exc.errors()]) from None
    result = validate_graph(defn, workflow_id=workflow_id)
    if result.errors:
        raise WorkflowValidationError(result.errors)
    return result.warnings


def _format_pydantic_error(err: dict[str, Any]) -> str:
    loc = ".".join(str(p) for p in err.get("loc", ()))
    return f"{loc}: {err.get('msg', 'invalid')}" if loc else str(err.get("msg", "invalid"))


# --------------------------------------------------------------------------- #
# AI generation
# --------------------------------------------------------------------------- #

_GENERATE_SYSTEM = (
    "あなたは電話 IVR/AI 応対ワークフローを設計するアシスタントです。"
    "利用可能なノード種別とその設定/出力ハンドルは以下の JSON カタログの通りです。"
    "ユーザーの要望に沿って、キー 'nodes' と 'edges' を持つワークフロー定義を "
    "1 つだけ JSON で出力してください。各 node は {id, type, config} を持ち、type は "
    "カタログの type のいずれか、config は config_schema に従うこと。start ノードは "
    "ちょうど 1 つ。edge は {id, source, target, sourceHandle} を持ち、sourceHandle は "
    "ソースノードの output_handles のいずれかであること。JSON のみを返し、説明文は付けない。"
)


def _extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of an LLM response (tolerating ``` fences/prose)."""
    stripped = text.strip()
    if "```" in stripped:
        # take the content of the first fenced block
        parts = stripped.split("```")
        for chunk in parts:
            chunk = chunk.strip()
            if chunk.startswith("json"):
                chunk = chunk[4:].strip()
            if chunk.startswith("{"):
                stripped = chunk
                break
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise WorkflowValidationError(["LLM did not return a JSON object"])
    try:
        return json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise WorkflowValidationError([f"LLM returned invalid JSON: {exc}"]) from None


async def _select_llm(session: AsyncSession, box: SecretBox):
    provider = await session.scalar(
        select(Provider)
        .where(Provider.type == "llm", Provider.enabled.is_(True))
        .order_by(Provider.id)
    )
    if provider is None:
        raise NoLlmProviderError("no enabled LLM provider configured")
    config = json.loads(provider.config_json or "{}")
    api_key = box.decrypt(provider.api_key_encrypted) if provider.api_key_encrypted else None
    return registry.build_llm(provider.kind, config, api_key)


async def generate_definition(
    session: AsyncSession, box: SecretBox, prompt: str
) -> tuple[dict[str, Any], list[str]]:
    """Generate + validate a workflow definition via the configured LLM.

    Raises ``NoLlmProviderError`` (503) when no LLM provider exists and
    ``WorkflowValidationError`` (422) when the generated definition fails
    validation — the old implementation returned unvalidated definitions.
    """
    from millicall.ai.llm.base import ChatMessage

    llm = await _select_llm(session, box)
    catalog = json.dumps(node_type_catalog(), ensure_ascii=False)
    messages = [
        ChatMessage(role="system", content=f"{_GENERATE_SYSTEM}\n\nカタログ:\n{catalog}"),
        ChatMessage(role="user", content=prompt),
    ]
    chunks: list[str] = []
    agen = llm.stream_chat(messages)
    try:
        async for tok in agen:
            chunks.append(tok)
    finally:
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            await aclose()
    definition = _extract_json("".join(chunks))
    warnings = validate_definition(definition)
    return definition, warnings
