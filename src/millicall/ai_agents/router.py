from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.ai_agents.schemas import AiAgentCreate, AiAgentRead, AiAgentUpdate
from millicall.deps import get_session, require_admin
from millicall.models import AiAgent, Provider

router = APIRouter(
    prefix="/api/ai-agents", tags=["ai-agents"], dependencies=[Depends(require_admin)]
)


async def _check_provider(session: AsyncSession, pid: int, expected_type: str) -> None:
    p = await session.get(Provider, pid)
    if p is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"provider {pid} does not exist",
        )
    if p.type != expected_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"provider {pid} is type {p.type}, expected {expected_type}",
        )


@router.post("", response_model=AiAgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AiAgentCreate, session: AsyncSession = Depends(get_session)
) -> AiAgent:
    await _check_provider(session, body.llm_provider_id, "llm")
    await _check_provider(session, body.tts_provider_id, "tts")
    await _check_provider(session, body.stt_provider_id, "stt")
    agent = AiAgent(
        name=body.name,
        system_prompt=body.system_prompt,
        greeting=body.greeting,
        llm_provider_id=body.llm_provider_id,
        tts_provider_id=body.tts_provider_id,
        stt_provider_id=body.stt_provider_id,
        max_history=body.max_history,
        silence_end_ms=body.silence_end_ms,
        enabled=body.enabled,
    )
    session.add(agent)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="name exists") from None
    await session.refresh(agent)
    return agent


@router.get("", response_model=list[AiAgentRead])
async def list_agents(session: AsyncSession = Depends(get_session)) -> list[AiAgent]:
    result = await session.scalars(select(AiAgent).order_by(AiAgent.name))
    return list(result)


@router.get("/{agent_id}", response_model=AiAgentRead)
async def get_agent(agent_id: int, session: AsyncSession = Depends(get_session)) -> AiAgent:
    agent = await session.get(AiAgent, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return agent


@router.patch("/{agent_id}", response_model=AiAgentRead)
async def update_agent(
    agent_id: int, body: AiAgentUpdate, session: AsyncSession = Depends(get_session)
) -> AiAgent:
    agent = await session.get(AiAgent, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if body.llm_provider_id is not None:
        await _check_provider(session, body.llm_provider_id, "llm")
        agent.llm_provider_id = body.llm_provider_id
    if body.tts_provider_id is not None:
        await _check_provider(session, body.tts_provider_id, "tts")
        agent.tts_provider_id = body.tts_provider_id
    if body.stt_provider_id is not None:
        await _check_provider(session, body.stt_provider_id, "stt")
        agent.stt_provider_id = body.stt_provider_id
    for attr in (
        "name",
        "system_prompt",
        "greeting",
        "max_history",
        "silence_end_ms",
        "enabled",
    ):
        val = getattr(body, attr)
        if val is not None:
            setattr(agent, attr, val)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="name exists") from None
    await session.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: int, session: AsyncSession = Depends(get_session)) -> None:
    agent = await session.get(AiAgent, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await session.delete(agent)
    await session.commit()
