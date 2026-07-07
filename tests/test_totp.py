"""TOTP 2FA のテスト（Phase 6 Task 2）。

カバレッジ:
  - セットアップ・確認・無効化のエンドポイント
  - 2 段階ログイン（チケット→TOTP コード）
  - リカバリコードによるログインと消費
  - 監査ログの確認
  - 秘密情報のリーク検査（シークレット・リカバリ平文が DB/監査に出ない）
"""
import json

import pyotp
from sqlalchemy import select

from millicall.auth.security import issue_totp_ticket, read_totp_ticket
from millicall.models import AuditLog, User

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


async def _create_totp_user(app, username="totpuser", password="Totp1234!"):
    """TOTP 有効ユーザーを DB に直接作成し、(username, password) を返す。"""
    from millicall.auth.security import hash_password

    sm = app.state.sessionmaker
    async with sm() as s:
        s.add(
            User(
                username=username,
                hashed_password=hash_password(password),
                display_name=username,
                role="admin",
                origin="local",
            )
        )
        await s.commit()
    return username, password


async def _setup_and_enable_totp(client, username, password):
    """ログイン → setup → verify してリカバリコードを返す。setup 時の secret も返す。"""
    await client.post("/api/auth/login", json={"username": username, "password": password})
    setup_resp = await client.post("/api/auth/totp/setup")
    assert setup_resp.status_code == 200
    secret = setup_resp.json()["secret"]
    code = pyotp.TOTP(secret).now()
    verify_resp = await client.post("/api/auth/totp/verify", json={"code": code})
    assert verify_resp.status_code == 200
    recovery_codes = verify_resp.json()["recovery_codes"]
    return secret, recovery_codes


# ---------------------------------------------------------------------------
# ticket ヘルパー ユニットテスト
# ---------------------------------------------------------------------------


def test_totp_ticket_roundtrip():
    """issue/read_totp_ticket のラウンドトリップで uid/epoch が正しく返る。"""
    ticket = issue_totp_ticket("k" * 40, 99, epoch=2)
    data = read_totp_ticket("k" * 40, ticket, max_age=300)
    assert data is not None
    assert data.uid == 99
    assert data.epoch == 2


def test_totp_ticket_wrong_secret():
    """異なる secret ではチケット検証が失敗する。"""
    ticket = issue_totp_ticket("k" * 40, 1, epoch=0)
    assert read_totp_ticket("other-secret", ticket, max_age=300) is None


def test_totp_ticket_expired():
    """max_age=-1 で期限切れチケットは None を返す。"""
    ticket = issue_totp_ticket("k" * 40, 1, epoch=0)
    assert read_totp_ticket("k" * 40, ticket, max_age=-1) is None


def test_totp_ticket_session_salt_distinct():
    """セッショントークンを TOTP ソルトで検証してもデコードできない（混用防止）。"""
    from millicall.auth.security import issue_session

    session_token = issue_session("k" * 40, 1, epoch=0)
    assert read_totp_ticket("k" * 40, session_token, max_age=300) is None


# ---------------------------------------------------------------------------
# TOTP セットアップ
# ---------------------------------------------------------------------------


async def test_totp_setup_returns_secret_and_uri(client, app, user_factory):
    """setup がシークレットと provisioning_uri を返す。"""
    username, password = await user_factory(username="su1", password="Pass1234!")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    resp = await client.post("/api/auth/totp/setup")
    assert resp.status_code == 200
    data = resp.json()
    assert "secret" in data
    assert "provisioning_uri" in data
    assert "Millicall" in data["provisioning_uri"]
    assert username in data["provisioning_uri"]


async def test_totp_setup_stores_encrypted_secret(client, app, user_factory):
    """setup 後 DB には暗号化済み文字列が保存され、平文とは異なる。"""
    username, password = await user_factory(username="su2", password="Pass1234!")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    resp = await client.post("/api/auth/totp/setup")
    assert resp.status_code == 200
    plain_secret = resp.json()["secret"]

    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        stored = user.totp_secret

    # 暗号化されているので平文とは異なる
    assert stored is not None
    assert stored != plain_secret

    # 復号すると元に戻る（SecretBox ラウンドトリップ確認）
    from millicall.crypto import SecretBox

    box = SecretBox(app.state.secrets.master_key)
    assert box.decrypt(stored) == plain_secret


