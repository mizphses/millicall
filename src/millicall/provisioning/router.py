"""プロビジョニング HTTP エンドポイント群。

プレフィックス /provisioning。Cookie 認証なし（IP電話はクッキーを扱えない）。
LAN 内 IP からのアクセスのみ許可し、エンドポイントの存在を隠すため 404 を返す。
デバイス固有エンドポイントはワンタイムトークンゲートも備える。
"""

import ipaddress
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.config import Settings
from millicall.deps import get_session, get_settings_dep
from millicall.models import Contact, Device, Extension, NetworkConfig
from millicall.network.validation import normalize_mac
from millicall.provisioning.templates import (
    render_panasonic_common,
    render_panasonic_config,
    render_panasonic_model_file,
    render_panasonic_phonebook,
    render_yealink_boot,
    render_yealink_common,
    render_yealink_config,
    render_yealink_phonebook,
)

logger = logging.getLogger("millicall.provisioning.router")

# Panasonic のプレプロビジョニング入口ファイル名（{MODEL}.cfg）を判定する正規表現。
# KX-HDV130N / KX-HDV230N / KX-HDV330N / KX-HDV430N などの N サフィックス、
# および NB サフィックス（KX-HDV130NB 等）を含め「KX-HDV で始まり英数字・ハイフンが続く」
# ものを広く受理する。Yealink の {mac}.cfg（12桁hex）とは決して衝突しない。
_PANASONIC_MODEL_RE = re.compile(r"^KX-HDV[A-Za-z0-9-]+$")

router = APIRouter(prefix="/provisioning", tags=["provisioning"])


