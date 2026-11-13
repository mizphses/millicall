"""デバイス管理 API エンドポイント群。

プレフィックス /api/devices。require_admin 依存（管理者のみ）。
デバイス一覧取得・DHCP リース同期・クイックプロビジョニング・デバイス削除に加え、
配信ゲート（provision_mode）の切替、厳格モードのトークン発行・破棄、
SIP 登録状態の取得、電話機への再取得指示（resync）を提供する（issue #85）。
"""

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.app_settings.service import effective_settings
from millicall.audit import get_client_ip, record_audit
from millicall.deps import (
    get_change_listener,
    get_esl_factory,
    get_netd_client,
    get_session,
    get_settings_dep,
    require_admin,
)
from millicall.models import Device, Extension, NetworkConfig, User
from millicall.network.client import NetdClient, NetdError
from millicall.provisioning.service import (
    PROVISION_MODE_STANDARD,
    PROVISION_MODE_STRICT,
    ResyncResult,
    chunked,
    device_is_registered,
    fetch_sip_registrations,
    generate_provision_token,
    hash_provision_token,
    quick_provision,
    reset_config_fetch,
    resync_phone,
    sync_devices_from_leases,
)
from millicall.provisioning.templates import provisioning_root_url
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

ProvisionMode = Literal["standard", "strict"]


class DeviceRead(BaseModel):
    """デバイス情報レスポンスモデル。provision_token（ハッシュ含む）は絶対に含めない。"""

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
    # 配信ゲート（issue #85）
    provision_mode: ProvisionMode
    # 厳格モードのトークンが発行済みか（値そのものは返さない）
    token_issued: bool
    # 端末固有 cfg の最終取得記録（取得確認用）
    last_config_fetch_at: datetime | None
    last_config_fetch_user_agent: str | None
    last_config_fetch_used_token: bool

    model_config = {"from_attributes": True}


class QuickProvisionBody(BaseModel):
    """クイックプロビジョニングリクエストボディ。"""

    extension_number: str
    display_name: str


class DeviceUpdate(BaseModel):
    """デバイス更新リクエストボディ（指定した項目のみ更新）。"""

    provision_mode: ProvisionMode | None = None


class ProvisionTokenIssued(BaseModel):
    """厳格モードのトークン発行結果。トークンはこの応答でしか返さない（DB はハッシュのみ）。

    電話機の Web 画面へ手入力する URL を機種別に返す。
    """

    token: str
    # Yealink: Auto Provision の Server URL に入力する（末尾 / 付き）
    yealink_server_url: str
    # Panasonic: Provisioning の Standard File URL / Master File URL に入力する
    panasonic_standard_file_url: str
    panasonic_master_file_url: str


class DeviceRegistrations(BaseModel):
    """SIP 登録状態。available=False は FreeSWITCH から取得できなかった（未確認）。"""

    available: bool
    registered_device_ids: list[int]


class ResyncAttemptRead(BaseModel):
    """再取得指示の 1 手段の結果。ok は「指示が受理された」ことのみを表す。"""

    method: str
    ok: bool
    detail: str
    verified: bool
    skipped: bool


class ResyncResultRead(BaseModel):
    """POST /api/devices/{id}/resync のレスポンス。

    requested=True でも電話機が設定を取りに来たとは限らない。取得したかは
    DeviceRead.last_config_fetch_at で確認する。
    """

    requested: bool
    attempts: list[ResyncAttemptRead]


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------


def _to_device_read(device: Device, ext: Extension | None) -> DeviceRead:
    """Device と（あれば）紐付く Extension から DeviceRead を組み立てる。"""
    return DeviceRead(
        id=device.id,
        mac_address=device.mac_address,
        ip_address=device.ip_address,
        hostname=device.hostname,
        model=device.model,
        extension_id=device.extension_id,
        extension_number=ext.number if ext is not None else None,
        extension_display_name=ext.display_name if ext is not None else None,
        provisioned=device.provisioned,
        last_seen=device.last_seen,
        active=device.active,
        created_at=device.created_at,
        provision_mode=device.provision_mode,  # type: ignore[arg-type]
        token_issued=device.provision_token is not None,
        last_config_fetch_at=device.last_config_fetch_at,
        last_config_fetch_user_agent=device.last_config_fetch_user_agent,
        last_config_fetch_used_token=device.last_config_fetch_used_token,
    )


