from datetime import datetime

from millicall.models import CallMessage


async def test_list_call_messages_returns_ascending_by_id(auth_client_with_telephony, app):
    sm = app.state.sessionmaker
    async with sm() as s:
        s.add(CallMessage(call_uuid="c1", agent_id=1, role="user", text="もしもし",
                          latency_ms=None, created_at=datetime(2026, 7, 5, 10, 0, 0)))
        s.add(CallMessage(call_uuid="c1", agent_id=1, role="assistant", text="はい、こちら",
                          latency_ms=420, created_at=datetime(2026, 7, 5, 10, 0, 1)))
        s.add(CallMessage(call_uuid="other", agent_id=1, role="user", text="別通話",
                          latency_ms=None, created_at=datetime(2026, 7, 5, 10, 0, 2)))
        await s.commit()
    resp = await auth_client_with_telephony.get("/api/call-messages?call_uuid=c1")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]["role"] == "user"
    assert rows[0]["text"] == "もしもし"
    assert rows[1]["role"] == "assistant"
    assert rows[1]["latency_ms"] == 420
    # 昇順 id
    assert rows[0]["id"] < rows[1]["id"]


async def test_list_call_messages_empty_for_unknown_uuid(auth_client_with_telephony):
    resp = await auth_client_with_telephony.get("/api/call-messages?call_uuid=nope")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_call_messages_requires_call_uuid(auth_client_with_telephony):
    resp = await auth_client_with_telephony.get("/api/call-messages")
    assert resp.status_code == 422


async def test_call_messages_requires_auth(client):
    assert (await client.get("/api/call-messages?call_uuid=c1")).status_code == 401
