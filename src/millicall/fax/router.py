"""FAX 受信 API（issue #121）。管理者のみ（他の管理 API と同じく require_admin）。

- /api/fax-receivers : FAX 受信番号（統一番号プランの kind="fax"）の CRUD。
  変更は TelephonyChangeListener.notify で dialplan 再生成 + reloadxml を発火する。
- /api/faxes         : 受信 FAX の一覧・詳細・PDF ダウンロード・削除。
  PDF/TIFF は DB に記録した絶対パスを fax_dir 配下か検証してから扱う。
"""

import contextlib
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.audit import get_client_ip, record_audit
from millicall.config import Settings
from millicall.deps import get_change_listener, get_session, get_settings_dep, require_admin
from millicall.fax.service import resolve_inside
from millicall.models import Fax, FaxReceiver, Trunk, User
from millicall.numberplan import KIND_FAX, NumberConflictError, assert_number_free
from millicall.telephony.hooks import ExtensionChangeListener

router = APIRouter(prefix="/api", tags=["faxes"], dependencies=[Depends(require_admin)])

_NUMBER_RE = r"^\d{2,6}$"


# --------------------------------------------------------------------------- 受信番号


class FaxReceiverUpsert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: str = Field(..., pattern=_NUMBER_RE)
    name: str = Field(..., min_length=1, max_length=100)
    enabled: bool = True


class FaxReceiverRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    name: str
    enabled: bool


@router.get("/fax-receivers", response_model=list[FaxReceiverRead])
async def list_fax_receivers(session: AsyncSession = Depends(get_session)) -> list[FaxReceiver]:
    return list(await session.scalars(select(FaxReceiver).order_by(FaxReceiver.number)))


@router.post(
    "/fax-receivers", response_model=FaxReceiverRead, status_code=status.HTTP_201_CREATED
)
async def create_fax_receiver(
    body: FaxReceiverUpsert,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> FaxReceiver:
    try:
        await assert_number_free(session, body.number)
    except NumberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    receiver = FaxReceiver(number=body.number, name=body.name, enabled=body.enabled)
    session.add(receiver)
    await session.commit()
    await session.refresh(receiver)
    await listener.notify(session)
    return receiver


@router.patch("/fax-receivers/{receiver_id}", response_model=FaxReceiverRead)
async def update_fax_receiver(
    receiver_id: int,
    body: FaxReceiverUpsert,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> FaxReceiver:
    receiver = await session.get(FaxReceiver, receiver_id)
    if receiver is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        await assert_number_free(session, body.number, exclude=(KIND_FAX, receiver_id))
    except NumberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    old_number = receiver.number
    receiver.number = body.number
    receiver.name = body.name
    receiver.enabled = body.enabled
    if old_number != body.number:
        # FAX 自動検知の切替先も追従させる（参照切れで検知が黙って止まるのを防ぐ）。
        trunks = await session.scalars(select(Trunk).where(Trunk.fax_route_number == old_number))
        for t in trunks:
            t.fax_route_number = body.number
    await session.commit()
    await session.refresh(receiver)
    await listener.notify(session)
    return receiver


@router.delete("/fax-receivers/{receiver_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fax_receiver(
    receiver_id: int,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> None:
    receiver = await session.get(FaxReceiver, receiver_id)
    if receiver is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    referenced = await session.scalar(
        select(Trunk.name).where(Trunk.fax_route_number == receiver.number)
    )
    if referenced is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"トランク {referenced} の FAX 自動検知の切替先に使われているため削除できません",
        )
    await session.delete(receiver)
    await session.commit()
    await listener.notify(session)


# --------------------------------------------------------------------------- 受信 FAX


class FaxRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    call_uuid: str
    from_number: str
    to_number: str
    remote_station_id: str
    pages: int
    success: bool
    result_text: str
    has_pdf: bool
    received_at: datetime


class FaxList(BaseModel):
    items: list[FaxRead]
    total: int


def _to_read(fax: Fax) -> FaxRead:
    return FaxRead(
        id=fax.id,
        call_uuid=fax.call_uuid,
        from_number=fax.from_number,
        to_number=fax.to_number,
        remote_station_id=fax.remote_station_id,
        pages=fax.pages,
        success=fax.success,
        result_text=fax.result_text,
        has_pdf=bool(fax.pdf_path),
        received_at=fax.received_at,
    )


async def _get_fax(session: AsyncSession, fax_id: int) -> Fax:
    fax = await session.get(Fax, fax_id)
    if fax is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FAX が見つかりません")
    return fax


def _safe_path(settings: Settings, stored: str | None) -> Path | None:
    """DB に記録したパスを fax_dir 配下に限って返す（範囲外・未記録は None）。"""
    if not stored:
        return None
    try:
        return resolve_inside(settings.fax_dir, Path(stored))
    except (ValueError, OSError):
        return None


@router.get("/faxes", response_model=FaxList)
async def list_faxes(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> FaxList:
    total = await session.scalar(select(func.count()).select_from(Fax)) or 0
    rows = await session.scalars(
        select(Fax).order_by(Fax.received_at.desc(), Fax.id.desc()).offset(offset).limit(limit)
    )
    return FaxList(items=[_to_read(f) for f in rows], total=total)


@router.get("/faxes/{fax_id}", response_model=FaxRead)
async def get_fax(fax_id: int, session: AsyncSession = Depends(get_session)) -> FaxRead:
    return _to_read(await _get_fax(session, fax_id))


@router.get("/faxes/{fax_id}/pdf", response_class=FileResponse)
async def download_fax_pdf(
    fax_id: int,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> FileResponse:
    fax = await _get_fax(session, fax_id)
    path = _safe_path(settings, fax.pdf_path)
    if path is None or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF がありません")
    stamp = fax.received_at.strftime("%Y%m%d-%H%M%S")
    return FileResponse(
        path, media_type="application/pdf", filename=f"fax-{fax.id}-{stamp}.pdf"
    )


@router.delete("/faxes/{fax_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fax(
    fax_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    user: User = Depends(require_admin),
) -> None:
    fax = await _get_fax(session, fax_id)
    for stored in (fax.pdf_path, fax.tiff_path):
        path = _safe_path(settings, stored)
        if path is not None:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
    await session.delete(fax)
    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="fax.delete",
        target_type="fax",
        target_id=str(fax_id),
        detail={"from": fax.from_number, "to": fax.to_number},
        ip_address=get_client_ip(request),
    )
    await session.commit()