async def _load_extensions(devices: list[Device], session: AsyncSession) -> dict[int, Extension]:
    """devices に紐付く Extension を ID 一覧でまとめて取得する（N+1 回避・チャンク分割）。"""
    ext_ids = list(dict.fromkeys(d.extension_id for d in devices if d.extension_id is not None))
    ext_by_id: dict[int, Extension] = {}
    for chunk in chunked(ext_ids):
        for ext in await session.scalars(select(Extension).where(Extension.id.in_(chunk))):
            ext_by_id[ext.id] = ext
    return ext_by_id


async def _build_device_reads(devices: list[Device], session: AsyncSession) -> list[DeviceRead]:
    """Device のリストから DeviceRead のリストを構築する（入力順・重複を保つ）。

    紐付く Extension は端末ごとではなく ID 一覧でまとめて取得する（N+1 回避）。
    IN 句はバインド変数上限対策でチャンク分割する。

    Args:
        devices: Device ORM オブジェクトのリスト。
        session: SQLAlchemy 非同期セッション（Extension 取得に使用）。

    Returns:
        devices と同じ順序の DeviceRead リスト。
    """
    ext_by_id = await _load_extensions(devices, session)
    return [
        _to_device_read(d, ext_by_id.get(d.extension_id) if d.extension_id is not None else None)
        for d in devices
    ]


async def _build_device_read(device: Device, session: AsyncSession) -> DeviceRead:
    """単一 Device ORM オブジェクトから DeviceRead を構築する。"""
    return (await _build_device_reads([device], session))[0]


async def _get_device_or_404(session: AsyncSession, device_id: int) -> Device:
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return device


def _to_resync_read(result: ResyncResult) -> ResyncResultRead:
    return ResyncResultRead(
        requested=result.requested,
        attempts=[
            ResyncAttemptRead(
                method=a.method,
                ok=a.ok,
                detail=a.detail,
                verified=a.verified,
                skipped=a.skipped,
            )
            for a in result.attempts
        ],
    )


async def _resync(request: Request, device: Device, esl_factory) -> ResyncResult:
    """実効設定の電話機管理者資格情報で resync_phone を呼ぶ（例外は飲み込む）。"""
    # 管理者資格情報は実効設定由来（管理画面/DB で上書き可能。パスワードは暗号化保存され、
    # ここで復号済み値を使う）。
    settings = await effective_settings(request.app.state)
    try:
        return await resync_phone(
            device,
            admin_username=settings.phone_admin_username,
            admin_password=settings.phone_admin_password,
            esl_factory=esl_factory,
        )
    except Exception:  # noqa: BLE001
        logger.warning("resync_phone 失敗 device_id=%s", device.id, exc_info=True)
        return ResyncResult()


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("", response_model=list[DeviceRead])
async def list_devices(
    session: AsyncSession = Depends(get_session),
) -> list[DeviceRead]:
    """デバイス一覧を返す（Extension 情報付き。Extension は一括取得）。"""
    devices = list(await session.scalars(select(Device).order_by(Device.id)))
    return await _build_device_reads(devices, session)


@router.get("/registrations", response_model=DeviceRegistrations)
async def device_registrations(
    session: AsyncSession = Depends(get_session),
    esl_factory=Depends(get_esl_factory),
) -> DeviceRegistrations:
    """FreeSWITCH の SIP 登録状態から「端末の IP から内線が登録されている」端末を返す。

    FreeSWITCH へ到達できない場合も 200 で available=False を返す（UI は「未確認」と表示し、
    登録済みと偽らない）。登録一覧は 1 回の ESL 問い合わせでまとめて取得する。
    """
    regs = await fetch_sip_registrations(esl_factory)
    if regs is None:
        return DeviceRegistrations(available=False, registered_device_ids=[])
    devices = list(
        await session.scalars(
            select(Device).where(Device.extension_id.is_not(None)).order_by(Device.id)
        )
    )
    ext_by_id = await _load_extensions(devices, session)
    registered = [
        d.id
        for d in devices
        if device_is_registered(
            d,
            ext_by_id[d.extension_id].number if d.extension_id in ext_by_id else None,
            regs,
        )
    ]
    return DeviceRegistrations(available=True, registered_device_ids=registered)


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
    return await _build_device_reads(devices, session)


