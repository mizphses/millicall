"""デバイス管理 API エンドポイント群。

プレフィックス /api/devices。require_admin 依存（管理者のみ）。
デバイス一覧取得・DHCP リース同期・クイックプロビジョニング・デバイス削除を提供する。
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.config import get_settings
from millicall.deps import get_change_listener, get_netd_client, get_session, require_admin
from millicall.models import Device, Extension
from millicall.network.client import NetdClient, NetdError
from millicall.provisioning.service import quick_provision, resync_phone, sync_devices_from_leases
from millicall.telephony.hooks import ExtensionChangeListener

logger = logging.getLogger("millicall.provisioning.devices_router")

router = APIRouter(
    prefix="/api/devices",
    tags=["devices"],
    dependencies=[Depends(require_admin)],
)


# ---------------------------------------------------------------------------
# Pydantic スキーマ
# ---------------------------------------------------------------------------


class DeviceRead(BaseModel):
    """デバイス情報レスポンスモデル。provision_token は絶対に含めない。"""

    id: int
    mac_address: str
    ip_address: str | None
    hostname: str | None
    model: str | None
    extension_id: int | None
    extension_number: str | None
    extension_display_name: str | None
    provisioned: bool
    last_seen: datetime | None
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class QuickProvisionBody(BaseModel):
    """クイックプロビジョニングリクエストボディ。"""

    extension_number: str
    display_name: str


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------


async def _build_device_read(device: Device, session: AsyncSession) -> DeviceRead:
    """Device ORM オブジェクトから DeviceRead を構築する。

    extension_id がある場合は Extension を取得して number / display_name を埋める。

    Args:
        device: Device ORM オブジェクト。
        session: SQLAlchemy 非同期セッション（Extension 取得に使用）。

    Returns:
        DeviceRead インスタンス。
    """
    extension_number: str | None = None
    extension_display_name: str | None = None
    if device.extension_id is not None:
        ext = await session.get(Extension, device.extension_id)
        if ext is not None:
            extension_number = ext.number
            extension_display_name = ext.display_name

    return DeviceRead(
        id=device.id,
        mac_address=device.mac_address,
        ip_address=device.ip_address,
        hostname=device.hostname,
        model=device.model,
        extension_id=device.extension_id,
        extension_number=extension_number,
        extension_display_name=extension_display_name,
        provisioned=device.provisioned,
        last_seen=device.last_seen,
        active=device.active,
        created_at=device.created_at,
    )


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("", response_model=list[DeviceRead])
async def list_devices(
    session: AsyncSession = Depends(get_session),
) -> list[DeviceRead]:
    """デバイス一覧を返す（Extension 情報付き）。

    デバイス数は少ないため N+1 で Extension を取得しても問題ない。
    """
    devices = list(await session.scalars(select(Device).order_by(Device.id)))
    return [await _build_device_read(d, session) for d in devices]


@router.post("/sync", response_model=list[DeviceRead])
async def sync_devices(
    session: AsyncSession = Depends(get_session),
    netd_client: NetdClient = Depends(get_netd_client),
) -> list[DeviceRead]:
    """DHCP リースからデバイス行を upsert して結果を返す。

    netd が利用できない場合は 502 を返す。
    """
    try:
        devices = await sync_devices_from_leases(session, netd_client)
    except NetdError as exc:
        logger.error("sync_devices: netd エラー: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"netd 通信エラー: {exc}",
        ) from exc
    return [await _build_device_read(d, session) for d in devices]


@router.post("/{device_id}/quick-provision", response_model=DeviceRead)
async def quick_provision_endpoint(
    device_id: int,
    body: QuickProvisionBody,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> DeviceRead:
    """デバイスに内線を割り当て、プロビジョニング完了状態にする。

    内線割り当て後、best-effort で電話機への HTTP resync 要求も送る。
    """
    try:
        device = await quick_provision(
            session=session,
            device_id=device_id,
            extension_number=body.extension_number,
            display_name=body.display_name,
            telephony_notify=listener.notify,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    # best-effort resync（失敗しても 200 を返す）。管理者資格情報は Settings 由来
    # （env で上書き可能。コードに定数を持たない）。
    settings = get_settings()
    try:
        await resync_phone(
            device,
            admin_username=settings.phone_admin_username,
            admin_password=settings.phone_admin_password,
        )
    except Exception:  # noqa: BLE001
        logger.warning("quick_provision: resync_phone 失敗 device_id=%s", device_id)

    return await _build_device_read(device, session)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """デバイスを削除する。"""
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await session.delete(device)
    await session.commit()
