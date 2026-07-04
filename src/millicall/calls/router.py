import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.calls.schemas import CallCreate, CallCreated
from millicall.config import Settings
from millicall.deps import get_esl_factory, get_session, get_settings_dep, require_admin
from millicall.models import Extension
from millicall.telephony.esl import ESLError

logger = logging.getLogger("millicall.calls")

router = APIRouter(prefix="/api/calls", tags=["calls"], dependencies=[Depends(require_admin)])


@router.post("", response_model=CallCreated, status_code=status.HTTP_201_CREATED)
async def create_call(
    body: CallCreate,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    esl_factory=Depends(get_esl_factory),
) -> CallCreated:
    ext = await session.scalar(select(Extension).where(Extension.number == body.from_extension))
    if ext is None or not ext.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="from_extension not found or disabled"
        )

    call_uuid = uuid.uuid4().hex
    variables = (
        f"origination_uuid={call_uuid},"
        f"origination_caller_id_number={ext.number},"
        f"origination_caller_id_name={ext.number}"
    )
    command = (
        f"originate {{{variables}}}user/{body.from_extension}@{settings.sip_domain} "
        f"{body.to} XML default"
    )

    client = esl_factory()
    try:
        async def _connect_and_originate():
            await client.connect()
            await client.bgapi(command)

        await asyncio.wait_for(_connect_and_originate(), timeout=settings.esl_timeout_seconds)
    except (OSError, ESLError, TimeoutError) as exc:
        logger.warning("originate failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="FreeSWITCH unreachable"
        ) from exc
    finally:
        await client.close()

    return CallCreated(call_uuid=call_uuid)
