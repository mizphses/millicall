"""プロビジョニング HTTP エンドポイント群。

プレフィックス /provisioning。Cookie 認証なし（IP電話はクッキーを扱えない）。
LAN 内 IP からのアクセスのみ許可し、エンドポイントの存在を隠すため 404 を返す。

端末固有 cfg（SIP 認証情報を含む）の配信ゲート（issue #85）:
  * 全モード共通: LAN 内 + 登録済み端末（provisioned/active/内線割当済み）+ 送信元 IP が
    端末の IP と一致 + 内線が enabled。
  * provision_mode="standard"（既定）: 上記のみ。通常 URL で配信する。
  * provision_mode="strict": 加えてパス形式のトークン ``/provisioning/t/<token>/...`` が
    必須。トークンは管理者が明示発行し、電話機の Web 画面へ手入力する（手動配送）。
  * トークン付き URL で来た場合は、モードに関わらずトークンが一致しなければ 404。
    不一致・欠落・未発行はすべて 404（存在を明かさない fail-closed）。
トークンはクエリでは受け付けない（アクセスログに残るため）。パス形式のトークンも
uvicorn のアクセスログでは伏字にする（``ProvisioningTokenRedactFilter``）。
"""

import ipaddress
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.config import Settings
from millicall.deps import get_secret_box, get_session, get_settings_dep
from millicall.models import Contact, Device, Extension, NetworkConfig
from millicall.network.validation import normalize_mac
from millicall.provisioning.service import (
    PROVISION_MODE_STRICT,
    hash_provision_token,
    provision_token_matches,
    record_config_fetch,
)
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
from millicall.sip_credentials import decrypt_extension_password

logger = logging.getLogger("millicall.provisioning.router")

# Panasonic のプレプロビジョニング入口ファイル名（{MODEL}.cfg）を判定する正規表現。
# KX-HDV130N / KX-HDV230N / KX-HDV330N / KX-HDV430N などの N サフィックス、
# および NB サフィックス（KX-HDV130NB 等）を含め「KX-HDV で始まり英数字・ハイフンが続く」
# ものを広く受理する。Yealink の {mac}.cfg（12桁hex）とは決して衝突しない。
_PANASONIC_MODEL_RE = re.compile(r"^KX-HDV[A-Za-z0-9-]+$")

# パス形式トークンの許容形式（secrets.token_urlsafe(32) は 43 文字の URL セーフ文字列）。
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

# アクセスログからトークンを伏せるためのパターン。
_TOKEN_PATH_RE = re.compile(r"(/provisioning/t/)[^/?#\s\"]+")

router = APIRouter(prefix="/provisioning", tags=["provisioning"])

# 電話機が取得するファイル群。通常 URL（/provisioning/...）と厳格モードのトークン付き URL
# （/provisioning/t/{token}/...）の両方に同じハンドラを載せる（モジュール末尾で include）。
_files = APIRouter()


def redact_provisioning_token(text: str) -> str:
    """文字列中の ``/provisioning/t/<token>`` を ``/provisioning/t/***`` に置き換える。"""
    return _TOKEN_PATH_RE.sub(r"\1***", text)


