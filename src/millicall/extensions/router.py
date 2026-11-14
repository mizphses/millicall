from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.app_settings.service import effective_settings
from millicall.audit import get_client_ip, record_audit
from millicall.crypto import SecretBox
from millicall.deps import get_change_listener, get_secret_box, get_session, require_admin
from millicall.extensions.schemas import (
    ExtensionCreate,
    ExtensionCredentials,
    ExtensionDeviceSummary,
    ExtensionRead,
    ExtensionUpdate,
)
from millicall.models import Device, Extension, User
from millicall.numberplan import NumberConflictError, assert_number_free
from millicall.provisioning.service import chunked, devices_bound_to, unbind_device
from millicall.sip_credentials import decrypt_extension_password, new_extension_password
from millicall.sip_endpoint import resolve_internal_sip_endpoint
from millicall.telephony.hooks import ExtensionChangeListener

router = APIRouter(
    prefix="/api/extensions", tags=["extensions"], dependencies=[Depends(require_admin)]
)


def _device_summary(device: Device) -> ExtensionDeviceSummary:
    return ExtensionDeviceSummary(
        device_id=device.id,
        mac_address=device.mac_address,
        model=device.model,
        ip_address=device.ip_address,
        last_seen=device.last_seen,
    )


async def _to_reads(session: AsyncSession, exts: list[Extension]) -> list[ExtensionRead]:
    """内線のリストを、紐づく端末の要約付きの ExtensionRead に変換する（端末は一括取得）。"""
    by_ext: dict[int, list[ExtensionDeviceSummary]] = {}
    for chunk in chunked([e.id for e in exts]):
        devices = await session.scalars(
            select(Device).where(Device.extension_id.in_(chunk)).order_by(Device.id)
        )
        for d in devices:
            if d.extension_id is not None:
                by_ext.setdefault(d.extension_id, []).append(_device_summary(d))
    return [
        ExtensionRead(
            id=e.id,
            number=e.number,
            display_name=e.display_name,
            enabled=e.enabled,
            calling_permission=e.calling_permission,
            devices=by_ext.get(e.id, []),
        )
        for e in exts
    ]


async def _to_read(session: AsyncSession, ext: Extension) -> ExtensionRead:
    return (await _to_reads(session, [ext]))[0]


@router.post("", response_model=ExtensionRead, status_code=status.HTTP_201_CREATED)
async def create_extension(
    body: ExtensionCreate,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
    box: SecretBox = Depends(get_secret_box),
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
    _, sip_password_encrypted = new_extension_password(box)
    ext = Extension(
        number=body.number,
        display_name=body.display_name,
        sip_password_encrypted=sip_password_encrypted,
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
    return await _to_read(session, ext)


@router.get("", response_model=list[ExtensionRead])
async def list_extensions(session: AsyncSession = Depends(get_session)) -> list[ExtensionRead]:
    """内線一覧（紐づく端末の要約付き）。"""
    result = await session.scalars(select(Extension).order_by(Extension.number))
    return await _to_reads(session, list(result))


@router.get("/{ext_id}", response_model=ExtensionRead)
async def get_extension(ext_id: int, session: AsyncSession = Depends(get_session)) -> ExtensionRead:
    ext = await session.get(Extension, ext_id)
    if ext is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return await _to_read(session, ext)


@router.get("/{ext_id}/credentials", response_model=ExtensionCredentials)
async def get_extension_credentials(
    ext_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
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
    # 復号できなければ SipCredentialError（503）。監査記録より前に確定させ、
    # 閲覧できなかった操作を「閲覧した」と記録しない。
    password = decrypt_extension_password(box, ext)

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
        password=password,
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
) -> ExtensionRead:
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
    return await _to_read(session, ext)


@router.delete("/{ext_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_extension(
    ext_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> None:
    """内線を削除する。紐づく端末は割当を解除してから削除する（issue #149）。

    devices.extension_id の FK は ON DELETE SET NULL だが、それだけでは端末が
    provisioned=True のまま残るため、アプリ側で明示的に解除（provisioned=False・
    取得記録リセット）し、監査ログに残す。
    """
    ext = await session.get(Extension, ext_id)
    if ext is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    ip = get_client_ip(request)
    released = await devices_bound_to(session, ext.id)
    for device in released:
        unbind_device(device)
        await record_audit(
            session,
            actor_user_id=admin.id,
            actor_label=admin.username,
            action="device.extension.unassign",
            target_type="device",
            target_id=str(device.id),
            detail={
                "mac_address": device.mac_address,
                "extension_number": ext.number,
                "reason": "extension_deleted",
            },
            ip_address=ip,
        )
    await record_audit(
        session,
        actor_user_id=admin.id,
        actor_label=admin.username,
        action="extension.delete",
        target_type="extension",
        target_id=str(ext.id),
        detail={
            "number": ext.number,
            "released_device_mac_addresses": [d.mac_address for d in released],
        },
        ip_address=ip,
    )
    # 解除を先に確定させてから削除する（SQLite で FK が無効でも端末側に古い ID を残さない）。
    await session.flush()
    await session.delete(ext)
    await session.commit()
    await listener.notify(session)
