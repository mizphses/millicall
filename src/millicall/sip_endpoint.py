"""ソフトフォンが接続すべき internal SIP エンドポイント（サーバ／ドメイン）の算出。

PR #57 で telephony 設定生成に導入された internal プロファイルの実効値
（子LAN 適用時は lan_ip、そうでなければ settings.sip_bind_ip / sip_domain）と
同じ結果を返すための共通ヘルパー。管理画面の認証情報表示と設定生成で
同一ロジックを共有し、ソフトフォンの接続先と FreeSWITCH の待ち受けが一致することを保証する。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from millicall.config import Settings
from millicall.models import NetworkConfig


@dataclass(frozen=True)
class InternalSipEndpoint:
    """ソフトフォンが接続する internal SIP プロファイルの実効エンドポイント。

    sip_server: ソフトフォンの接続先ホスト（internal が待ち受ける IP）。
    domain: SIP レルム／ドメイン（internal のドメインと一致）。
    """

    sip_server: str
    domain: str


async def resolve_internal_sip_endpoint(
    session: AsyncSession, settings: Settings
) -> InternalSipEndpoint:
    """internal プロファイルの実効 sip_server / domain を返す。

    NetworkConfig(id=1) を読み、applied=True かつ lan_ip 非空なら子LAN GW IP を
    サーバ／ドメイン両方に使う（ソフトフォンも子LAN 上にいる前提）。それ以外は
    従来の主LAN 構成（domain=settings.sip_domain、
    sip_server=settings.sip_bind_ip or settings.sip_domain）へフォールバックする。

    この判定は telephony 設定生成（TelephonyService._load_child_lan_ip →
    fsconfig の internal_bind_ip / internal_domain）と同じ結果になる。
    """
    cfg = await session.get(NetworkConfig, 1)
    if cfg is not None and cfg.applied and cfg.lan_ip:
        return InternalSipEndpoint(sip_server=cfg.lan_ip, domain=cfg.lan_ip)
    return InternalSipEndpoint(
        sip_server=settings.sip_bind_ip or settings.sip_domain,
        domain=settings.sip_domain,
    )
