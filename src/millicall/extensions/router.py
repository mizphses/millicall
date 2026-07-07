from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.deps import get_change_listener, get_session, require_admin
from millicall.extensions.schemas import ExtensionCreate, ExtensionRead, ExtensionUpdate
from millicall.gen import generate_sip_password
from millicall.models import Extension
from millicall.telephony.hooks import ExtensionChangeListener

router = APIRouter(
    prefix="/api/extensions", tags=["extensions"], dependencies=[Depends(require_admin)]
)


@router.post("", response_model=ExtensionRead, status_code=status.HTTP_201_CREATED)
async def create_extension(
    body: ExtensionCreate,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> Extension:
    existing = await session.scalar(select(Extension).where(Extension.number == body.number))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Extension number already exists"
        )
    ext = Extension(
        number=body.number,
        display_name=body.display_name,
        sip_password=generate_sip_password(),
        enabled=True,
        calling_permission=body.calling_permission,
    )
    session.add(ext)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Extension number already exists"
        ) from None
    await session.refresh(ext)
    await listener.notify(session)
    return ext


@router.get("", response_model=list[ExtensionRead])
async def list_extensions(session: AsyncSession = Depends(get_session)) -> list[Extension]:
    result = await session.scalars(select(Extension).order_by(Extension.number))
    return list(result)


@router.get("/{ext_id}", response_model=ExtensionRead)
async def get_extension(ext_id: int, session: AsyncSession = Depends(get_session)) -> Extension:
    ext = await session.get(Extension, ext_id)
    if ext is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return ext


@router.patch("/{ext_id}", response_model=ExtensionRead)
async def update_extension(
    ext_id: int,
    body: ExtensionUpdate,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> Extension:
    ext = await session.get(Extension, ext_id)
    if ext is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if body.display_name is not None:
        ext.display_name = body.display_name
    if body.enabled is not None:
        ext.enabled = body.enabled
    if body.calling_permission is not None:
        ext.calling_permission = body.calling_permission
    await session.commit()
    await session.refresh(ext)
    await listener.notify(session)
    return ext


@router.delete("/{ext_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_extension(
    ext_id: int,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> None:
    ext = await session.get(Extension, ext_id)
    if ext is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await session.delete(ext)
    await session.commit()
    await listener.notify(session)
