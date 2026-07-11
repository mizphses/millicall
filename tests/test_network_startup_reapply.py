"""起動時ネットワーク設定再適用テスト。

OS/コンテナ再起動後、core 起動時（lifespan）に保存済みネットワーク設定を
netd へ best-effort で再適用する `_reapply_network_config_on_startup` を検証する。

カバレッジ:
  - applied=True の設定は起動時に netd へ apply_dhcp / apply_nat を送ること
  - applied=False（デフォルト未適用）では何も送らないこと
  - NetworkConfig が無ければ何もしないこと
  - netd 接続失敗（NetdError 等）でも例外を送出せず起動を止めないこと
"""

import pytest

from millicall.main import _reapply_network_config_on_startup
from millicall.models import NetworkConfig
from millicall.network.client import NetdError


class _FakeNetdClient:
    """テスト用の netd クライアントスタブ。呼び出し引数を記録する。"""

    def __init__(self) -> None:
        self.apply_dhcp_calls: list[dict] = []
        self.apply_nat_calls: list[dict] = []
        self.fail_apply_dhcp = False

    async def apply_dhcp(self, **kwargs) -> None:
        if self.fail_apply_dhcp:
            raise NetdError("apply_dhcp テスト失敗")
        self.apply_dhcp_calls.append(kwargs)

    async def apply_nat(self, **kwargs) -> None:
        self.apply_nat_calls.append(kwargs)


async def _set_config(app, *, applied: bool) -> None:
    """id=1 の NetworkConfig を用意し applied フラグを設定する。"""
    async with app.state.sessionmaker() as session:
        cfg = await session.get(NetworkConfig, 1)
        if cfg is None:
            cfg = NetworkConfig(id=1)
            session.add(cfg)
        cfg.lan_interface = "eth0"
        cfg.lan_ip = "192.168.1.1"
        cfg.lan_prefix = 24
        cfg.applied = applied
        await session.commit()


@pytest.mark.asyncio
async def test_startup_reapplies_when_applied_true(app):
    """applied=True の設定は起動時に netd へ apply_dhcp / apply_nat を送ること。"""
    fake = _FakeNetdClient()
    app.state.netd_client = fake
    await _set_config(app, applied=True)

    await _reapply_network_config_on_startup(app, app.state.settings)

    assert len(fake.apply_dhcp_calls) == 1
    assert len(fake.apply_nat_calls) == 1
    dhcp_args = fake.apply_dhcp_calls[0]
    assert dhcp_args["lan_interface"] == "eth0"
    assert dhcp_args["lan_ip"] == "192.168.1.1"
    assert dhcp_args["lan_prefix"] == 24


@pytest.mark.asyncio
async def test_startup_skips_when_applied_false(app):
    """applied=False では起動時に netd へ何も送らないこと。"""
    fake = _FakeNetdClient()
    app.state.netd_client = fake
    await _set_config(app, applied=False)

    await _reapply_network_config_on_startup(app, app.state.settings)

    assert fake.apply_dhcp_calls == []
    assert fake.apply_nat_calls == []


@pytest.mark.asyncio
async def test_startup_skips_when_no_config(app):
    """NetworkConfig 行が無ければ何もしないこと（例外も送出しない）。"""
    fake = _FakeNetdClient()
    app.state.netd_client = fake

    # id=1 行が存在しないことを保証する（app フィクスチャは自動生成しない）。
    async with app.state.sessionmaker() as session:
        cfg = await session.get(NetworkConfig, 1)
        assert cfg is None

    await _reapply_network_config_on_startup(app, app.state.settings)

    assert fake.apply_dhcp_calls == []
    assert fake.apply_nat_calls == []


@pytest.mark.asyncio
async def test_startup_swallows_netd_error(app):
    """netd 適用が失敗（NetdError）しても例外を送出せず起動を止めないこと。"""
    fake = _FakeNetdClient()
    fake.fail_apply_dhcp = True
    app.state.netd_client = fake
    await _set_config(app, applied=True)

    # 例外が送出されないこと（best-effort）。送出されればこのテストは失敗する。
    await _reapply_network_config_on_startup(app, app.state.settings)

    # apply_dhcp が失敗したので apply_nat には到達しない。
    assert fake.apply_nat_calls == []
