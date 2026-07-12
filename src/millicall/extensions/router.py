from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.app_settings.service import effective_settings
from millicall.audit import get_client_ip, record_audit
from millicall.deps import get_change_listener, get_session, require_admin
from millicall.extensions.schemas import (
    ExtensionCreate,
    ExtensionCredentials,
    ExtensionRead,
    ExtensionUpdate,
)
from millicall.gen import generate_sip_password
from millicall.models import Extension, User
from millicall.numberplan import NumberConflictError, assert_number_free
from millicall.sip_endpoint import resolve_internal_sip_endpoint
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
    try:
        # 統一番号プラン: AI/ワークフロー/グループとも重複しないこと
        await assert_number_free(session, body.number)
    except NumberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
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


@router.get("/{ext_id}/credentials", response_model=ExtensionCredentials)
async def get_extension_credentials(
    ext_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ExtensionCredentials:
    """内線の SIP 接続情報（平文パスワード含む）を返す（管理者専用）。

    ソフトフォン（Zoiper 等）を社内LAN から手動設定するための資格情報を返す。
    sip_server / domain は telephony 設定生成（PR #57）の internal ロジックと
    同じ結果になるよう resolve_internal_sip_endpoint で算出する。
    閲覧は監査ログに記録するが、パスワードそのものはログに残さない。
    存在しない内線は 404。
    """
    ext = await session.get(Extension, ext_id)
    if ext is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    settings = await effective_settings(request.app.state)
    endpoint = await resolve_internal_sip_endpoint(session, settings)

    # 監査ログ: 認証情報の閲覧を記録（内線番号/ID のみ。パスワードは残さない）。
    await record_audit(
        session,
        actor_user_id=admin.id,
        actor_label=admin.username,
        action="extension.credentials.view",
        target_type="extension",
        target_id=str(ext.id),
        detail={"number": ext.number},
        ip_address=get_client_ip(request),
    )
    await session.commit()

    return ExtensionCredentials(
        number=ext.number,
        password=ext.sip_password,
        sip_server=endpoint.sip_server,
        sip_port=settings.sip_port,
        domain=endpoint.domain,
        display_name=ext.display_name,
        transport="UDP",
    )


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