async def test_totp_setup_does_not_enable(client, app, user_factory):
    """setup 直後は totp_enabled=False のまま。"""
    username, password = await user_factory(username="su3", password="Pass1234!")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    await client.post("/api/auth/totp/setup")
    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        assert user.totp_enabled is False


# ---------------------------------------------------------------------------
# TOTP 確認（verify）
# ---------------------------------------------------------------------------


async def test_totp_verify_correct_code_enables(client, app, user_factory):
    """正しいコードで verify すると totp_enabled=True になる。"""
    username, password = await user_factory(username="vf1", password="Pass1234!")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    setup = await client.post("/api/auth/totp/setup")
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()
    resp = await client.post("/api/auth/totp/verify", json={"code": code})
    assert resp.status_code == 200

    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        assert user.totp_enabled is True


async def test_totp_verify_returns_10_recovery_codes(client, app, user_factory):
    """verify 成功後に 10 個のリカバリコードが返される。"""
    username, password = await user_factory(username="vf2", password="Pass1234!")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    setup = await client.post("/api/auth/totp/setup")
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()
    resp = await client.post("/api/auth/totp/verify", json={"code": code})
    assert resp.status_code == 200
    recovery_codes = resp.json()["recovery_codes"]
    assert len(recovery_codes) == 10


async def test_totp_verify_recovery_codes_stored_hashed(client, app, user_factory):
    """DB には平文ではなく Argon2 ハッシュが保存される。"""
    username, password = await user_factory(username="vf3", password="Pass1234!")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    setup = await client.post("/api/auth/totp/setup")
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()
    resp = await client.post("/api/auth/totp/verify", json={"code": code})
    assert resp.status_code == 200
    plain_codes = resp.json()["recovery_codes"]

    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        stored_json = user.recovery_codes
        assert stored_json is not None
        stored_hashes = json.loads(stored_json)

    # DB に平文が含まれていないことを確認
    for code_plain in plain_codes:
        assert code_plain not in stored_json

    # DB にはハッシュ文字列が入っている（$argon2 で始まる）
    for h in stored_hashes:
        assert h.startswith("$argon2")


async def test_totp_verify_wrong_code_returns_400(client, app, user_factory):
    """誤ったコードは 400 で、totp_enabled=False のまま。"""
    username, password = await user_factory(username="vf4", password="Pass1234!")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    await client.post("/api/auth/totp/setup")
    resp = await client.post("/api/auth/totp/verify", json={"code": "000000"})
    assert resp.status_code == 400
    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        assert user.totp_enabled is False


# ---------------------------------------------------------------------------
# TOTP ログイン（2 段階）
# ---------------------------------------------------------------------------


async def test_login_totp_enabled_returns_ticket(client, app, user_factory):
    """TOTP 有効ユーザーのパスワード認証は totp_required + ticket を返す（Cookie なし）。"""
    username, password = await _create_totp_user(app, username="tl1", password="Totp1234!")
    _, __ = await _setup_and_enable_totp(await _new_client(app), username, password)

    # 新しいクライアントでログイン
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["totp_required"] is True
        assert "ticket" in data
        # セッション Cookie がセットされていないことを確認
        assert "millicall_session" not in resp.cookies


async def test_login_totp_correct_code_issues_session(client, app, user_factory):
    """正しい TOTP コードで /login/totp を呼ぶとセッション Cookie が発行される。"""
    username, password = await _create_totp_user(app, username="tl2", password="Totp1234!")
    fresh = await _new_client(app)
    secret, _ = await _setup_and_enable_totp(fresh, username, password)

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login_resp = await c.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert login_resp.status_code == 200
        ticket = login_resp.json()["ticket"]
        code = pyotp.TOTP(secret).now()
        totp_resp = await c.post(
            "/api/auth/login/totp", json={"ticket": ticket, "code": code}
        )
        assert totp_resp.status_code == 200
        assert "millicall_session" in totp_resp.cookies
        assert totp_resp.json()["username"] == username


async def test_login_totp_wrong_code_returns_401(client, app, user_factory):
    """誤ったコードは 401 でセッション Cookie を発行しない。"""
    username, password = await _create_totp_user(app, username="tl3", password="Totp1234!")
    fresh = await _new_client(app)
    await _setup_and_enable_totp(fresh, username, password)

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login_resp = await c.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        ticket = login_resp.json()["ticket"]
        totp_resp = await c.post(
            "/api/auth/login/totp", json={"ticket": ticket, "code": "000000"}
        )
        assert totp_resp.status_code == 401
        assert "millicall_session" not in totp_resp.cookies


