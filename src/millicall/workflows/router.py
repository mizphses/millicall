"""Workflow CRUD + node-types/handles delivery + AI generation (Phase 4b Task 2).

POST/PUT validate the graph before persisting (typed parse + ``validate_graph``);
hard violations reject with 422 while advisory ``warnings`` ride along on the
2xx response. Each save keeps a ``target_type='workflow'`` Route in sync so
inbound calls route through the existing dialplan path.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.crypto import SecretBox
from millicall.deps import get_change_listener, get_secret_box, get_session, require_admin
from millicall.models import Workflow
from millicall.numberplan import NumberConflictError, assert_number_free
from millicall.telephony.hooks import ExtensionChangeListener
from millicall.workflows.handles import HANDLE_VOCAB
from millicall.workflows.nodes import node_type_catalog
from millicall.workflows.schemas import (
    WorkflowGenerateRequest,
    WorkflowGenerateResponse,
    WorkflowRead,
    WorkflowUpsert,
)
from millicall.workflows.service import (
    generate_definition,
    validate_definition,
)

router = APIRouter(
    prefix="/api/workflows", tags=["workflows"], dependencies=[Depends(require_admin)]
)


def _to_read(wf: Workflow, warnings: list[str]) -> WorkflowRead:
    return WorkflowRead(
        id=wf.id,
        name=wf.name,
        number=wf.number,
        description=wf.description,
        default_tts_provider_id=wf.default_tts_provider_id,
        enabled=wf.enabled,
        definition=json.loads(wf.definition_json or "{}"),
        warnings=warnings,
        created_at=wf.created_at,
        updated_at=wf.updated_at,
    )


# --------------------------------------------------------------------------- #
# static-path routes (declared before /{workflow_id})
# --------------------------------------------------------------------------- #


@router.get("/node-types")
async def get_node_types() -> list[dict]:
    return node_type_catalog()


@router.get("/handles")
async def get_handles() -> dict[str, list[str]]:
    return HANDLE_VOCAB


@router.post("/generate", response_model=WorkflowGenerateResponse)
async def generate_workflow(
    body: WorkflowGenerateRequest,
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
) -> WorkflowGenerateResponse:
    definition, warnings = await generate_definition(session, box, body.prompt)
    return WorkflowGenerateResponse(definition=definition, warnings=warnings)


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


@router.post("", response_model=WorkflowRead, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    body: WorkflowUpsert,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> WorkflowRead:
    warnings = validate_definition(body.definition)
    wf = Workflow(
        name=body.name,
        number=body.number,
        description=body.description,
        default_tts_provider_id=body.default_tts_provider_id,
        definition_json=json.dumps(body.definition, ensure_ascii=False),
        enabled=body.enabled,
    )
    try:
        # 統一番号プラン: 4テーブル横断の番号一意チェック
        await assert_number_free(session, body.number)
    except NumberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    session.add(wf)
    try:
        await session.flush()  # assign wf.id (and catch number UNIQUE)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="number already in use"
        ) from None
    await session.refresh(wf)
    await listener.notify(session)
    return _to_read(wf, warnings)


@router.get("", response_model=list[WorkflowRead])
async def list_workflows(session: AsyncSession = Depends(get_session)) -> list[WorkflowRead]:
    result = await session.scalars(select(Workflow).order_by(Workflow.number))
    return [_to_read(wf, []) for wf in result]


@router.get("/{workflow_id}", response_model=WorkflowRead)
async def get_workflow(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> WorkflowRead:
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _to_read(wf, [])


@router.put("/{workflow_id}", response_model=WorkflowRead)
async def update_workflow(
    workflow_id: int,
    body: WorkflowUpsert,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> WorkflowRead:
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    warnings = validate_definition(body.definition, workflow_id=workflow_id)
    try:
        await assert_number_free(session, body.number, exclude=("workflow", workflow_id))
    except NumberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    wf.name = body.name
    wf.number = body.number
    wf.description = body.description
    wf.default_tts_provider_id = body.default_tts_provider_id
    wf.enabled = body.enabled
    wf.definition_json = json.dumps(body.definition, ensure_ascii=False)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="number already in use"
        ) from None
    await session.refresh(wf)
    await listener.notify(session)
    return _to_read(wf, warnings)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: int,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> None:
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await session.delete(wf)
    await session.commit()
    await listener.notify(session)
