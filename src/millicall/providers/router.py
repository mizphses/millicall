import json
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.ai import registry
from millicall.crypto import SecretBox, mask_secret
from millicall.deps import get_secret_box, get_session, require_admin
from millicall.models import Provider
from millicall.providers.enums import KIND_BY_TYPE, ProviderKind, ProviderType
from millicall.providers.schemas import (
    ProviderCreate,
    ProviderRead,
    ProviderTestResult,
    ProviderUpdate,
)

router = APIRouter(
    prefix="/api/providers", tags=["providers"], dependencies=[Depends(require_admin)]
)


def _decrypt_or_empty(p: Provider, box: SecretBox) -> str:
    """マスク専用: 平文は保持せず、末尾4文字のマスクだけ作るために復号する。"""
    if not p.api_key_encrypted:
        return ""
    try:
        return box.decrypt(p.api_key_encrypted)
    except Exception:
        return ""


def _to_read(p: Provider, box: SecretBox) -> ProviderRead:
    return ProviderRead(
        id=p.id,
        name=p.name,
        type=p.type,
        kind=p.kind,
        config=json.loads(p.config_json or "{}"),
        api_key_masked=mask_secret(_decrypt_or_empty(p, box)),
        enabled=p.enabled,
    )


def _redact(detail: str, api_key: str | None) -> str:
    """例外 detail 文字列から api_key 平文を除去して返す。

    api_key が truthy かつ detail に含まれる場合のみ置換し、
    それ以外は detail をそのまま返す。
    """
    if api_key and api_key in detail:
        return detail.replace(api_key, "****")
    return detail


def _validate_kind(ptype: ProviderType, kind: ProviderKind) -> None:
    if kind not in KIND_BY_TYPE[ptype]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"kind {kind} is not valid for type {ptype}",
        )


@router.post("", response_model=ProviderRead, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderCreate,
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
) -> ProviderRead:
    _validate_kind(body.type, body.kind)
    provider = Provider(
        name=body.name,
        type=body.type.value,
        kind=body.kind.value,
        config_json=json.dumps(body.config),
        api_key_encrypted=box.encrypt(body.api_key) if body.api_key else None,
        enabled=body.enabled,
    )
    session.add(provider)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="name exists"
        ) from None
    await session.refresh(provider)
    return _to_read(provider, box)


@router.get("", response_model=list[ProviderRead])
async def list_providers(
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
) -> list[ProviderRead]:
    result = await session.scalars(select(Provider).order_by(Provider.name))
    return [_to_read(p, box) for p in result]


@router.patch("/{provider_id}", response_model=ProviderRead)
async def update_provider(
    provider_id: int,
    body: ProviderUpdate,
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
) -> ProviderRead:
    p = await session.get(Provider, provider_id)
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if body.name is not None:
        p.name = body.name
    if body.config is not None:
        p.config_json = json.dumps(body.config)
    if body.api_key is not None:
        p.api_key_encrypted = box.encrypt(body.api_key) if body.api_key else None
    if body.enabled is not None:
        p.enabled = body.enabled
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="name exists"
        ) from None
    await session.refresh(p)
    return _to_read(p, box)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    p = await session.get(Provider, provider_id)
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await session.delete(p)
    await session.commit()


@router.post("/{provider_id}/test", response_model=ProviderTestResult)
async def test_provider(
    provider_id: int,
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
) -> ProviderTestResult:
    p = await session.get(Provider, provider_id)
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    config = json.loads(p.config_json or "{}")
    api_key = box.decrypt(p.api_key_encrypted) if p.api_key_encrypted else None
    started = time.perf_counter()
    try:
        if p.type == ProviderType.LLM:
            # ChatMessage は後続タスク(Task 4)で追加される ai.llm.base に属するため遅延 import。
            # 現行 build_llm は全 kind で UnknownProviderKind を送出し、ここへ到達しない。
            from millicall.ai.llm.base import ChatMessage

            llm = registry.build_llm(p.kind, config, api_key)
            agen = llm.stream_chat([ChatMessage(role="user", content="ping")])
            first = await agen.__anext__()
            await agen.aclose()
            detail = f"first token: {first[:20]!r}"
        elif p.type == ProviderType.TTS:
            tts = registry.build_tts(p.kind, config, api_key)
            pcm = await tts.synthesize("テスト")
            detail = f"{len(pcm)} bytes pcm"
        else:
            stt = registry.build_stt(p.kind, config, api_key)
            # 無音 200ms(8k/16bit=3200bytes) を投げて疎通のみ確認
            sess = stt.open_session()
            await sess.feed(b"\x00" * 3200)
            text = await sess.finish()
            detail = f"transcript: {text!r}"
    except registry.UnknownProviderKind:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"kind {p.kind} not implemented yet",
        ) from None
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.perf_counter() - started) * 1000)
        safe_detail = _redact(str(exc), api_key)[:200]
        return ProviderTestResult(ok=False, detail=safe_detail, latency_ms=elapsed)
    elapsed = int((time.perf_counter() - started) * 1000)
    return ProviderTestResult(ok=True, detail=detail, latency_ms=elapsed)