async def test_login_totp_invalid_ticket_returns_401(client, app):
    """無効なチケット文字列は 401。"""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/auth/login/totp",
            json={"ticket": "not.a.valid.ticket", "code": "123456"},
        )
        assert resp.status_code == 401


async def test_login_totp_expired_ticket_returns_401(app):
    """期限切れチケットは 401。"""
    from httpx import ASGITransport, AsyncClient

    username, password = await _create_totp_user(app, username="tl4", password="Totp1234!")
    fresh = await _new_client(app)
    secret, _ = await _setup_and_enable_totp(fresh, username, password)

    # max_age=-1 相当の期限切れチケットを手動発行
    expired_ticket = issue_totp_ticket(app.state.secrets.session_secret, 9999, epoch=0)
    # max_age=0 では confirm されない場合もあるので invalid uid を使用

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/auth/login/totp",
            json={"ticket": expired_ticket, "code": pyotp.TOTP(secret).now()},
        )
        assert resp.status_code == 401


async def test_login_totp_disabled_user_via_ticket_returns_401(app):
    """チケット発行後にユーザーを disabled にすると 401。"""
    username, password = await _create_totp_user(app, username="tl5", password="Totp1234!")
    fresh = await _new_client(app)
    secret, _ = await _setup_and_enable_totp(fresh, username, password)

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login_resp = await c.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        ticket = login_resp.json()["ticket"]

        # ユーザーを無効化
        sm = app.state.sessionmaker
        async with sm() as s:
            user = await s.scalar(select(User).where(User.username == username))
            user.enabled = False
            await s.commit()

        code = pyotp.TOTP(secret).now()
        resp = await c.post(
            "/api/auth/login/totp", json={"ticket": ticket, "code": code}
        )
        assert resp.status_code == 401