class ProvisioningTokenRedactFilter(logging.Filter):
    """uvicorn アクセスログのリクエストパスからプロビジョニングトークンを伏せる。

    uvicorn.access は ``'%s - "%s %s HTTP/%s" %d'`` 形式でパスを args に入れるため、
    msg と文字列 args の両方を置換する。レコードは常に通す。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_provisioning_token(record.msg)
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(
                redact_provisioning_token(a) if isinstance(a, str) else a for a in record.args
            )
        return True


def install_access_log_redaction() -> None:
    """uvicorn.access ロガーにトークン伏字フィルタを（重複なく）付ける。

    uvicorn はアプリの import 前にロギングを構成し、dictConfig は既存ロガーの
    フィルタを外さないため、import 時に付ければ実運用のアクセスログに効く。
    """
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, ProvisioningTokenRedactFilter) for f in access_logger.filters):
        access_logger.addFilter(ProvisioningTokenRedactFilter())


install_access_log_redaction()


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


async def _token_scope(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> str | None:
    """パス形式トークンを取り出し、厳格モードのいずれかの端末のトークンか確かめる。

    通常 URL（トークンなし）なら None を返す。トークン付き URL で、形式不正または
    どの厳格モード端末のトークンとも一致しない場合は 404（共通ファイルもトークンを
    鍵に配らない）。端末固有ファイルは ``_get_provisioned_device`` で端末ごとに再照合する。
    """
    token = request.path_params.get("token")
    if token is None:
        return None
    if not _TOKEN_RE.match(token):
        raise HTTPException(status_code=404)
    found = await session.scalar(
        select(Device.id)
        .where(
            Device.provision_mode == PROVISION_MODE_STRICT,
            Device.provision_token == hash_provision_token(token),
        )
        .limit(1)
    )
    if found is None:
        raise HTTPException(status_code=404)
    return token


async def _get_provisioned_device(
    mac_normalized: str,
    token: str | None,
    session: AsyncSession,
    client_ip: str,
) -> Device:
    """プロビジョニング済みデバイスを検索し、provision_mode に応じたゲートを適用する。

    1. MAC で Device 行を検索 → 見つからなければ 404
    2. provisioned=False / active=False / extension_id=None → 404
    3. 送信元 IP が Device の IP と一致しない → 404
    4. トークン付き URL（token あり）なら、その端末のトークンと一致しなければ 404
       （standard 端末はトークンを持たないので常に 404）
    5. provision_mode="strict" でトークンなし（通常 URL）→ 404
    トークンは消費しない（厳格モードの電話機は以後も同じトークン付き URL で取得する）。

    Args:
        mac_normalized: 正規化済みの MAC アドレス文字列。
        token: パス形式トークン（通常 URL なら None）。
        session: SQLAlchemy 非同期セッション。
        client_ip: リクエスト送信元 IP。

    Returns:
        プロビジョニング済みの Device オブジェクト。

    Raises:
        HTTPException(404): 上記いずれかの条件に該当する場合。
    """
    device = await session.scalar(select(Device).where(Device.mac_address == mac_normalized))
    if device is None:
        raise HTTPException(status_code=404)
    if not device.provisioned or not device.active or device.extension_id is None:
        raise HTTPException(status_code=404)

    if not device.ip_address or device.ip_address != client_ip:
        raise HTTPException(status_code=404)

    if token is not None and not provision_token_matches(device, token):
        raise HTTPException(status_code=404)
    if device.provision_mode == PROVISION_MODE_STRICT and token is None:
        raise HTTPException(status_code=404)

    return device


async def _record_fetch(
    session: AsyncSession, device: Device, request: Request, token: str | None
) -> None:
    """端末固有 cfg の配信成功を記録する（取得確認用）。"""
    record_config_fetch(
        device, user_agent=request.headers.get("user-agent"), used_token=token is not None
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Panasonic エンドポイント
# ---------------------------------------------------------------------------


@_files.get("/Panasonic/ConfigCommon.cfg", response_class=PlainTextResponse)
async def panasonic_common_config(
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
    token: str | None = Depends(_token_scope),
) -> PlainTextResponse:
    """Panasonic KX-HDV 共通設定ファイルを返す（LAN 制限のみ。トークン付き URL は照合あり）。

    端末固有のクレデンシャルを含まない共通設定。
    """
    content = render_panasonic_common(network_config=nc, settings=settings, token=token)
    return PlainTextResponse(
        content, media_type="text/plain", headers={"Cache-Control": "no-store"}
    )


@_files.get("/Panasonic/Config{mac}.cfg", response_class=PlainTextResponse)
async def panasonic_device_config(
    mac: str,
    request: Request,
    token: str | None = Depends(_token_scope),
    session: AsyncSession = Depends(get_session),
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """Panasonic KX-HDV 端末固有設定ファイルを返す（LAN 制限 + known-device + IP 一致 + provision_mode ゲート）。

    SIP 認証情報を含むため、プロビジョニング済みデバイスのみアクセス可能。
    配信に成功したら取得記録（日時・User-Agent・トークン使用有無）を残す。
    """
    # MAC 正規化
    try:
        mac_normalized = normalize_mac(mac)
    except ValueError:
        raise HTTPException(status_code=404) from None

    device = await _get_provisioned_device(mac_normalized, token, session, request.client.host)

    ext = await session.get(Extension, device.extension_id)
    if ext is None or not ext.enabled:
        raise HTTPException(status_code=404)

    content = render_panasonic_config(
        extension=ext,
        sip_password=decrypt_extension_password(get_secret_box(request), ext),
        network_config=nc,
        settings=settings,
    )
    await _record_fetch(session, device, request, token)
    return PlainTextResponse(
        content, media_type="text/plain", headers={"Cache-Control": "no-store"}
    )


# ---------------------------------------------------------------------------
# Yealink エンドポイント
# ---------------------------------------------------------------------------


@_files.get("/Yealink/y000000000000.boot", response_class=PlainTextResponse)
async def yealink_boot(
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
    token: str | None = Depends(_token_scope),
) -> PlainTextResponse:
    """Yealink 自動プロビジョニング起動ファイルを返す（LAN 制限のみ。トークン付きは照合あり）。"""
    content = render_yealink_boot(network_config=nc, settings=settings, token=token)
    return PlainTextResponse(
        content, media_type="text/plain", headers={"Cache-Control": "no-store"}
    )


@_files.get("/Yealink/common.cfg", response_class=PlainTextResponse)
async def yealink_common_config(
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
    token: str | None = Depends(_token_scope),
) -> PlainTextResponse:
    """Yealink 共通設定ファイルを返す（LAN 制限のみ。トークン付きは照合あり）。"""
    content = render_yealink_common(network_config=nc, settings=settings, token=token)
    return PlainTextResponse(
        content, media_type="text/plain", headers={"Cache-Control": "no-store"}
    )


@_files.get("/Yealink/{mac}.cfg", response_class=PlainTextResponse)
async def yealink_device_config(
    mac: str,
    request: Request,
    token: str | None = Depends(_token_scope),
    session: AsyncSession = Depends(get_session),
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """Yealink 端末固有設定ファイルを返す（LAN 制限 + known-device + IP 一致 + provision_mode ゲート）。

    SIP 認証情報を含むため、プロビジョニング済みデバイスのみアクセス可能。
    配信に成功したら取得記録（日時・User-Agent・トークン使用有無）を残す。
    """
    # MAC 正規化
    try:
        mac_normalized = normalize_mac(mac)
    except ValueError:
        raise HTTPException(status_code=404) from None

    device = await _get_provisioned_device(mac_normalized, token, session, request.client.host)

    ext = await session.get(Extension, device.extension_id)
    if ext is None or not ext.enabled:
        raise HTTPException(status_code=404)

    content = render_yealink_config(
        extension=ext,
        sip_password=decrypt_extension_password(get_secret_box(request), ext),
        network_config=nc,
        settings=settings,
    )
    await _record_fetch(session, device, request, token)
    return PlainTextResponse(
        content, media_type="text/plain", headers={"Cache-Control": "no-store"}
    )


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


@_files.get("/ConfigCommon.cfg", response_class=PlainTextResponse)
async def root_panasonic_common_config(
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
    token: str | None = Depends(_token_scope),
) -> PlainTextResponse:
    """option 66 直下の Panasonic 共通設定を返す（既存 /Panasonic/ConfigCommon.cfg に相当）。

    Panasonic KX-HDV は option 66 直下に ConfigCommon.cfg を直接要求する。
    セキュリティ・レンダリングは既存ハンドラと同一（LAN 制限のみ）。
    """
    return await panasonic_common_config(nc=nc, settings=settings, token=token)


@_files.get("/y000000000000.boot", response_class=PlainTextResponse)
async def root_yealink_common_boot(
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
    token: str | None = Depends(_token_scope),
) -> PlainTextResponse:
    """option 66 直下の Yealink 共通 boot ファイルを返す（既存 /Yealink/y000000000000.boot に相当）。

    boot 内の絶対 URL include（/Yealink/common.cfg, /Yealink/$MAC.cfg）経由で
    残りの設定は既存の /Yealink/ ルートに到達する。セキュリティは LAN 制限のみ。
    """
    return await yealink_boot(nc=nc, settings=settings, token=token)


@_files.get("/Config{mac}.cfg", response_class=PlainTextResponse)
async def root_panasonic_device_config(
    mac: str,
    request: Request,
    token: str | None = Depends(_token_scope),
    session: AsyncSession = Depends(get_session),
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """option 66 直下の Panasonic 端末固有設定を返す（既存 /Panasonic/Config{mac}.cfg に相当）。

    SIP 認証情報を含むため、LAN 制限 + known-device + IP 一致 + provision_mode ゲートを維持する。
    """
    return await panasonic_device_config(
        mac=mac, request=request, token=token, session=session, nc=nc, settings=settings
    )


@_files.get("/{mac}.boot", response_class=PlainTextResponse)
async def root_yealink_mac_boot(
    mac: str,
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
    token: str | None = Depends(_token_scope),
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
    return await yealink_boot(nc=nc, settings=settings, token=token)


@_files.get("/{name}.cfg", response_class=PlainTextResponse)
async def root_cfg_dispatcher(
    name: str,
    request: Request,
    token: str | None = Depends(_token_scope),
    session: AsyncSession = Depends(get_session),
    nc: NetworkConfig = Depends(_require_lan),
    settings: Settings = Depends(get_settings_dep),
) -> PlainTextResponse:
    """option 66 直下の ``{name}.cfg`` を単一ディスパッチャで振り分ける。

    ``ConfigCommon.cfg`` / ``Config{mac}.cfg`` は上位の固定・具体ルートで処理済みのため
    ここには到達しない。この直下 ``{name}.cfg`` に来るのは主に次の 2 系統:

    - Panasonic プレプロビジョニング入口ファイル ``{MODEL}.cfg``
      （KX-HDV130N / KX-HDV230N / KX-HDV330N / KX-HDV430N とその NB サフィックス等）。
      SIP 認証情報を含まない最小の入口ファイルのため、LAN 制限のみでトークン不要
      （トークン付き URL で来た場合は照合し、取得先 URL もトークン付きで返す）。
    - Yealink 端末固有設定 ``{mac}.cfg``（12桁hex）。SIP 認証情報を含むため
      端末固有 cfg の配信ゲート（モジュール docstring 参照）を適用する。

    ``{model}.cfg`` と ``{mac}.cfg`` は同じ ``X.cfg`` にマッチし FastAPI では
    ルート衝突するため、ファイル名パターンで確実に分岐する（KX-HDV* → 入口ファイル、
    それ以外 → 従来どおり MAC 検証を経て Yealink 端末設定）。
    """
    # Panasonic プレプロビジョニング入口ファイル（{MODEL}.cfg）
    if _PANASONIC_MODEL_RE.match(name):
        content = render_panasonic_model_file(network_config=nc, settings=settings, token=token)
        return PlainTextResponse(
            content, media_type="text/plain", headers={"Cache-Control": "no-store"}
        )

    # それ以外は従来どおり Yealink 端末固有設定として MAC 検証を経て提供する。
    # Yealink は boot 経由（絶対 URL include）で /Yealink/{mac}.cfg を取得するため
    # 直下 {mac}.cfg が無くても無害だが、堅牢性のため直下でも同一設定を提供する。
    # SIP 認証情報を含むため、LAN 制限 + known-device + IP 一致 + provision_mode ゲートを維持する。
    return await yealink_device_config(
        mac=name, request=request, token=token, session=session, nc=nc, settings=settings
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


# ---------------------------------------------------------------------------
# ファイル群のマウント（通常 URL とトークン付き URL）
#
# 電話帳は LAN 制限のみで、cfg からも通常 URL で参照するためトークン付きには載せない。
# ---------------------------------------------------------------------------

router.include_router(_files)
router.include_router(_files, prefix="/t/{token}")
