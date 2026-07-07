"""ログイン試行レート制限・ロックアウトのテスト（Phase 6 Task 3）。

カバレッジ:
  - 同一ユーザー名の N 回失敗 → 429 (Retry-After ヘッダー付き)
  - 同一 IP の N 回失敗 → 429
  - 成功でカウンタがリセットされる
  - /login/totp と /totp/verify もスロットリング対象（H-2）
  - ロックアウト時に audit ログが記録される
"""
import pyotp
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from millicall.auth.security import hash_password
from millicall.config import Settings
from millicall.main import create_app
from millicall.models import AuditLog, LoginAttempt, User
from tests.conftest import CsrfAwareClient

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


async def _make_app(tmp_path, max_attempts: int = 3, lockout_seconds: int = 60):
    """テスト用に max_attempts を小さい値に設定した app を生成する。"""
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        login_max_attempts=max_attempts,
        login_lockout_seconds=lockout_seconds,
    )
    return create_app(settings)


async def _create_user(app, username="throttleuser", password="Passw0rd1"):
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


# ---------------------------------------------------------------------------
# /api/auth/login のスロットリング
# ---------------------------------------------------------------------------


async def test_username_lockout_after_max_attempts(tmp_path):
    """同一ユーザー名の連続失敗が max_attempts に達すると 429 を返す。"""
    app = await _make_app(tmp_path, max_attempts=3)
    async with app.router.lifespan_context(app):
        await _create_user(app, username="lu1", password="Passw0rd1")
        from httpx import ASGITransport

        transport = ASGITransport(app=app)
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            # 3 回失敗
            for _ in range(3):
                resp = await c.post(
                    "/api/auth/login", json={"username": "lu1", "password": "wrong"}
                )
                assert resp.status_code == 401

            # 4 回目は 429
            resp = await c.post(
                "/api/auth/login", json={"username": "lu1", "password": "wrong"}
            )
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers


async def test_retry_after_header_present(tmp_path):
    """429 レスポンスに Retry-After ヘッダーが含まれる。"""
    app = await _make_app(tmp_path, max_attempts=2, lockout_seconds=120)
    async with app.router.lifespan_context(app):
        await _create_user(app, username="lu2", password="Passw0rd1")
        from httpx import ASGITransport

        transport = ASGITransport(app=app)
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            for _ in range(2):
                await c.post("/api/auth/login", json={"username": "lu2", "password": "bad"})
            resp = await c.post("/api/auth/login", json={"username": "lu2", "password": "bad"})
            assert resp.status_code == 429
            # lockout_seconds の値が返っていることを確認
            assert resp.headers.get("Retry-After") == "120"


async def test_success_clears_failure_counter(tmp_path):
    """ログイン成功でカウンタがリセットされ、再び試行できる。"""
    app = await _make_app(tmp_path, max_attempts=3)
    async with app.router.lifespan_context(app):
        await _create_user(app, username="lu3", password="Passw0rd1")
        from httpx import ASGITransport

        transport = ASGITransport(app=app)
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            # 2 回失敗（上限未満）
            for _ in range(2):
                await c.post("/api/auth/login", json={"username": "lu3", "password": "bad"})

            # 成功でリセット
            resp = await c.post(
                "/api/auth/login", json={"username": "lu3", "password": "Passw0rd1"}
            )
            assert resp.status_code == 200

            # 再び 2 回失敗しても 429 にならない（カウンタがリセットされているため）
            for _ in range(2):
                resp = await c.post(
                    "/api/auth/login", json={"username": "lu3", "password": "bad"}
                )
                assert resp.status_code == 401


async def test_lockout_audit_recorded(tmp_path):
    """ロックアウト発生時に audit ログ "login.lockout" が記録される。"""
    app = await _make_app(tmp_path, max_attempts=2)
    async with app.router.lifespan_context(app):
        await _create_user(app, username="lu4", password="Passw0rd1")
        from httpx import ASGITransport

        transport = ASGITransport(app=app)
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            for _ in range(2):
                await c.post("/api/auth/login", json={"username": "lu4", "password": "bad"})
            # ロックアウトを発動させる
            await c.post("/api/auth/login", json={"username": "lu4", "password": "bad"})

        sm = app.state.sessionmaker
        async with sm() as s:
            count = await s.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "login.lockout")
            )
        assert count and count >= 1


async def test_nonexistent_user_also_rate_limited(tmp_path):
    """存在しないユーザー名でも失敗として記録され、ロックアウトが発動する。"""
    app = await _make_app(tmp_path, max_attempts=3)
    async with app.router.lifespan_context(app):
        from httpx import ASGITransport

        transport = ASGITransport(app=app)
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            for _ in range(3):
                await c.post(
                    "/api/auth/login",
                    json={"username": "ghostuser", "password": "nope"},
                )
            resp = await c.post(
                "/api/auth/login",
                json={"username": "ghostuser", "password": "nope"},
            )
            assert resp.status_code == 429


async def test_login_attempt_rows_inserted_on_failure(tmp_path):
    """失敗時に login_attempts テーブルに行が挿入される。"""
    app = await _make_app(tmp_path, max_attempts=10)
    async with app.router.lifespan_context(app):
        await _create_user(app, username="lu5", password="Passw0rd1")
        from httpx import ASGITransport

        transport = ASGITransport(app=app)
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            await c.post("/api/auth/login", json={"username": "lu5", "password": "bad"})
            await c.post("/api/auth/login", json={"username": "lu5", "password": "bad"})

        sm = app.state.sessionmaker
        async with sm() as s:
            count = await s.scalar(
                select(func.count()).select_from(LoginAttempt).where(
                    LoginAttempt.username == "lu5"
                )
            )
        # 2 回の失敗 × 2 行（username キーと IP キー）
        assert count and count >= 2


