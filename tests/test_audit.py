"""監査ログのテスト。"""


async def test_record_audit_inserts_row(app):
    """record_auditがAuditLogを挿入する。"""
    from sqlalchemy import select

    from millicall.audit import record_audit
    from millicall.models import AuditLog

    sm = app.state.sessionmaker
    async with sm() as s:
        await record_audit(s, actor_user_id=None, actor_label="system", action="test.event")
        await s.commit()
        result = await s.scalar(select(AuditLog).where(AuditLog.action == "test.event"))
    assert result is not None
    assert result.actor_label == "system"


async def test_login_success_creates_audit(client, app, user_factory):
    """ログイン成功時にlogin.successイベントが記録される。"""
    from sqlalchemy import select

    from millicall.models import AuditLog

    username, password = await user_factory(username="audituser", password="Audit123!")
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200
    sm = app.state.sessionmaker
    async with sm() as s:
        log = await s.scalar(
            select(AuditLog)
            .where(AuditLog.action == "login.success")
            .where(AuditLog.actor_label == username)
        )
    assert log is not None
    assert log.actor_user_id is not None


async def test_login_failure_creates_audit(client, app):
    """ログイン失敗時にlogin.failureイベントが記録され、パスワードが含まれない。"""
    from sqlalchemy import select

    from millicall.models import AuditLog

    resp = await client.post(
        "/api/auth/login", json={"username": "nouser", "password": "wrongpw"}
    )
    assert resp.status_code == 401
    sm = app.state.sessionmaker
    async with sm() as s:
        log = await s.scalar(
            select(AuditLog)
            .where(AuditLog.action == "login.failure")
            .where(AuditLog.actor_label == "nouser")
        )
    assert log is not None
    assert log.actor_user_id is None
    # パスワードがdetailに含まれていないことを確認
    if log.detail:
        assert "wrongpw" not in log.detail


async def test_logout_creates_audit(client, app, user_factory):
    """ログアウト時にlogoutイベントが記録される。"""
    from sqlalchemy import select

    from millicall.models import AuditLog

    username, password = await user_factory()
    await client.post("/api/auth/login", json={"username": username, "password": password})
    await client.post("/api/auth/logout")
    sm = app.state.sessionmaker
    async with sm() as s:
        log = await s.scalar(select(AuditLog).where(AuditLog.action == "logout"))
    assert log is not None


async def test_audit_list_admin_only(client, user_factory):
    """GET /api/audit は管理者専用（非管理者は403）。"""
    username, password = await user_factory(
        username="regular", password="User123!", role="user"
    )
    await client.post("/api/auth/login", json={"username": username, "password": password})
    resp = await client.get("/api/audit")
    assert resp.status_code == 403


async def test_audit_list_admin_returns_newest_first(client, user_factory):
    """GET /api/audit は新しい順で返す。"""
    username, password = await user_factory()
    await client.post("/api/auth/login", json={"username": username, "password": password})
    resp = await client.get("/api/audit")
    assert resp.status_code == 200
    logs = resp.json()
    if len(logs) >= 2:
        from datetime import datetime

        dates = [datetime.fromisoformat(entry["created_at"]) for entry in logs]
        assert dates == sorted(dates, reverse=True)
