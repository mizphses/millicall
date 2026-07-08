from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.deps import get_change_listener, get_session, require_admin
from millicall.models import Trunk
from millicall.telephony.hooks import ExtensionChangeListener
from millicall.trunks.schemas import TrunkCreate, TrunkRead, TrunkUpdate

router = APIRouter(prefix="/api/trunks", tags=["trunks"], dependencies=[Depends(require_admin)])


@router.post("", response_model=TrunkRead, status_code=status.HTTP_201_CREATED)
async def create_trunk(
    body: TrunkCreate,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> TrunkRead:
    existing = await session.scalar(select(Trunk).where(Trunk.name == body.name))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Trunk name already exists"
        )
    trunk = Trunk(
        name=body.name,
        display_name=body.display_name,
        host=body.host,
        username=body.username,
        password=body.password,
        did_number=body.did_number,
        caller_id=body.caller_id,
        enabled=body.enabled,
    )
    session.add(trunk)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Trunk name already exists"
        ) from None
    await session.refresh(trunk)
    await listener.notify(session)
    return TrunkRead.from_orm_trunk(trunk)


@router.get("", response_model=list[TrunkRead])
async def list_trunks(session: AsyncSession = Depends(get_session)) -> list[TrunkRead]:
    result = await session.scalars(select(Trunk).order_by(Trunk.name))
    return [TrunkRead.from_orm_trunk(t) for t in result]


@router.get("/{trunk_id}", response_model=TrunkRead)
async def get_trunk(trunk_id: int, session: AsyncSession = Depends(get_session)) -> TrunkRead:
    trunk = await session.get(Trunk, trunk_id)
    if trunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return TrunkRead.from_orm_trunk(trunk)


@router.patch("/{trunk_id}", response_model=TrunkRead)
async def update_trunk(
    trunk_id: int,
    body: TrunkUpdate,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> TrunkRead:
    trunk = await session.get(Trunk, trunk_id)
    if trunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    for fld in (
        "display_name",
        "host",
        "username",
        "password",
        "did_number",
        "caller_id",
        "enabled",
    ):
        val = getattr(body, fld)
        if val is not None:
            setattr(trunk, fld, val)
    await session.commit()
    await session.refresh(trunk)
    await listener.notify(session)
    return TrunkRead.from_orm_trunk(trunk)


@router.delete("/{trunk_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trunk(
    trunk_id: int,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> None:
    trunk = await session.get(Trunk, trunk_id)
    if trunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await session.delete(trunk)
    await session.commit()
    await listener.notify(session)
