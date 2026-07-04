from datetime import datetime

from millicall.models import Cdr


async def test_list_cdr_returns_recent_first(auth_client_with_telephony, app):
    sm = app.state.sessionmaker
    async with sm() as s:
        s.add(Cdr(call_uuid="u1", direction="outbound", src_number="1001",
                  dst_number="0312345678", started_at=datetime(2026, 7, 5, 10, 0, 0),
                  duration_seconds=30, billsec_seconds=25, hangup_cause="NORMAL_CLEARING"))
        s.add(Cdr(call_uuid="u2", direction="inbound", src_number="0398765432",
                  dst_number="1001", started_at=datetime(2026, 7, 5, 11, 0, 0),
                  duration_seconds=10, billsec_seconds=0, hangup_cause="NO_ANSWER"))
        await s.commit()
    resp = await auth_client_with_telephony.get("/api/cdr")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]["call_uuid"] == "u2"  # 新しい順(started_at desc)


async def test_cdr_requires_auth(client):
    assert (await client.get("/api/cdr")).status_code == 401


async def test_list_cdr_direction_filter(auth_client_with_telephony, app):
    sm = app.state.sessionmaker
    async with sm() as s:
        s.add(Cdr(call_uuid="u1", direction="outbound", src_number="1001",
                  dst_number="0312345678", started_at=datetime(2026, 7, 5, 10, 0, 0),
                  duration_seconds=30, billsec_seconds=25, hangup_cause="NORMAL_CLEARING"))
        s.add(Cdr(call_uuid="u2", direction="inbound", src_number="0398765432",
                  dst_number="1001", started_at=datetime(2026, 7, 5, 11, 0, 0),
                  duration_seconds=10, billsec_seconds=0, hangup_cause="NO_ANSWER"))
        await s.commit()
    resp = await auth_client_with_telephony.get("/api/cdr?direction=inbound")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["call_uuid"] == "u2"
    assert rows[0]["direction"] == "inbound"


async def test_list_cdr_offset_pagination(auth_client_with_telephony, app):
    sm = app.state.sessionmaker
    async with sm() as s:
        for i in range(3):
            s.add(Cdr(call_uuid=f"u{i}", direction="outbound", src_number="1001",
                      dst_number=f"030{i}345678", started_at=datetime(2026, 7, 5, 10, i, 0),
                      duration_seconds=30, billsec_seconds=25, hangup_cause="NORMAL_CLEARING"))
        await s.commit()
    resp1 = await auth_client_with_telephony.get("/api/cdr?limit=2")
    assert resp1.status_code == 200
    rows1 = resp1.json()
    assert len(rows1) == 2

    resp2 = await auth_client_with_telephony.get("/api/cdr?limit=2&offset=2")
    assert resp2.status_code == 200
    rows2 = resp2.json()
    assert len(rows2) == 1

    # Verify no overlap
    uuids1 = {row["call_uuid"] for row in rows1}
    uuids2 = {row["call_uuid"] for row in rows2}
    assert len(uuids1 & uuids2) == 0


async def test_list_cdr_limit_bounds(auth_client_with_telephony):
    resp_zero = await auth_client_with_telephony.get("/api/cdr?limit=0")
    assert resp_zero.status_code == 422

    resp_over = await auth_client_with_telephony.get("/api/cdr?limit=1001")
    assert resp_over.status_code == 422
