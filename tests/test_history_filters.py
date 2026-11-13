from datetime import datetime

import pytest

from millicall.models import AuditLog, Cdr


async def test_cdr_search_period_order_and_restorable_detail(auth_client_with_telephony, app):
    client = auth_client_with_telephony
    async with app.state.sessionmaker() as session:
        rows = [Cdr(call_uuid=f"filter-{i}", src_number="201", dst_number="03_100",
                    direction="inbound", started_at=datetime(2026, 11, 1, 10, i)) for i in range(3)]
        rows.append(Cdr(call_uuid="other", src_number="202", dst_number="03x100",
                        direction="outbound", started_at=datetime(2026, 11, 1, 10, 3)))
        session.add_all(rows)
        await session.commit()
    response = await client.get("/api/cdr", params={
        "q": "03_100", "since": "2026-11-01T19:01:00+09:00",
        "until": "2026-11-01T10:03:00Z", "order": "oldest", "limit": 1,
    })
    assert response.status_code == 200
    assert [row["call_uuid"] for row in response.json()] == ["filter-1"]
    detail = await client.get(f"/api/cdr/{response.json()[0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["call_uuid"] == "filter-1"
    assert (await client.get("/api/cdr/99999")).status_code == 404
    assert (await client.get("/api/cdr", params={"q": "filter-2"})).json()[0]["call_uuid"] == "filter-2"


async def test_audit_target_search_period_and_stable_pagination(auth_client_with_telephony, app):
    client = auth_client_with_telephony
    async with app.state.sessionmaker() as session:
        for i in range(3):
            session.add(AuditLog(actor_label="operator", action="trunk.update",
                target_type="trunk", target_id="42", detail=f"change {i}",
                created_at=datetime(2026, 11, 1, 10)))
        await session.commit()
    params = {"q": "trunk#42", "since": "2026-11-01T00:00:00Z",
              "until": "2026-11-02T00:00:00Z", "order": "oldest", "limit": 2}
    first = (await client.get("/api/audit", params=params)).json()
    second = (await client.get("/api/audit", params={**params, "offset": 2})).json()
    assert [row["detail"] for row in first + second] == ["change 0", "change 1", "change 2"]
    assert (await client.get("/api/audit", params={"q": "nonexistent"})).json() == []


@pytest.mark.parametrize("path", ["/api/cdr", "/api/audit"])
async def test_invalid_history_period_is_rejected(auth_client_with_telephony, path):
    response = await auth_client_with_telephony.get(path, params={
        "since": "2026-11-02T00:00:00Z", "until": "2026-11-01T00:00:00Z",
    })
    assert response.status_code == 422


async def test_cdr_detail_requires_auth(client):
    assert (await client.get("/api/cdr/1")).status_code == 401
