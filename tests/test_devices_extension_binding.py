"""電話機（devices）の内線割当を「事前定義した内線から選んで紐づける」形にするテスト（issue #149）。

quick-provision の extension_id 指定・新規作成・両方指定 422・他端末割当済み 409・reassign、
PATCH での変更/解除、内線一覧の端末要約、内線削除時の端末解除と監査ログを検証する。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import select

from millicall.models import AuditLog, Device, Extension
from tests.sip_helpers import encrypt_for_test


class _CountingListener:
    """FreeSWITCH 再生成通知の回数を数えるリスナー。"""

    def __init__(self) -> None:
        self.calls = 0

    async def notify(self, session, *, sync_gateway: str | None = None) -> None:
        self.calls += 1


@pytest.fixture
def listener(app) -> _CountingListener:
    fake = _CountingListener()
    app.state.change_listener = fake
    return fake


async def _ext(app, number: str, display_name: str = "User") -> int:
    async with app.state.sessionmaker() as session:
        ext = Extension(
            number=number,
            display_name=display_name,
            sip_password_encrypted=encrypt_for_test("pass12345678", app=app),
        )
        session.add(ext)
        await session.commit()
        return ext.id


async def _device(
    app,
    mac: str,
    *,
    extension_id: int | None = None,
    model: str | None = "T54W",
    ip: str = "10.0.0.100",
) -> int:
    async with app.state.sessionmaker() as session:
        device = Device(
            mac_address=mac,
            ip_address=ip,
            model=model,
            provisioned=extension_id is not None,
            active=True,
            extension_id=extension_id,
            last_config_fetch_at=datetime(2026, 1, 1) if extension_id else None,
        )
        session.add(device)
        await session.commit()
        return device.id


async def _get(app, model, pk):
    async with app.state.sessionmaker() as session:
        return await session.get(model, pk)


async def _audits(app, action: str) -> list[dict]:
    async with app.state.sessionmaker() as session:
        rows = await session.scalars(select(AuditLog).where(AuditLog.action == action))
        return [json.loads(r.detail or "{}") for r in rows]


# ---------------------------------------------------------------------------
# quick-provision
# ---------------------------------------------------------------------------


async def test_quick_provision_by_extension_id(app, auth_client_with_telephony, listener) -> None:
    """extension_id で既存内線を選んで紐づける。表示名は変更しない。"""
    ext_id = await _ext(app, "1001", "受付")
    dev_id = await _device(app, "AA:00:00:00:00:01")

    resp = await auth_client_with_telephony.post(
        f"/api/devices/{dev_id}/quick-provision", json={"extension_id": ext_id}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["extension_id"] == ext_id
    assert body["extension_number"] == "1001"
    assert body["extension_display_name"] == "受付"
    assert body["provisioned"] is True
    assert listener.calls == 1

    audits = await _audits(app, "device.extension.assign")
    assert audits[-1]["extension_number"] == "1001"
    assert audits[-1]["mac_address"] == "AA:00:00:00:00:01"


async def test_quick_provision_new_extension_still_works(
    app, auth_client_with_telephony, listener
) -> None:
    """従来の番号 + 表示名指定は内線を新規作成して紐づける。"""
    dev_id = await _device(app, "AA:00:00:00:00:02")
    resp = await auth_client_with_telephony.post(
        f"/api/devices/{dev_id}/quick-provision",
        json={"extension_number": "1002", "display_name": "新規"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["extension_number"] == "1002"
    audits = await _audits(app, "device.extension.assign")
    assert audits[-1]["extension_created"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"extension_id": 1, "extension_number": "1003", "display_name": "x"},
        {"extension_id": 1, "extension_number": "1003"},
        {},
        {"extension_number": "1003"},
    ],
)
async def test_quick_provision_invalid_target_422(app, auth_client_with_telephony, payload) -> None:
    """extension_id と番号指定の両方、またはどちらも不足は 422。"""
    dev_id = await _device(app, "AA:00:00:00:00:03")
    resp = await auth_client_with_telephony.post(
        f"/api/devices/{dev_id}/quick-provision", json=payload
    )
    assert resp.status_code == 422


async def test_quick_provision_unknown_extension_404(app, auth_client_with_telephony) -> None:
    dev_id = await _device(app, "AA:00:00:00:00:04")
    resp = await auth_client_with_telephony.post(
        f"/api/devices/{dev_id}/quick-provision", json={"extension_id": 99999}
    )
    assert resp.status_code == 404


async def test_quick_provision_conflict_409(app, auth_client_with_telephony, listener) -> None:
    """別の端末に割当済みの内線を選ぶと 409（相手端末の MAC/モデル/IP を含む）。"""
    ext_id = await _ext(app, "1005")
    holder = await _device(app, "AA:00:00:00:00:05", extension_id=ext_id, ip="10.0.0.55")
    dev_id = await _device(app, "AA:00:00:00:00:06", ip="10.0.0.56")

    resp = await auth_client_with_telephony.post(
        f"/api/devices/{dev_id}/quick-provision", json={"extension_id": ext_id}
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "AA:00:00:00:00:05" in detail
    assert "T54W" in detail
    assert "10.0.0.55" in detail
    # 何も変わっていない
    assert (await _get(app, Device, holder)).extension_id == ext_id
    assert (await _get(app, Device, dev_id)).extension_id is None
    assert listener.calls == 0


async def test_quick_provision_conflict_by_number_409(app, auth_client_with_telephony) -> None:
    """番号指定でも、既存内線が別の端末に割当済みなら 409（1 内線 = 1 端末）。"""
    ext_id = await _ext(app, "1007")
    await _device(app, "AA:00:00:00:00:07", extension_id=ext_id)
    dev_id = await _device(app, "AA:00:00:00:00:08")
    resp = await auth_client_with_telephony.post(
        f"/api/devices/{dev_id}/quick-provision",
        json={"extension_number": "1007", "display_name": "x"},
    )
    assert resp.status_code == 409


async def test_quick_provision_reassign(app, auth_client_with_telephony, listener) -> None:
    """reassign=True なら旧端末の割当を外して付け替える。"""
    ext_id = await _ext(app, "1009")
    holder = await _device(app, "AA:00:00:00:00:09", extension_id=ext_id)
    dev_id = await _device(app, "AA:00:00:00:00:10")

    resp = await auth_client_with_telephony.post(
        f"/api/devices/{dev_id}/quick-provision",
        json={"extension_id": ext_id, "reassign": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["extension_id"] == ext_id

    old = await _get(app, Device, holder)
    assert old.extension_id is None
    assert old.provisioned is False
    assert old.last_config_fetch_at is None
    assert listener.calls == 1

    reassign = await _audits(app, "device.extension.reassign")
    assert reassign[-1]["from_device_mac_addresses"] == ["AA:00:00:00:00:09"]
    unassign = await _audits(app, "device.extension.unassign")
    assert unassign[-1]["mac_address"] == "AA:00:00:00:00:09"
    assert unassign[-1]["reason"] == "reassign"


async def test_quick_provision_same_device_same_extension_ok(
    app, auth_client_with_telephony
) -> None:
    """自分自身に割当済みの内線を選び直しても 409 にならない。"""
    ext_id = await _ext(app, "1011")
    dev_id = await _device(app, "AA:00:00:00:00:11", extension_id=ext_id)
    resp = await auth_client_with_telephony.post(
        f"/api/devices/{dev_id}/quick-provision", json={"extension_id": ext_id}
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# PATCH /api/devices/{id}
# ---------------------------------------------------------------------------


async def test_patch_unassign(app, auth_client_with_telephony, listener) -> None:
    """extension_id=null で割当を解除する（provisioned=False・取得記録リセット・再生成通知）。"""
    ext_id = await _ext(app, "1012")
    dev_id = await _device(app, "AA:00:00:00:00:12", extension_id=ext_id)

    resp = await auth_client_with_telephony.patch(
        f"/api/devices/{dev_id}", json={"extension_id": None}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["extension_id"] is None
    assert resp.json()["provisioned"] is False
    dev = await _get(app, Device, dev_id)
    assert dev.last_config_fetch_at is None
    assert listener.calls == 1
    audits = await _audits(app, "device.extension.unassign")
    assert audits[-1] == {
        "mac_address": "AA:00:00:00:00:12",
        "extension_number": "1012",
        "reason": "manual",
    }


async def test_patch_change_extension(app, auth_client_with_telephony, listener) -> None:
    """extension_id を別の内線に変更できる。他端末割当済みなら 409、reassign で付け替え。"""
    a = await _ext(app, "1013")
    b = await _ext(app, "1014")
    dev_id = await _device(app, "AA:00:00:00:00:13", extension_id=a)
    holder = await _device(app, "AA:00:00:00:00:14", extension_id=b)

    conflict = await auth_client_with_telephony.patch(
        f"/api/devices/{dev_id}", json={"extension_id": b}
    )
    assert conflict.status_code == 409
    assert "AA:00:00:00:00:14" in conflict.json()["detail"]

    resp = await auth_client_with_telephony.patch(
        f"/api/devices/{dev_id}", json={"extension_id": b, "reassign": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["extension_number"] == "1014"
    assert (await _get(app, Device, holder)).extension_id is None
    assert listener.calls == 1


async def test_patch_without_extension_id_keeps_assignment(
    app, auth_client_with_telephony, listener
) -> None:
    """extension_id を送らない PATCH は割当を変更しない。"""
    ext_id = await _ext(app, "1015")
    dev_id = await _device(app, "AA:00:00:00:00:15", extension_id=ext_id)
    resp = await auth_client_with_telephony.patch(
        f"/api/devices/{dev_id}", json={"provision_mode": "strict"}
    )
    assert resp.status_code == 200
    assert resp.json()["extension_id"] == ext_id
    assert listener.calls == 0


# ---------------------------------------------------------------------------
# /api/extensions
# ---------------------------------------------------------------------------


async def test_extensions_list_includes_device_summary(app, auth_client_with_telephony) -> None:
    ext_id = await _ext(app, "1016")
    await _ext(app, "1017")
    dev_id = await _device(app, "AA:00:00:00:00:16", extension_id=ext_id, ip="10.0.0.16")

    resp = await auth_client_with_telephony.get("/api/extensions")
    assert resp.status_code == 200
    by_number = {e["number"]: e for e in resp.json()}
    devices = by_number["1016"]["devices"]
    assert len(devices) == 1
    assert devices[0]["device_id"] == dev_id
    assert devices[0]["mac_address"] == "AA:00:00:00:00:16"
    assert devices[0]["model"] == "T54W"
    assert devices[0]["ip_address"] == "10.0.0.16"
    assert "last_seen" in devices[0]
    assert by_number["1017"]["devices"] == []

    one = await auth_client_with_telephony.get(f"/api/extensions/{ext_id}")
    assert one.json()["devices"][0]["device_id"] == dev_id


async def test_delete_extension_unbinds_device(app, auth_client_with_telephony, listener) -> None:
    """内線を削除すると紐づく端末の割当を解除し（provisioned=False）、監査ログに残す。"""
    ext_id = await _ext(app, "1018")
    dev_id = await _device(app, "AA:00:00:00:00:18", extension_id=ext_id)

    resp = await auth_client_with_telephony.delete(f"/api/extensions/{ext_id}")
    assert resp.status_code == 204

    dev = await _get(app, Device, dev_id)
    assert dev is not None
    assert dev.extension_id is None
    assert dev.provisioned is False
    assert dev.last_config_fetch_at is None
    assert listener.calls == 1

    unassign = await _audits(app, "device.extension.unassign")
    assert unassign[-1]["reason"] == "extension_deleted"
    assert unassign[-1]["extension_number"] == "1018"
    deleted = await _audits(app, "extension.delete")
    assert deleted[-1]["released_device_mac_addresses"] == ["AA:00:00:00:00:18"]
