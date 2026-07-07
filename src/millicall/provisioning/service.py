"""プロビジョニングサービス — デバイス同期・クイックプロビジョニング・電話機リセット。

netd 経由で DHCP リースを取得して Device 行を upsert したり、
管理者が指定した内線番号をデバイスに割り当てたりするユースケース関数群。
"""

import base64
import logging
import secrets
import urllib.parse
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.gen import generate_sip_password
from millicall.models import Device, Extension
from millicall.network.client import NetdClient
from millicall.network.validation import is_valid_hostname, normalize_mac, validate_ipv4

logger = logging.getLogger("millicall.provisioning")


def _utcnow() -> datetime:
    """タイムゾーンなし UTC 現在時刻を返す（DB の他列に合わせて timezone-naive）。"""
    return datetime.now(UTC).replace(tzinfo=None)


async def sync_devices_from_leases(
    session: AsyncSession,
    netd_client: NetdClient,
) -> list[Device]:
    """DHCP リースから Device 行を upsert する。

    netd_client 経由でリースを取得し、既存行は ip/hostname/last_seen を更新、
    未登録 MAC は新規行を追加する（provisioned=False, active=True）。

    不正な MAC / IP はスキップする。不正な hostname は NULL として保存する。

    Args:
        session: SQLAlchemy 非同期セッション。
        netd_client: DHCP リース取得に使う NetdClient インスタンス。

    Returns:
        upsert した Device オブジェクトのリスト。

    Raises:
        NetdError: netd との通信失敗時（呼び出し元が 502 に変換する）。
    """
    leases = await netd_client.get_dhcp_leases()

    devices: list[Device] = []
    now = _utcnow()

    for lease in leases:
        raw_mac = lease.get("mac", "")
        raw_ip = lease.get("ip", "")
        raw_hostname = lease.get("hostname", "")

        # MAC 正規化 — 不正なら行をスキップ
        try:
            mac = normalize_mac(raw_mac)
        except ValueError:
            logger.debug("sync_devices: 不正な MAC をスキップ: %r", raw_mac)
            continue

        # IP 検証 — 不正なら行をスキップ
        try:
            validate_ipv4(raw_ip)
        except ValueError:
            logger.debug("sync_devices: 不正な IP をスキップ: %r", raw_ip)
            continue

        # hostname は RFC1123 で検証; 不正なら NULL として保存
        hostname: str | None = raw_hostname if is_valid_hostname(raw_hostname) else None

        # 既存行を MAC で検索
        existing = await session.scalar(
            select(Device).where(Device.mac_address == mac)
        )
        if existing is not None:
            existing.ip_address = raw_ip
            existing.hostname = hostname
            existing.last_seen = now
            existing.active = True
            devices.append(existing)
        else:
            device = Device(
                mac_address=mac,
                ip_address=raw_ip,
                hostname=hostname,
                provisioned=False,
                active=True,
                last_seen=now,
            )
            session.add(device)
            devices.append(device)

    await session.commit()

    # commit 後に id 等を確実に持たせるため refresh する
    for d in devices:
        await session.refresh(d)

    return devices


async def quick_provision(
    session: AsyncSession,
    device_id: int,
    extension_number: str,
    display_name: str,
    telephony_notify: Callable[[AsyncSession], Awaitable[None]],
) -> Device:
    """デバイスに内線を割り当ててプロビジョニング完了状態にする。

    Extension が存在しない場合は新規作成する（SIP パスワードは自動生成）。
    ワンタイムプロビジョニングトークンを設定し、FreeSWITCH 設定を再生成する。

    Args:
        session: SQLAlchemy 非同期セッション。
        device_id: 対象 Device の主キー。
        extension_number: 割り当てる内線番号（SIP ユーザー名）。
        display_name: 内線の表示名（既存 Extension がある場合は更新しない）。
        telephony_notify: TelephonyChangeListener.notify のような非同期 callable。
                          call signature: ``async (session) -> None``。

    Returns:
        更新済みの Device オブジェクト。

    Raises:
        ValueError: 指定した device_id のデバイスが存在しない場合。
    """
    device = await session.get(Device, device_id)
    if device is None:
        raise ValueError(f"Device not found: id={device_id}")

    # Extension を番号で検索、なければ新規作成
    ext = await session.scalar(
        select(Extension).where(Extension.number == extension_number)
    )
    if ext is None:
        ext = Extension(
            number=extension_number,
            display_name=display_name,
            sip_password=generate_sip_password(),
            enabled=True,
        )
        session.add(ext)
        await session.flush()  # ext.id を確定させる（commit は後）

    device.extension_id = ext.id
    device.provisioned = True
    device.provision_token = secrets.token_urlsafe(32)

    await session.commit()
    await session.refresh(device)

    # FreeSWITCH 設定再生成 + reloadxml
    await telephony_notify(session)

    return device


async def resync_phone(device: Device) -> bool:
    """電話機の IP アドレスへ HTTP resync リクエストを送る（ベストエフォート）。

    Panasonic KX-HDV → /admin/resync → /cgi-bin/api-provision の順で試行し、
    どちらも失敗した場合は Yealink AutoProvision を試みる。
    1 つでも成功したら True を返す。全失敗は False。

    エラーは飲み込み、例外を呼び出し元に伝播させない。

    Args:
        device: IP アドレスを持つ Device オブジェクト。

    Returns:
        resync リクエストが 1 つ以上成功したか否か。
    """
    if not device.ip_address:
        return False

    ip = device.ip_address
    creds = base64.b64encode(b"admin:adminpass").decode()
    auth_headers = {"Authorization": f"Basic {creds}"}

    async with httpx.AsyncClient() as client:
        # Panasonic resync (1): admin エンドポイント
        try:
            r = await client.get(
                f"http://{ip}/admin/resync",
                headers=auth_headers,
                timeout=5.0,
            )
            if r.status_code < 400:
                logger.info("resync_phone: Panasonic resync 成功 ip=%s", ip)
                return True
        except Exception:
            logger.debug("resync_phone: Panasonic /admin/resync 失敗 ip=%s", ip)

        # Panasonic resync (2): CGI フォールバック
        try:
            r = await client.get(
                f"http://{ip}/cgi-bin/api-provision?event=resync",
                timeout=5.0,
            )
            if r.status_code < 400:
                logger.info("resync_phone: Panasonic CGI resync 成功 ip=%s", ip)
                return True
        except Exception:
            logger.debug("resync_phone: Panasonic /cgi-bin/api-provision 失敗 ip=%s", ip)

        # Yealink AutoProvision servlet
        try:
            action = urllib.parse.quote("http://127.0.0.1/autoprovision")
            r = await client.get(
                f"http://{ip}/servlet?key=AutoProvision&value={action}",
                headers=auth_headers,
                timeout=5.0,
            )
            if r.status_code < 400:
                logger.info("resync_phone: Yealink AutoProvision 成功 ip=%s", ip)
                return True
        except Exception:
            logger.debug("resync_phone: Yealink /servlet AutoProvision 失敗 ip=%s", ip)

    logger.warning("resync_phone: 全手段失敗 ip=%s", ip)
    return False
