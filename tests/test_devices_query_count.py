"""端末同期・一覧の SQL 回数テスト（issue #83）。

sync_devices_from_leases と GET /api/devices の SELECT 数が件数に比例しないこと、
および重複 MAC の勝敗規則・返り値の順序を固定する。
"""

from __future__ import annotations

import time
from contextlib import contextmanager

import pytest
from sqlalchemy import event, select

from millicall.models import Device, Extension
from millicall.provisioning.service import sync_devices_from_leases

# 1000 件でも IN 句チャンク（SQLite のバインド変数上限対策）数 + 定数に収まる上限。
_MAX_SELECTS = 10


class FakeNetdClient:
    def __init__(self, leases: list[dict]) -> None:
        self._leases = leases

    async def get_dhcp_leases(self) -> list[dict]:
        return self._leases


@contextmanager
def count_statements(app):
    """セッションが使う engine で実行された SQL 文を種別ごとに数える。"""
    engine = app.state.sessionmaker.kw["bind"].sync_engine
    stats = {"select": 0, "total": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        stats["total"] += 1
        if statement.lstrip().upper().startswith("SELECT"):
            stats["select"] += 1

    event.listen(engine, "before_cursor_execute", _before)
    try:
        yield stats
    finally:
        event.remove(engine, "before_cursor_execute", _before)


def _mac(i: int) -> str:
    return f"02:00:00:{(i >> 16) & 0xFF:02X}:{(i >> 8) & 0xFF:02X}:{i & 0xFF:02X}"


def _leases(n: int) -> list[dict]:
    return [
        {"mac": _mac(i), "ip": f"10.0.{i // 250}.{i % 250 + 1}", "hostname": f"ph-{i}"}
        for i in range(n)
    ]


@pytest.mark.parametrize("n", [1, 100, 1000])
async def test_sync_select_count_is_not_proportional(app, n) -> None:
    """新規のみ / 既存のみのどちらでも同期の SELECT 数が件数に比例しない。"""
    sm = app.state.sessionmaker
    leases = _leases(n)

    async with sm() as session:
        with count_statements(app) as stats:
            t0 = time.perf_counter()
            devices = await sync_devices_from_leases(session, FakeNetdClient(leases))
            elapsed = time.perf_counter() - t0
        print(
            f"\n[sync-new n={n}] select={stats['select']} total={stats['total']} "
            f"{elapsed * 1000:.1f}ms"
        )
        assert len(devices) == n
        assert stats["select"] <= _MAX_SELECTS
        # commit 後の属性アクセスで遅延ロード（MissingGreenlet）が起きない
        assert all(d.id is not None and d.created_at is not None for d in devices)

    # 2 回目は全件が既存端末
    async with sm() as session:
        with count_statements(app) as stats:
            t0 = time.perf_counter()
            devices = await sync_devices_from_leases(session, FakeNetdClient(leases))
            elapsed = time.perf_counter() - t0
        print(
            f"[sync-existing n={n}] select={stats['select']} total={stats['total']} "
            f"{elapsed * 1000:.1f}ms"
        )
        assert len(devices) == n
        assert stats["select"] <= _MAX_SELECTS
        assert all(d.id is not None and d.created_at is not None for d in devices)


@pytest.mark.parametrize("n", [1, 100, 1000])
async def test_list_select_count_is_not_proportional(app, auth_client_with_telephony, n) -> None:
    """一覧の SELECT 数が件数（Extension 付き端末数）に比例しない。"""
    sm = app.state.sessionmaker
    async with sm() as session:
        exts = [
            Extension(number=f"{2000 + i}", display_name=f"User {i}", sip_password="x" * 16)
            for i in range(n)
        ]
        session.add_all(exts)
        await session.flush()
        session.add_all(
            Device(mac_address=_mac(i), ip_address="10.0.0.1", extension_id=exts[i].id)
            for i in range(n)
        )
        await session.commit()

    with count_statements(app) as stats:
        t0 = time.perf_counter()
        resp = await auth_client_with_telephony.get("/api/devices")
        elapsed = time.perf_counter() - t0
    print(f"\n[list n={n}] select={stats['select']} total={stats['total']} {elapsed * 1000:.1f}ms")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == n
    assert [d["id"] for d in body] == sorted(d["id"] for d in body)
    assert body[0]["extension_number"] == "2000"
    assert body[-1]["extension_display_name"] == f"User {n - 1}"
    assert stats["select"] <= _MAX_SELECTS


async def test_list_devices_without_extension(app, auth_client_with_telephony) -> None:
    """内線なし端末は extension_* が None（JOIN で端末が落ちない）。id 昇順。"""
    sm = app.state.sessionmaker
    async with sm() as session:
        ext = Extension(number="3001", display_name="Has Ext", sip_password="x" * 16)
        session.add(ext)
        await session.flush()
        session.add(Device(mac_address=_mac(1), extension_id=ext.id))
        session.add(Device(mac_address=_mac(2)))
        await session.commit()

    resp = await auth_client_with_telephony.get("/api/devices")
    body = resp.json()
    assert [d["extension_number"] for d in body] == ["3001", None]
    assert body[1]["extension_id"] is None


async def test_sync_duplicate_mac_last_lease_wins_and_repeats_in_result(app) -> None:
    """同一 MAC のリースが複数ある場合: 最後のリースが勝ち、返り値はリース順で同一端末が重複する。

    現行実装（リースごとに SELECT→upsert、autoflush で先行リースの行が見える）の挙動を固定する。
    表記ゆれ（ハイフン区切り・小文字）は正規化後に同一 MAC として扱われる。
    """
    sm = app.state.sessionmaker
    leases = [
        {"mac": "AA:BB:CC:00:00:01", "ip": "10.0.0.1", "hostname": "first"},
        {"mac": "AA:BB:CC:00:00:02", "ip": "10.0.0.2", "hostname": "other"},
        {"mac": "aa-bb-cc-00-00-01", "ip": "10.0.0.3", "hostname": "last"},
    ]
    async with sm() as session:
        devices = await sync_devices_from_leases(session, FakeNetdClient(leases))
        assert [d.mac_address for d in devices] == [
            "AA:BB:CC:00:00:01",
            "AA:BB:CC:00:00:02",
            "AA:BB:CC:00:00:01",
        ]
        assert devices[0] is devices[2]
        assert (devices[0].ip_address, devices[0].hostname) == ("10.0.0.3", "last")

    async with sm() as session:
        rows = list(await session.scalars(select(Device).order_by(Device.id)))
        assert len(rows) == 2
        assert (rows[0].ip_address, rows[0].hostname) == ("10.0.0.3", "last")


async def test_sync_duplicate_mac_on_existing_device_last_wins(app) -> None:
    """既存端末に対する重複 MAC リースでも最後のリースが勝つ。extension 紐付けは維持される。"""
    sm = app.state.sessionmaker
    async with sm() as session:
        ext = Extension(number="4001", display_name="E", sip_password="x" * 16)
        session.add(ext)
        await session.flush()
        session.add(
            Device(
                mac_address="AA:BB:CC:00:00:09",
                ip_address="10.9.9.9",
                extension_id=ext.id,
                provisioned=True,
                hostname="old",
            )
        )
        await session.commit()

    leases = [
        {"mac": "AA:BB:CC:00:00:09", "ip": "10.0.0.1", "hostname": "first"},
        {"mac": "AA:BB:CC:00:00:09", "ip": "10.0.0.2", "hostname": "bad_host!"},
    ]
    async with sm() as session:
        devices = await sync_devices_from_leases(session, FakeNetdClient(leases))
        assert len(devices) == 2 and devices[0] is devices[1]
        d = devices[0]
        assert (d.ip_address, d.hostname) == ("10.0.0.2", None)
        assert d.provisioned is True and d.extension_id is not None and d.active is True


async def test_sync_invalid_leases_excluded_from_result_and_db(app) -> None:
    """不正 MAC / 不正 IP は除外し、不正 hostname は NULL。有効リースの順序は保たれる。"""
    sm = app.state.sessionmaker
    leases = [
        {"mac": "02:00:00:00:00:0B", "ip": "10.0.0.11", "hostname": "b"},
        {"mac": "bogus", "ip": "10.0.0.12", "hostname": "x"},
        {"mac": "02:00:00:00:00:0C", "ip": "999.0.0.1", "hostname": "y"},
        {"mac": "02:00:00:00:00:0A", "ip": "10.0.0.13", "hostname": "bad_host!"},
        {},
    ]
    async with sm() as session:
        devices = await sync_devices_from_leases(session, FakeNetdClient(leases))
        assert [d.mac_address for d in devices] == ["02:00:00:00:00:0B", "02:00:00:00:00:0A"]
        assert devices[1].hostname is None
    async with sm() as session:
        assert len(list(await session.scalars(select(Device)))) == 2


async def test_sync_works_when_session_expires_on_commit(app) -> None:
    """expire_on_commit=True のセッションでも commit 後の属性アクセスが破綻しない（refresh フォールバック）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sm = async_sessionmaker(app.state.sessionmaker.kw["bind"], expire_on_commit=True)
    async with sm() as session:
        devices = await sync_devices_from_leases(session, FakeNetdClient(_leases(3)))
        assert [d.id is not None and d.created_at is not None for d in devices] == [True] * 3
