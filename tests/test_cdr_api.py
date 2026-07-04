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
