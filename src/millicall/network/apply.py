"""ネットワーク設定の netd への適用ロジック（共通化）。

POST /api/network/apply エンドポイントと、core 起動時（lifespan）の
自動再適用の両方から呼べるよう、「provisioning_url 構築 → netd.apply_dhcp
→ netd.apply_nat」の手順をひとつの async 関数へ抽出する。

netd はステートレスのため、この関数が送る DHCP/NAT 設定は netd 側で毎回
ホストのランタイム状態（インターフェース IP・nftables・dnsmasq）へ反映される。
失敗時は既存エンドポイントと同様に NetdError を送出する（握りつぶさない）。
起動時の best-effort 化（例外を warning に留める）は呼び出し側の責務とする。
"""

from millicall.config import Settings, http_port_suffix
from millicall.models import NetworkConfig
from millicall.network.client import NetdClient


async def apply_network_config_to_netd(
    cfg: NetworkConfig, netd: NetdClient, settings: Settings
) -> None:
    """保存済み NetworkConfig を netd 経由でホストへ適用する。

    apply_dhcp → apply_nat の順で実行する。provisioning_base_url が空の場合は
    lan_ip + core の HTTP ポートから provisioning URL を構築する
    （標準ポート 80 は URL から省略。option 66 が指す先 = core の待受ポート）。

    失敗時は NetdError を送出する。呼び出し側が用途に応じて 502 変換
    （エンドポイント）または warning ログのみ（起動時 best-effort）を選ぶ。
    """
    # provisioning_base_url が空の場合は lan_ip + core の HTTP ポートから構築する。
    provisioning_url = cfg.provisioning_base_url
    if not provisioning_url:
        suffix = http_port_suffix(settings.http_port)
        provisioning_url = f"http://{cfg.lan_ip}{suffix}/provisioning/"

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
        http_port=settings.http_port,
    )