async def _require_lan(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> NetworkConfig:
    """クライアント IP が LAN CIDR 内にあることを検証する。

    プロキシなし（LAN インタフェースに直接 bind）のため request.client.host を信頼する。
    LAN 外や設定不備の場合は 404 を返す（エンドポイント存在を明かさないため 403 ではない）。

    Args:
        request: FastAPI リクエストオブジェクト。
        session: SQLAlchemy 非同期セッション。

    Returns:
        NetworkConfig オブジェクト（id=1）。

    Raises:
        HTTPException(404): LAN 外からのアクセス、設定なし、または IP 解析失敗の場合。
    """
    nc = await session.get(NetworkConfig, 1)
    if nc is None:
        raise HTTPException(status_code=404)
    # request.client が None（一部 ASGI 経路）なら送信元不明として fail-closed で拒否。
    if request.client is None:
        raise HTTPException(status_code=404)
    try:
        network = ipaddress.IPv4Network(f"{nc.lan_ip}/{nc.lan_prefix}", strict=False)
        client_ip = ipaddress.IPv4Address(request.client.host)
    except ValueError:
        raise HTTPException(status_code=404) from None
    if client_ip not in network:
        raise HTTPException(status_code=404)
    return nc


async def _get_provisioned_device(
    mac_normalized: str,
    token: str | None,
    session: AsyncSession,
) -> Device:
    """プロビジョニング済みデバイスを検索し、トークンゲートを適用する。

    1. MAC で Device 行を検索 → 見つからなければ 404
    2. provisioned=False または extension_id=None → 404
    3. provision_token が設定されている場合:
       - token パラメータなし、または不一致 → 404
       - 一致 → provision_token=None（単一使用消費）してコミット
    4. provision_token がない場合 → LAN + known-device のみで通過

    Args:
        mac_normalized: 正規化済みの MAC アドレス文字列。
        token: URL クエリパラメータ ?token= の値。
        session: SQLAlchemy 非同期セッション。

    Returns:
        プロビジョニング済みの Device オブジェクト。

    Raises:
        HTTPException(404): デバイス未発見、未プロビジョニング、またはトークン不一致。
    """
    device = await session.scalar(select(Device).where(Device.mac_address == mac_normalized))
    if device is None:
        raise HTTPException(status_code=404)
    if not device.provisioned or device.extension_id is None:
        raise HTTPException(status_code=404)

    if device.provision_token is not None:
        # トークンゲート: 提供されたトークンが一致するか検証
        if token is None or token != device.provision_token:
            raise HTTPException(status_code=404)
        # トークン消費（単一使用）: コミット前に None に設定
        device.provision_token = None
        await session.commit()
        await session.refresh(device)

    return device


# ---------------------------------------------------------------------------
# Panasonic エンドポイント
# ---------------------------------------------------------------------------


@router.get("/Panasonic/ConfigCommon.cfg", response_class=PlainTextResponse)
async def panasonic_common_config(
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """Panasonic KX-HDV 共通設定ファイルを返す（LAN 制限のみ）。

    端末固有のクレデンシャルを含まない共通設定。
    """
    content = render_panasonic_common(network_config=nc, settings=settings)
    return PlainTextResponse(content, media_type="text/plain")


@router.get("/Panasonic/Config{mac}.cfg", response_class=PlainTextResponse)
async def panasonic_device_config(
    mac: str,
    token: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """Panasonic KX-HDV 端末固有設定ファイルを返す（LAN 制限 + known-device + トークンゲート）。

    SIP 認証情報を含むため、プロビジョニング済みデバイスのみアクセス可能。
    """
    # MAC 正規化
    try:
        mac_normalized = normalize_mac(mac)
    except ValueError:
        raise HTTPException(status_code=404) from None

    device = await _get_provisioned_device(mac_normalized, token, session)

    ext = await session.get(Extension, device.extension_id)
    if ext is None:
        raise HTTPException(status_code=404)

    content = render_panasonic_config(extension=ext, network_config=nc, settings=settings)
    return PlainTextResponse(content, media_type="text/plain")


# ---------------------------------------------------------------------------
# Yealink エンドポイント
# ---------------------------------------------------------------------------


@router.get("/Yealink/y000000000000.boot", response_class=PlainTextResponse)
async def yealink_boot(
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """Yealink 自動プロビジョニング起動ファイルを返す（LAN 制限のみ）。"""
    content = render_yealink_boot(network_config=nc, settings=settings)
    return PlainTextResponse(content, media_type="text/plain")


@router.get("/Yealink/common.cfg", response_class=PlainTextResponse)
async def yealink_common_config(
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """Yealink 共通設定ファイルを返す（LAN 制限のみ）。"""
    content = render_yealink_common(network_config=nc, settings=settings)
    return PlainTextResponse(content, media_type="text/plain")


@router.get("/Yealink/{mac}.cfg", response_class=PlainTextResponse)
async def yealink_device_config(
    mac: str,
    token: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """Yealink 端末固有設定ファイルを返す（LAN 制限 + known-device + トークンゲート）。

    SIP 認証情報を含むため、プロビジョニング済みデバイスのみアクセス可能。
    """
    # MAC 正規化
    try:
        mac_normalized = normalize_mac(mac)
    except ValueError:
        raise HTTPException(status_code=404) from None

    device = await _get_provisioned_device(mac_normalized, token, session)

    ext = await session.get(Extension, device.extension_id)
    if ext is None:
        raise HTTPException(status_code=404)

    content = render_yealink_config(extension=ext, network_config=nc, settings=settings)
    return PlainTextResponse(content, media_type="text/plain")


# ---------------------------------------------------------------------------
# option 66 直下エントリファイル（/provisioning/ 直下）
#
# DHCP option 66 のプロビジョニング URL は http://<lan_ip>/provisioning/ であり、
# 電話機はこの base URL に**ファイル名を直接付けて**取得しにくる。
# 既存のベンダーサブディレクトリ配下ルート（/Yealink/・/Panasonic/）は
# boot の絶対 URL include（/Yealink/common.cfg, /Yealink/$MAC.cfg）が参照するため
# 互換のため残し、ここでは直下エントリファイルを既存ハンドラへ委譲する。
#
# 固定ルート（ConfigCommon.cfg, y000000000000.boot）をパスパラメータ付きルート
# （Config{mac}.cfg, {mac}.boot, {mac}.cfg）より先に宣言し、ルート衝突を防ぐ。
# ---------------------------------------------------------------------------


@router.get("/ConfigCommon.cfg", response_class=PlainTextResponse)
async def root_panasonic_common_config(
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """option 66 直下の Panasonic 共通設定を返す（既存 /Panasonic/ConfigCommon.cfg に相当）。

    Panasonic KX-HDV は option 66 直下に ConfigCommon.cfg を直接要求する。
    セキュリティ・レンダリングは既存ハンドラと同一（LAN 制限のみ）。
    """
    return await panasonic_common_config(nc=nc, settings=settings)


@router.get("/y000000000000.boot", response_class=PlainTextResponse)
async def root_yealink_common_boot(
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """option 66 直下の Yealink 共通 boot ファイルを返す（既存 /Yealink/y000000000000.boot に相当）。

    boot 内の絶対 URL include（/Yealink/common.cfg, /Yealink/$MAC.cfg）経由で
    残りの設定は既存の /Yealink/ ルートに到達する。セキュリティは LAN 制限のみ。
    """
    return await yealink_boot(nc=nc, settings=settings)


@router.get("/Config{mac}.cfg", response_class=PlainTextResponse)
async def root_panasonic_device_config(
    mac: str,
    token: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """option 66 直下の Panasonic 端末固有設定を返す（既存 /Panasonic/Config{mac}.cfg に相当）。

    SIP 認証情報を含むため、LAN 制限 + known-device + トークンゲートを維持する。
    """
    return await panasonic_device_config(
        mac=mac, token=token, session=session, nc=nc, settings=settings
    )


@router.get("/{mac}.boot", response_class=PlainTextResponse)
async def root_yealink_mac_boot(
    mac: str,
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """option 66 直下の Yealink MAC 別 boot ファイルを返す（共通 boot と同一内容）。

    Yealink は起動時に <MAC>.boot を先に要求し、無ければ共通 boot にフォールバックする。
    boot 内容は $MAC 展開されるため共通 boot と同一で良い。
    不正な MAC 形式は 404 を返す（固定ルートを食わないよう検証）。
    セキュリティは LAN 制限のみ（共通 boot と同様）。
    """
    # MAC 正規化: 不正な形式は 404（存在を明かさない）
    try:
        normalize_mac(mac)
    except ValueError:
        raise HTTPException(status_code=404) from None
    return await yealink_boot(nc=nc, settings=settings)


@router.get("/{name}.cfg", response_class=PlainTextResponse)
async def root_cfg_dispatcher(
    name: str,
    token: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """option 66 直下の ``{name}.cfg`` を単一ディスパッチャで振り分ける。

    ``ConfigCommon.cfg`` / ``Config{mac}.cfg`` は上位の固定・具体ルートで処理済みのため
    ここには到達しない。この直下 ``{name}.cfg`` に来るのは主に次の 2 系統:

    - Panasonic プレプロビジョニング入口ファイル ``{MODEL}.cfg``
      （KX-HDV130N / KX-HDV230N / KX-HDV330N / KX-HDV430N とその NB サフィックス等）。
      SIP 認証情報を含まない最小の入口ファイルのため、LAN 制限のみでトークン不要。
    - Yealink 端末固有設定 ``{mac}.cfg``（12桁hex）。SIP 認証情報を含むため
      LAN 制限 + known-device + トークンゲートを維持する。

    ``{model}.cfg`` と ``{mac}.cfg`` は同じ ``X.cfg`` にマッチし FastAPI では
    ルート衝突するため、ファイル名パターンで確実に分岐する（KX-HDV* → 入口ファイル、
    それ以外 → 従来どおり MAC 検証を経て Yealink 端末設定）。
    """
    # Panasonic プレプロビジョニング入口ファイル（{MODEL}.cfg）
    if _PANASONIC_MODEL_RE.match(name):
        content = render_panasonic_model_file(network_config=nc, settings=settings)
        return PlainTextResponse(content, media_type="text/plain")

    # それ以外は従来どおり Yealink 端末固有設定として MAC 検証を経て提供する。
    # Yealink は boot 経由（絶対 URL include）で /Yealink/{mac}.cfg を取得するため
    # 直下 {mac}.cfg が無くても無害だが、堅牢性のため直下でも同一設定を提供する。
    # SIP 認証情報を含むため、LAN 制限 + known-device + トークンゲートを維持する。
    return await yealink_device_config(
        mac=name, token=token, session=session, nc=nc, settings=settings
    )


# ---------------------------------------------------------------------------
# 電話帳エンドポイント
# ---------------------------------------------------------------------------


@router.get("/phonebook/panasonic.xml")
async def panasonic_phonebook(
    session: AsyncSession = Depends(get_session),
    _nc: NetworkConfig = Depends(_require_lan),
) -> Response:
    """Panasonic XML 電話帳を返す（LAN 制限のみ）。"""
    contacts = list(await session.scalars(select(Contact).order_by(Contact.name)))
    xml_bytes = render_panasonic_phonebook(contacts)
    return Response(content=xml_bytes, media_type="application/xml")


@router.get("/phonebook/yealink.xml")
async def yealink_phonebook(
    session: AsyncSession = Depends(get_session),
    _nc: NetworkConfig = Depends(_require_lan),
) -> Response:
    """Yealink XML 電話帳を返す（LAN 制限のみ）。"""
    contacts = list(await session.scalars(select(Contact).order_by(Contact.name)))
    xml_bytes = render_yealink_phonebook(contacts)
    return Response(content=xml_bytes, media_type="application/xml")
