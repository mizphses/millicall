"""マイグレーション 0012 (network_config) / 0013 (devices) のスモークテスト。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from millicall.db import create_db_engine
from millicall.db_migrations import upgrade_to_head

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_SCRIPT_LOCATION = _REPO_ROOT / "alembic"


def _alembic_cfg(url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.mark.asyncio
async def test_network_config_table(tmp_path):
    """upgrade_to_head 後に network_config テーブルの全カラムが存在する。"""
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    upgrade_to_head(url)
    engine = create_db_engine(url)
    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("network_config")]
        )
    await engine.dispose()
    assert {
        "id",
        "lan_interface",
        "lan_ip",
        "lan_prefix",
        "dhcp_range_start",
        "dhcp_range_end",
        "dhcp_lease_hours",
        "provisioning_base_url",
        "nat_enabled",
        "wan_interface",
        "tailscale_enabled",
        "tailscale_auth_key_encrypted",
        "created_at",
        "updated_at",
    } <= set(cols)


@pytest.mark.asyncio
async def test_devices_table(tmp_path):
    """upgrade_to_head 後に devices テーブルの全カラムが存在し mac_address は UNIQUE。"""
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    upgrade_to_head(url)
    engine = create_db_engine(url)
    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("devices")]
        )
        indexes = await conn.run_sync(lambda c: inspect(c).get_indexes("devices"))
        uniques = await conn.run_sync(lambda c: inspect(c).get_unique_constraints("devices"))
    await engine.dispose()
    assert {
        "id",
        "mac_address",
        "ip_address",
        "hostname",
        "model",
        "extension_id",
        "provisioned",
        "provision_token",
        "last_seen",
        "active",
        "created_at",
    } <= set(cols)
    unique_mac = any(
        ix.get("unique") and ix.get("column_names") == ["mac_address"] for ix in indexes
    )
    unique_mac = unique_mac or any(uc.get("column_names") == ["mac_address"] for uc in uniques)
    assert unique_mac


@pytest.mark.asyncio
async def test_downgrade_0013(tmp_path):
    """0013 → 0012 のダウングレードで devices テーブルが削除される。"""
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    upgrade_to_head(url)
    command.downgrade(_alembic_cfg(url), "0012")
    engine = create_db_engine(url)
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    await engine.dispose()
    assert "devices" not in tables
    assert "network_config" in tables


@pytest.mark.asyncio
async def test_downgrade_0012(tmp_path):
    """0012 → 0011 のダウングレードで network_config テーブルが削除される。"""
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    upgrade_to_head(url)
    command.downgrade(_alembic_cfg(url), "0011")
    engine = create_db_engine(url)
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    await engine.dispose()
    assert "network_config" not in tables
    assert "devices" not in tables