@router.post("/{device_id}/quick-provision", response_model=DeviceRead)
async def quick_provision_endpoint(
    device_id: int,
    body: QuickProvisionBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
    esl_factory=Depends(get_esl_factory),
) -> DeviceRead:
    """デバイスに内線を割り当て、プロビジョニング完了状態にする。

    トークンは発行しない（既定は standard ゲート。厳格モードは管理者が個別に選ぶ）。
    内線割り当て後、best-effort で電話機へ再取得を指示する（結果は応答に含めない。
    取得できたかは一覧の取得記録で確認する）。
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

    await _resync(request, device, esl_factory)
    return await _build_device_read(device, session)


@router.patch("/{device_id}", response_model=DeviceRead)
async def update_device(
    device_id: int,
    body: DeviceUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DeviceRead:
    """デバイスの配信ゲート（provision_mode）を切り替える。

    standard へ戻すとトークンは破棄する（トークン付き URL は以後 404。電話機側の URL も
    通常 URL に戻す必要がある）。strict にしてもトークンは自動発行しない
    （POST /{id}/token で明示的に発行する。発行までは端末固有 cfg は 404）。
    """
    device = await _get_device_or_404(session, device_id)
    if body.provision_mode is not None and body.provision_mode != device.provision_mode:
        previous = device.provision_mode
        device.provision_mode = body.provision_mode
        token_discarded = False
        if body.provision_mode == PROVISION_MODE_STANDARD and device.provision_token is not None:
            device.provision_token = None
            token_discarded = True
        await record_audit(
            session,
            actor_user_id=admin.id,
            actor_label=admin.username,
            action="device.provision_mode.update",
            target_type="device",
            target_id=str(device.id),
            detail={
                "mac_address": device.mac_address,
                "from": previous,
                "to": body.provision_mode,
                "token_discarded": token_discarded,
            },
            ip_address=get_client_ip(request),
        )
        await session.commit()
    return await _build_device_read(device, session)


@router.post("/{device_id}/token", response_model=ProvisionTokenIssued)
async def issue_provision_token(
    device_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings=Depends(get_settings_dep),
) -> ProvisionTokenIssued:
    """厳格モードのトークンを（再）発行し、トークン付き URL を一度だけ返す。

    既存のトークンは無効になる（電話機に入力済みの古い URL は 404 になる）。
    DB にはハッシュのみを保存し、監査ログにもトークンは記録しない。
    取得記録はリセットする（新しいトークンでの取得を待つ）。
    """
    device = await _get_device_or_404(session, device_id)
    if device.provision_mode != PROVISION_MODE_STRICT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="トークンは厳格モードの端末にのみ発行できます",
        )
    nc = await session.get(NetworkConfig, 1)
    if nc is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ネットワーク設定が未作成のためプロビジョニング URL を作れません",
        )

    rotated = device.provision_token is not None
    token = generate_provision_token()
    device.provision_token = hash_provision_token(token)
    reset_config_fetch(device)
    await record_audit(
        session,
        actor_user_id=admin.id,
        actor_label=admin.username,
        action="device.provision_token.issue",
        target_type="device",
        target_id=str(device.id),
        detail={"mac_address": device.mac_address, "rotated": rotated},
        ip_address=get_client_ip(request),
    )
    await session.commit()

    root = provisioning_root_url(nc, settings, token)
    return ProvisionTokenIssued(
        token=token,
        yealink_server_url=f"{root}/",
        panasonic_standard_file_url=f"{root}/Panasonic/Config{{MAC}}.cfg",
        panasonic_master_file_url=f"{root}/Panasonic/ConfigCommon.cfg",
    )


@router.delete("/{device_id}/token", response_model=DeviceRead)
async def revoke_provision_token(
    device_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DeviceRead:
    """厳格モードのトークンを破棄する。厳格モードのままなら端末固有 cfg は 404 になる。"""
    device = await _get_device_or_404(session, device_id)
    if device.provision_token is not None:
        device.provision_token = None
        await record_audit(
            session,
            actor_user_id=admin.id,
            actor_label=admin.username,
            action="device.provision_token.revoke",
            target_type="device",
            target_id=str(device.id),
            detail={"mac_address": device.mac_address},
            ip_address=get_client_ip(request),
        )
        await session.commit()
    return await _build_device_read(device, session)


@router.post("/{device_id}/resync", response_model=ResyncResultRead)
async def resync_device(
    device_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    esl_factory=Depends(get_esl_factory),
) -> ResyncResultRead:
    """電話機へ設定の再取得を指示する（SIP NOTIFY check-sync と HTTP）。

    これは指示のみで、電話機が取りに来たかは一覧の取得記録で確認する。
    指示できなかった場合も 200 で requested=False を返す（各手段の結果を含む）。
    """
    device = await _get_device_or_404(session, device_id)
    result = await _resync(request, device, esl_factory)
    return _to_resync_read(result)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """デバイスを削除する。"""
    device = await _get_device_or_404(session, device_id)
    await session.delete(device)
    await session.commit()