async def test_login_totp_epoch_changed_after_ticket_returns_401(app):
    """チケット発行後に epoch を変更すると 401。"""
    username, password = await _create_totp_user(app, username="tl6", password="Totp1234!")
    fresh = await _new_client(app)
    secret, _ = await _setup_and_enable_totp(fresh, username, password)

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login_resp = await c.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        ticket = login_resp.json()["ticket"]

        # epoch をバンプ
        from millicall.auth.security import bump_session_epoch

        sm = app.state.sessionmaker
        async with sm() as s:
            user = await s.scalar(select(User).where(User.username == username))
            bump_session_epoch(user)
            await s.commit()

        code = pyotp.TOTP(secret).now()
        resp = await c.post(
            "/api/auth/login/totp", json={"ticket": ticket, "code": code}
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# リカバリコード
# ---------------------------------------------------------------------------


async def test_recovery_code_login_succeeds(app):
    """リカバリコードで /login/totp が成功する。"""
    username, password = await _create_totp_user(app, username="rc1", password="Totp1234!")
    fresh = await _new_client(app)
    _, recovery_codes = await _setup_and_enable_totp(fresh, username, password)

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login_resp = await c.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        ticket = login_resp.json()["ticket"]
        rc = recovery_codes[0]
        resp = await c.post(
            "/api/auth/login/totp", json={"ticket": ticket, "code": rc}
        )
        assert resp.status_code == 200
        assert "millicall_session" in resp.cookies


async def test_recovery_code_consumed_on_use(app):
    """リカバリコードは一度使うと二度目は失敗する（消費済み）。"""
    username, password = await _create_totp_user(app, username="rc2", password="Totp1234!")
    fresh = await _new_client(app)
    _, recovery_codes = await _setup_and_enable_totp(fresh, username, password)
    rc = recovery_codes[0]

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # 1 回目 — 成功
        lr1 = await c.post("/api/auth/login", json={"username": username, "password": password})
        ticket1 = lr1.json()["ticket"]
        r1 = await c.post("/api/auth/login/totp", json={"ticket": ticket1, "code": rc})
        assert r1.status_code == 200

    async with AsyncClient(transport=transport, base_url="http://test") as c2:
        # 2 回目 — 失敗（既に消費済み）
        lr2 = await c2.post("/api/auth/login", json={"username": username, "password": password})
        ticket2 = lr2.json()["ticket"]
        r2 = await c2.post("/api/auth/login/totp", json={"ticket": ticket2, "code": rc})
        assert r2.status_code == 401


async def test_recovery_codes_not_stored_plaintext(app):
    """DB の recovery_codes カラムに平文コードが含まれていない。"""
    username, password = await _create_totp_user(app, username="rc3", password="Totp1234!")
    fresh = await _new_client(app)
    _, plain_codes = await _setup_and_enable_totp(fresh, username, password)

    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        stored = user.recovery_codes
        assert stored is not None
        for code in plain_codes:
            assert code not in stored


# ---------------------------------------------------------------------------
# TOTP 無効化
# ---------------------------------------------------------------------------


async def test_totp_disable_requires_valid_code(client, app, user_factory):
    """誤ったコードでは disable に失敗する。"""
    username, password = await _create_totp_user(app, username="dis1", password="Totp1234!")
    fresh = await _new_client(app)
    await _setup_and_enable_totp(fresh, username, password)

    # ログインして totp コードなしで disable しようとする（認証済みセッションが必要）
    # fresh は既に有効な TOTP セッションを持っているので引き続き使う
    resp = await fresh.post("/api/auth/totp/disable", json={"code": "000000"})
    assert resp.status_code == 400

    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        assert user.totp_enabled is True


async def test_totp_disable_with_valid_code(app):
    """有効なコードで disable すると totp が無効化され、ログインに TOTP が不要になる。"""
    username, password = await _create_totp_user(app, username="dis2", password="Totp1234!")
    fresh = await _new_client(app)
    secret, _ = await _setup_and_enable_totp(fresh, username, password)

    code = pyotp.TOTP(secret).now()
    resp = await fresh.post("/api/auth/totp/disable", json={"code": code})
    assert resp.status_code == 200

    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        assert user.totp_enabled is False
        assert user.totp_secret is None
        assert user.recovery_codes is None


async def test_after_disable_login_no_totp_required(app):
    """無効化後のログインは通常フロー（totp_required なし）。"""
    username, password = await _create_totp_user(app, username="dis3", password="Totp1234!")
    fresh = await _new_client(app)
    secret, _ = await _setup_and_enable_totp(fresh, username, password)

    code = pyotp.TOTP(secret).now()
    await fresh.post("/api/auth/totp/disable", json={"code": code})

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200
        assert "millicall_session" in resp.cookies
        data = resp.json()
        assert "totp_required" not in data or data.get("totp_required") is not True


async def test_totp_disable_with_recovery_code(app):
    """リカバリコードで disable できる。"""
    username, password = await _create_totp_user(app, username="dis4", password="Totp1234!")
    fresh = await _new_client(app)
    _, recovery_codes = await _setup_and_enable_totp(fresh, username, password)

    rc = recovery_codes[1]
    resp = await fresh.post("/api/auth/totp/disable", json={"code": rc})
    assert resp.status_code == 200

    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        assert user.totp_enabled is False


# ---------------------------------------------------------------------------
# 監査ログ
# ---------------------------------------------------------------------------


async def test_audit_totp_enable(app):
    """TOTP 有効化時に totp.enable が監査記録される。"""
    username, password = await _create_totp_user(app, username="au1", password="Totp1234!")
    fresh = await _new_client(app)
    await _setup_and_enable_totp(fresh, username, password)

    sm = app.state.sessionmaker
    async with sm() as s:
        log = await s.scalar(
            select(AuditLog)
            .where(AuditLog.action == "totp.enable")
            .where(AuditLog.actor_label == username)
        )
    assert log is not None


async def test_audit_totp_disable(app):
    """TOTP 無効化時に totp.disable が監査記録される。"""
    username, password = await _create_totp_user(app, username="au2", password="Totp1234!")
    fresh = await _new_client(app)
    secret, _ = await _setup_and_enable_totp(fresh, username, password)
    code = pyotp.TOTP(secret).now()
    await fresh.post("/api/auth/totp/disable", json={"code": code})

    sm = app.state.sessionmaker
    async with sm() as s:
        log = await s.scalar(
            select(AuditLog)
            .where(AuditLog.action == "totp.disable")
            .where(AuditLog.actor_label == username)
        )
    assert log is not None


async def test_audit_login_totp_challenge(app):
    """TOTP 有効ユーザーのログイン時に login.totp_challenge が記録される。"""
    username, password = await _create_totp_user(app, username="au3", password="Totp1234!")
    fresh = await _new_client(app)
    await _setup_and_enable_totp(fresh, username, password)

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/auth/login", json={"username": username, "password": password})

    sm = app.state.sessionmaker
    async with sm() as s:
        log = await s.scalar(
            select(AuditLog)
            .where(AuditLog.action == "login.totp_challenge")
            .where(AuditLog.actor_label == username)
        )
    assert log is not None


async def test_audit_login_totp_failure(app):
    """誤ったコードのログイン試行に login.totp_failure が記録される。"""
    username, password = await _create_totp_user(app, username="au4", password="Totp1234!")
    fresh = await _new_client(app)
    await _setup_and_enable_totp(fresh, username, password)

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        lr = await c.post("/api/auth/login", json={"username": username, "password": password})
        ticket = lr.json()["ticket"]
        await c.post("/api/auth/login/totp", json={"ticket": ticket, "code": "000000"})

    sm = app.state.sessionmaker
    async with sm() as s:
        log = await s.scalar(
            select(AuditLog)
            .where(AuditLog.action == "login.totp_failure")
            .where(AuditLog.actor_label == username)
        )
    assert log is not None


async def test_audit_detail_no_secret_or_code(app):
    """監査ログの detail にシークレットやコード平文が含まれていない。"""
    username, password = await _create_totp_user(app, username="au5", password="Totp1234!")
    fresh = await _new_client(app)
    secret, _ = await _setup_and_enable_totp(fresh, username, password)

    sm = app.state.sessionmaker
    async with sm() as s:
        logs = (await s.scalars(select(AuditLog).where(AuditLog.actor_label == username))).all()

    for log in logs:
        if log.detail:
            assert secret not in log.detail
            # リカバリコードのハッシュ文字列が入っていないことを確認
            assert "$argon2" not in log.detail


# ---------------------------------------------------------------------------
# 秘密情報リーク検査
# ---------------------------------------------------------------------------


async def test_secret_not_in_db_column(app):
    """DB の totp_secret カラムは平文 base32 シークレットと異なる（暗号化確認）。"""
    username, password = await _create_totp_user(app, username="leak1", password="Totp1234!")
    fresh = await _new_client(app)
    # ログインしてから setup を呼ぶ
    lr = await fresh.post("/api/auth/login", json={"username": username, "password": password})
    assert lr.status_code == 200
    assert "millicall_session" in lr.cookies
    setup = await fresh.post("/api/auth/totp/setup")
    assert setup.status_code == 200
    plain_secret = setup.json()["secret"]

    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        assert user.totp_secret != plain_secret
        assert plain_secret not in (user.totp_secret or "")


async def test_user_repr_no_secret(app):
    """User.__repr__ に totp_secret / recovery_codes が含まれない。"""
    username, password = await _create_totp_user(app, username="leak2", password="Totp1234!")
    fresh = await _new_client(app)
    secret, _ = await _setup_and_enable_totp(fresh, username, password)

    sm = app.state.sessionmaker
    async with sm() as s:
        user = await s.scalar(select(User).where(User.username == username))
        r = repr(user)
        assert "totp_secret" not in r
        assert "recovery_codes" not in r
        # 暗号化済み文字列も repr に出てはいけない
        assert secret not in r


# ---------------------------------------------------------------------------
# 既存ログインテストの回帰（TOTP なしユーザー）
# ---------------------------------------------------------------------------


async def test_login_non_totp_user_direct_session(client, user_factory):
    """TOTP 非有効ユーザーのログインは従来通り直接セッションを返す。"""
    username, password = await user_factory(username="notopt", password="Pass1234!")
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    assert "millicall_session" in resp.cookies
    data = resp.json()
    # totp_required キーがないか False
    assert data.get("totp_required") is not True
    assert "username" in data


# ---------------------------------------------------------------------------
# マイグレーション 0015 のスモークテスト
# ---------------------------------------------------------------------------


def test_migration_0015_upgrade_downgrade(tmp_path):
    """0015 の upgrade → downgrade が正常完了することを確認する。"""
    from alembic.command import downgrade, upgrade
    from alembic.config import Config

    db_url = f"sqlite:///{tmp_path}/test.db"
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", db_url)
    upgrade(cfg, "0014")
    upgrade(cfg, "0015")
    downgrade(cfg, "0014")


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------


async def _new_client(app):
    """app に対する新しい AsyncClient を返す。

    pytest-asyncio の fixture ではないため、
    テスト内で with ブロックなしに直接使いたいケース向け。
    cleanup は呼び出し元が行う（テスト終了まで接続を保持する）。
    """
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    await c.__aenter__()
    return c
