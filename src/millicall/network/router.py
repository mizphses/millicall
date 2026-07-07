"""ネットワーク設定 API（Phase 5 Task 4）。

単一行テーブル network_config（id=1）を読み書きする CRUD と、
netd への apply / tailscale 操作エンドポイントを提供する。

セキュリティルール:
  - tailscale_auth_key_encrypted は**一切レスポンスに含めない**。
    代わりに tailscale_auth_key_set (bool) のみ返す。
  - apply は PUT とは分離した POST /apply で明示的にのみ実行する。
  - NetdError は 502 に変換し、秘密情報を含まないメッセージのみ返す。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.crypto import SecretBox
from millicall.deps import get_netd_client, get_secret_box, get_session, require_admin
from millicall.models import NetworkConfig
from millicall.network.client import NetdClient, NetdError
from millicall.network.validation import (
    is_valid_interface,
    is_valid_tailscale_authkey,
    validate_cidr_prefix,
    validate_ipv4,
    validate_ipv4_range,
)

router = APIRouter(
    prefix="/api/network",
    tags=["network"],
    dependencies=[Depends(require_admin)],
)

# ---------------------------------------------------------------------------
# Pydantic スキーマ
# ---------------------------------------------------------------------------


class NetworkConfigRead(BaseModel):
    """GET /api/network / PUT /api/network のレスポンス。

    tailscale_auth_key_encrypted はこのスキーマに含めない。
    代わりに tailscale_auth_key_set (bool) でキー設定有無のみ伝える。
    """

    id: int
    lan_interface: str
    lan_ip: str
    lan_prefix: int
    dhcp_range_start: str
    dhcp_range_end: str
    dhcp_lease_hours: int
    provisioning_base_url: str
    nat_enabled: bool
    wan_interface: str
    tailscale_enabled: bool
    tailscale_auth_key_set: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NetworkConfigUpdate(BaseModel):
    """PUT /api/network のリクエストボディ。

    tailscale_auth_key の扱い:
      - None / 未指定: 既存のキーを変更しない。
      - 空文字列 "": 既存のキーを削除する（クリア）。
      - 非空文字列: 検証後に SecretBox 暗号化して保存する。
    """

    lan_interface: str = Field(default="enp3s0")
    lan_ip: str = Field(default="172.20.0.1")
    lan_prefix: int = Field(default=16, ge=0, le=32)
    dhcp_range_start: str = Field(default="172.20.1.1")
    dhcp_range_end: str = Field(default="172.20.254.254")
    dhcp_lease_hours: int = Field(default=12, ge=1, le=720)
    provisioning_base_url: str = Field(default="")
    nat_enabled: bool = Field(default=True)
    wan_interface: str = Field(default="")
    tailscale_enabled: bool = Field(default=False)
    tailscale_auth_key: str | None = Field(default=None)


class ApplyResult(BaseModel):
    ok: bool


class TailscaleStatusResult(BaseModel):
    connected: bool
    error: str | None = None
    detail: dict | None = None


# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _to_read(cfg: NetworkConfig) -> NetworkConfigRead:
    """NetworkConfig ORM モデルを NetworkConfigRead Pydantic モデルへ変換する。

    tailscale_auth_key_encrypted は出力しない。
    """
    return NetworkConfigRead(
        id=cfg.id,
        lan_interface=cfg.lan_interface,
        lan_ip=cfg.lan_ip,
        lan_prefix=cfg.lan_prefix,
        dhcp_range_start=cfg.dhcp_range_start,
        dhcp_range_end=cfg.dhcp_range_end,
        dhcp_lease_hours=cfg.dhcp_lease_hours,
        provisioning_base_url=cfg.provisioning_base_url,
        nat_enabled=cfg.nat_enabled,
        wan_interface=cfg.wan_interface,
        tailscale_enabled=cfg.tailscale_enabled,
        tailscale_auth_key_set=bool(cfg.tailscale_auth_key_encrypted),
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


async def _get_or_create(session: AsyncSession) -> NetworkConfig:
    """id=1 の NetworkConfig 行を返す。存在しなければデフォルト値で作成する。"""
    cfg = await session.get(NetworkConfig, 1)
    if cfg is None:
        cfg = NetworkConfig(id=1)
        session.add(cfg)
        await session.flush()
    return cfg


def _validate_update(body: NetworkConfigUpdate) -> None:
    """body のフィールドを validation.py のヘルパで検証し、不正なら HTTPException(422) を送出する。"""
    try:
        if not is_valid_interface(body.lan_interface):
            raise HTTPException(
                status_code=422,
                detail=f"lan_interface の形式が無効です: {body.lan_interface!r}",
            )
        validate_ipv4(body.lan_ip)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        validate_cidr_prefix(body.lan_prefix)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        validate_ipv4_range(body.dhcp_range_start, body.dhcp_range_end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.wan_interface and not is_valid_interface(body.wan_interface):
        raise HTTPException(
            status_code=422,
            detail=f"wan_interface の形式が無効です: {body.wan_interface!r}",
        )

    if body.tailscale_auth_key and not is_valid_tailscale_authkey(body.tailscale_auth_key):
        raise HTTPException(
            status_code=422,
            detail="tailscale_auth_key の形式が無効です（tskey-... 形式が必要です）",
        )


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("", response_model=NetworkConfigRead)
async def get_network_config(
    session: AsyncSession = Depends(get_session),
) -> NetworkConfigRead:
    """現在のネットワーク設定を返す。id=1 行が存在しない場合はデフォルト値で作成する。

    tailscale_auth_key_encrypted はレスポンスに**含めない**。
    """
    cfg = await _get_or_create(session)
    await session.commit()
    await session.refresh(cfg)
    return _to_read(cfg)


@router.put("", response_model=NetworkConfigRead)
async def update_network_config(
    body: NetworkConfigUpdate,
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
) -> NetworkConfigRead:
    """ネットワーク設定を更新して保存する。netd への適用は行わない。

    tailscale_auth_key の扱い:
      - None / 未指定: 既存キーを変更しない
      - 空文字列 "": キーを削除（NULL に）
      - 非空文字列: 検証後に暗号化して保存
    """
    _validate_update(body)

    cfg = await _get_or_create(session)

    cfg.lan_interface = body.lan_interface
    cfg.lan_ip = body.lan_ip
    cfg.lan_prefix = body.lan_prefix
    cfg.dhcp_range_start = body.dhcp_range_start
    cfg.dhcp_range_end = body.dhcp_range_end
    cfg.dhcp_lease_hours = body.dhcp_lease_hours
    cfg.provisioning_base_url = body.provisioning_base_url
    cfg.nat_enabled = body.nat_enabled
    cfg.wan_interface = body.wan_interface
    cfg.tailscale_enabled = body.tailscale_enabled

    # tailscale_auth_key の処理: None → 変更なし、"" → クリア、非空 → 暗号化保存
    if body.tailscale_auth_key is None:
        pass  # 既存キーを保持
    elif body.tailscale_auth_key == "":
        cfg.tailscale_auth_key_encrypted = None
    else:
        cfg.tailscale_auth_key_encrypted = box.encrypt(body.tailscale_auth_key)

    await session.commit()
    await session.refresh(cfg)
    return _to_read(cfg)


@router.post("/apply", response_model=ApplyResult)
async def apply_network_config(
    session: AsyncSession = Depends(get_session),
    netd: NetdClient = Depends(get_netd_client),
) -> ApplyResult:
    """保存済み設定を netd 経由でホストへ適用する。

    apply_dhcp → apply_nat の順で実行する。
    NetdError は 502 Bad Gateway に変換する（秘密情報は含まない）。
    このエンドポイントを呼ばない限り設定はホストに反映されない（PUT は保存のみ）。
    """
    cfg = await _get_or_create(session)
    await session.commit()

    # provisioning_base_url が空の場合は lan_ip から構築する
    provisioning_url = cfg.provisioning_base_url
    if not provisioning_url:
        provisioning_url = f"http://{cfg.lan_ip}:8000/provisioning/"

    try:
        await netd.apply_dhcp(
            lan_interface=cfg.lan_interface,
            lan_ip=cfg.lan_ip,
            lan_prefix=cfg.lan_prefix,
            dhcp_range_start=cfg.dhcp_range_start,
            dhcp_range_end=cfg.dhcp_range_end,
            dhcp_lease_hours=cfg.dhcp_lease_hours,
            provisioning_url=provisioning_url,
        )
        await netd.apply_nat(
            enabled=cfg.nat_enabled,
            lan_ip=cfg.lan_ip,
            lan_prefix=cfg.lan_prefix,
            wan_interface=cfg.wan_interface,
        )
    except NetdError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"netd への適用に失敗しました: {exc}",
        ) from exc

    return ApplyResult(ok=True)


@router.get("/tailscale/status", response_model=TailscaleStatusResult)
async def tailscale_status(
    netd: NetdClient = Depends(get_netd_client),
) -> TailscaleStatusResult:
    """Tailscale VPN の現在ステータスを返す。

    NetdError 発生時も 200 で返し、connected=False と error メッセージを含む。
    これにより GUI は常に状態をレンダリングできる。
    """
    try:
        detail = await netd.tailscale_status()
        connected = bool(detail.get("BackendState") == "Running")
        return TailscaleStatusResult(connected=connected, detail=detail)
    except NetdError as exc:
        return TailscaleStatusResult(connected=False, error=str(exc))


@router.post("/tailscale/up", response_model=ApplyResult)
async def tailscale_up(
    session: AsyncSession = Depends(get_session),
    netd: NetdClient = Depends(get_netd_client),
    box: SecretBox = Depends(get_secret_box),
) -> ApplyResult:
    """Tailscale VPN を起動する。

    tailscale_enabled が True かつ認証キーが設定されている必要がある。
    キーはレスポンス・ログに一切出力しない。
    """
    cfg = await _get_or_create(session)
    await session.commit()

    if not cfg.tailscale_enabled or not cfg.tailscale_auth_key_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tailscale の auth key が未設定です。先にキーを登録してください。",
        )

    # 復号失敗（マスターキー更新・保存値破損等）は 400 で返す（500 化させない）。
    try:
        auth_key = box.decrypt(cfg.tailscale_auth_key_encrypted)
    except Exception as exc:  # noqa: BLE001 — InvalidToken 等をまとめて 400 化
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="保存された auth key を復号できません。キーを再登録してください。",
        ) from exc
    try:
        await netd.tailscale_up(auth_key=auth_key)
    except NetdError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"tailscale up に失敗しました: {exc}",
        ) from exc
    finally:
        # auth_key を変数に残さないよう明示的に削除
        del auth_key

    return ApplyResult(ok=True)


@router.post("/tailscale/down", response_model=ApplyResult)
async def tailscale_down(
    netd: NetdClient = Depends(get_netd_client),
) -> ApplyResult:
    """Tailscale VPN を停止する。"""
    try:
        await netd.tailscale_down()
    except NetdError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"tailscale down に失敗しました: {exc}",
        ) from exc
    return ApplyResult(ok=True)