# ---------------------------------------------------------------------------
# /api/auth/login/totp のスロットリング（H-2）
# ---------------------------------------------------------------------------


async def _setup_totp_user(app, username="tl_user", password="Passw0rd1"):
    """TOTP 有効ユーザーを作成し、secret を返す。"""
    await _create_user(app, username=username, password=password)
    from httpx import ASGITransport

    transport = ASGITransport(app=app)
    async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/auth/login", json={"username": username, "password": password})
        setup = await c.post("/api/auth/totp/setup")
        assert setup.status_code == 200
        secret = setup.json()["secret"]
        code = pyotp.TOTP(secret).now()
        verify = await c.post("/api/auth/totp/verify", json={"code": code})
        assert verify.status_code == 200
    return secret


async def test_login_totp_endpoint_rate_limited(tmp_path):
    """POST /login/totp の TOTP コード失敗が max_attempts に達すると 429 を返す。"""
    app = await _make_app(tmp_path, max_attempts=3)
    async with app.router.lifespan_context(app):
        await _setup_totp_user(app, username="lt1", password="Passw0rd1!")
        from httpx import ASGITransport

        transport = ASGITransport(app=app)
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            # チケットを取得
            login_resp = await c.post(
                "/api/auth/login", json={"username": "lt1", "password": "Passw0rd1!"}
            )
            assert login_resp.status_code == 200
            ticket = login_resp.json()["ticket"]

            # 間違ったコードで 3 回失敗
            for _ in range(3):
                resp = await c.post(
                    "/api/auth/login/totp", json={"ticket": ticket, "code": "000000"}
                )
                assert resp.status_code == 401

            # 4 回目は 429
            resp = await c.post(
                "/api/auth/login/totp", json={"ticket": ticket, "code": "000000"}
            )
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers


async def test_totp_verify_endpoint_rate_limited(tmp_path):
    """POST /totp/verify の失敗が max_attempts に達すると 429 を返す。"""
    app = await _make_app(tmp_path, max_attempts=3)
    async with app.router.lifespan_context(app):
        await _create_user(app, username="tv1", password="Passw0rd1!")
        from httpx import ASGITransport

        transport = ASGITransport(app=app)
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            await c.post("/api/auth/login", json={"username": "tv1", "password": "Passw0rd1!"})
            # TOTP setup のみ（verify 前）
            setup = await c.post("/api/auth/totp/setup")
            assert setup.status_code == 200

            # 誤ったコードで 3 回失敗
            for _ in range(3):
                resp = await c.post("/api/auth/totp/verify", json={"code": "000000"})
                # 失敗は 400 または 429
                assert resp.status_code in (400, 429)

            # 4 回目以降は 429
            resp = await c.post("/api/auth/totp/verify", json={"code": "000000"})
            assert resp.status_code == 429


async def test_totp_disable_endpoint_rate_limited(tmp_path):
    """POST /totp/disable の失敗が max_attempts に達すると 429 を返す。"""
    app = await _make_app(tmp_path, max_attempts=3)
    async with app.router.lifespan_context(app):
        secret = await _setup_totp_user(app, username="td1", password="Passw0rd1!")
        from httpx import ASGITransport

        transport = ASGITransport(app=app)
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            # TOTP 有効ユーザー: 2 段階ログインでセッションを取得する
            login_resp = await c.post(
                "/api/auth/login", json={"username": "td1", "password": "Passw0rd1!"}
            )
            assert login_resp.status_code == 200
            ticket = login_resp.json()["ticket"]
            code = pyotp.TOTP(secret).now()
            totp_resp = await c.post(
                "/api/auth/login/totp", json={"ticket": ticket, "code": code}
            )
            assert totp_resp.status_code == 200

            # 誤ったコードで 3 回失敗
            for _ in range(3):
                resp = await c.post("/api/auth/totp/disable", json={"code": "000000"})
                assert resp.status_code in (400, 429)

            # 4 回目以降は 429
            resp = await c.post("/api/auth/totp/disable", json={"code": "000000"})
            assert resp.status_code == 429


# ---------------------------------------------------------------------------
# IP・ユーザー名しきい値の分離（レビュー H-1 回帰）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_username_threshold_decoupled_from_ip(app):
    """ip=None のとき、IP しきい値(10)ではロックされず、ユーザー名しきい値(30)でロックされる。

    これにより単一 IP の攻撃者（IP キーが先に 10 でロック）が正規アカウントを
    容易に DoS ロックアウトできないことを担保する。
    """
    from millicall.auth.throttle import check_and_raise, record_failure

    async with app.state.sessionmaker() as s:
        # 15 回失敗（username キーのみ）。IP しきい値 10 は超えるが username しきい値 30 未満。
        for _ in range(15):
            await record_failure(s, ip=None, username="victim", action="login")
        await s.commit()

        # username=victim, ip=None → username_count=15 < 30 なので通過（例外なし）
        await check_and_raise(
            s, ip=None, username="victim",
            ip_max_attempts=10, username_max_attempts=30, lockout_seconds=300,
        )

        # さらに 20 回（計 35）失敗させると username しきい値 30 を超えてロック
        for _ in range(20):
            await record_failure(s, ip=None, username="victim", action="login")
        await s.commit()
        with pytest.raises(HTTPException) as ei:
            await check_and_raise(
                s, ip=None, username="victim",
                ip_max_attempts=10, username_max_attempts=30, lockout_seconds=300,
            )
        assert ei.value.status_code == 429
