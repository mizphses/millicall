"""認証の Argon2・AI プロバイダ生成の DNS がイベントループを止めないことのテスト（issue #81）。

カバレッジ:
  - run_argon2 は専用スレッドで実行され、計算中もループが進む
  - Argon2 の同時計算数が上限（_ARGON2_MAX_WORKERS）を超えない
  - 形式不正のリカバリコードでは Argon2 を回さない / 形式正しい不一致では回す
  - /login/totp で形式不正と不一致が外部から区別できない（同じ 401・失敗記録・監査）
  - /login・SCIM Bearer 検証・build_llm_async/build_tts_async がループを止めない
  - 同期版 _verify_code（mcp_server/login.py が使用）が coroutine を返さない
"""

import asyncio
import socket
import threading
import time
from unittest.mock import patch

import pyotp
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from millicall.auth import hashing
from millicall.auth.security import hash_password
from millicall.models import AppSetting, AuditLog, LoginAttempt, User

# ループが止まったとみなすしきい値。疑似の重い処理は 0.3 秒ブロックするため、
# ループ上で実行されていればティッカーの最大間隔は 0.3 秒以上になる。
_BLOCK_SECONDS = 0.3
_MAX_ALLOWED_GAP = 0.15


async def _run_with_ticker(coro):
    """coro を実行しつつ 5ms 周期のティッカーを回し、(結果, ティック最大間隔) を返す。"""
    gaps: list[float] = []
    done = asyncio.Event()

    async def _ticker() -> None:
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(_ticker())
    await asyncio.sleep(0)  # ティッカーを先に走らせる
    try:
        result = await coro
    finally:
        done.set()
        await task
    return result, max(gaps)


# ---------------------------------------------------------------------------
# hashing ヘルパ単体
# ---------------------------------------------------------------------------


async def test_run_argon2_runs_on_dedicated_thread():
    name = await hashing.run_argon2(lambda: threading.current_thread().name)
    assert name.startswith("argon2")


async def test_run_argon2_does_not_block_loop():
    _, max_gap = await _run_with_ticker(hashing.run_argon2(time.sleep, _BLOCK_SECONDS))
    assert max_gap < _MAX_ALLOWED_GAP


async def test_run_argon2_concurrency_is_bounded():
    """同時に大量投入しても同時実行数は _ARGON2_MAX_WORKERS を超えない。"""
    lock = threading.Lock()
    active = 0
    peak = 0

    def _job() -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1

    await asyncio.gather(*(hashing.run_argon2(_job) for _ in range(8)))
    assert peak == hashing._ARGON2_MAX_WORKERS


async def test_cancelled_waiter_does_not_compute():
    """上限待ちのままキャンセルされた要求は計算されない（無駄な Argon2 を積まない）。"""
    release = threading.Event()
    ran: list[int] = []

    def _blocker() -> None:
        release.wait(5)

    def _job(i: int) -> None:
        ran.append(i)

    blockers = [
        asyncio.ensure_future(hashing.run_argon2(_blocker))
        for _ in range(hashing._ARGON2_MAX_WORKERS)
    ]
    await asyncio.sleep(0.05)  # 全ワーカーを塞ぐ
    waiter = asyncio.ensure_future(hashing.run_argon2(_job, 1))
    await asyncio.sleep(0.01)
    waiter.cancel()
    await asyncio.sleep(0)  # キャンセルを concurrent future へ伝播させてから解放する
    release.set()
    await asyncio.gather(*blockers)
    await hashing.run_argon2(_job, 2)  # キュー消化を待つ
    assert ran == [2]


def test_recovery_code_format():
    assert hashing.is_recovery_code_format("0a1b2-c3d4e-5f601-23456")
    assert not hashing.is_recovery_code_format("123456")  # TOTP コード
    assert not hashing.is_recovery_code_format("0A1B2-C3D4E-5F601-23456")  # 大文字
    assert not hashing.is_recovery_code_format("0a1b2c3d4e5f60123456")  # ハイフン無し
    assert not hashing.is_recovery_code_format("0a1b2-c3d4e-5f601-23456\n")
    assert not hashing.is_recovery_code_format("")


async def test_generated_recovery_codes_match_format():
    plain, hashed = await hashing.generate_recovery_codes()
    assert len(plain) == len(hashed) == hashing.RECOVERY_CODE_COUNT
    assert all(hashing.is_recovery_code_format(c) for c in plain)
    assert await hashing.find_recovery_code(hashed, plain[3]) == 3


async def test_malformed_recovery_code_skips_argon2():
    stored = [hash_password("aaaaa-aaaaa-aaaaa-aaaaa")] * 3
    with patch.object(hashing, "_verify_or_false", wraps=hashing._verify_or_false) as spy:
        assert await hashing.find_recovery_code(stored, "123456") is None
        assert hashing.find_recovery_code_sync(stored, "123456") is None
        assert spy.call_count == 0
        # 形式は正しいが不一致 → 全件を検証する
        assert await hashing.find_recovery_code(stored, "bbbbb-bbbbb-bbbbb-bbbbb") is None
        assert spy.call_count == 3


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


async def _create_local_user(app, username: str, password: str) -> None:
    async with app.state.sessionmaker() as s:
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


def _slow(fn):
    """fn の前に _BLOCK_SECONDS だけ同期ブロックする版を返す（重い Argon2/DNS の代用）。"""

    def _wrapped(*args, **kwargs):
        time.sleep(_BLOCK_SECONDS)
        return fn(*args, **kwargs)

    return _wrapped


async def test_login_does_not_block_loop(app):
    await _create_local_user(app, "offload1", "Passw0rd1")
    slow_verify = _slow(hashing.verify_password)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch.object(hashing, "verify_password", slow_verify):
            resp, max_gap = await _run_with_ticker(
                c.post("/api/auth/login", json={"username": "offload1", "password": "Passw0rd1"})
            )
    assert resp.status_code == 200
    assert max_gap < _MAX_ALLOWED_GAP


async def test_login_unknown_user_still_runs_dummy_hash_off_loop(app):
    """存在しないユーザーでもダミーハッシュ検証が（ループ外で）走る（タイミングガード維持）。"""
    transport = ASGITransport(app=app)
    with patch.object(hashing, "verify_password", wraps=hashing.verify_password) as spy:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert resp.status_code == 401
    assert spy.call_count == 1


async def _enable_totp(app, username: str, password: str) -> list[str]:
    from tests.conftest import CsrfAwareClient

    transport = ASGITransport(app=app)
    async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/auth/login", json={"username": username, "password": password})
        secret = (await c.post("/api/auth/totp/setup")).json()["secret"]
        resp = await c.post("/api/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()})
        assert resp.status_code == 200
        return resp.json()["recovery_codes"]


async def _count(app, model, *conds) -> int:
    async with app.state.sessionmaker() as s:
        return await s.scalar(select(func.count()).select_from(model).where(*conds)) or 0


async def test_login_totp_malformed_and_mismatch_are_indistinguishable(app):
    """形式不正（Argon2 なし）と形式正しい不一致（Argon2 あり）で外部から見える結果が同じ。"""
    await _create_local_user(app, "offload2", "Totp1234!")
    await _enable_totp(app, "offload2", "Totp1234!")

    transport = ASGITransport(app=app)
    results = []
    for code in ("123456", "fffff-fffff-fffff-fffff"):  # 形式不正 / 形式正しい不一致
        before_fail = await _count(app, LoginAttempt, LoginAttempt.key == "offload2")
        before_audit = await _count(app, AuditLog, AuditLog.action == "login.totp_failure")
        with patch.object(hashing, "_verify_or_false", wraps=hashing._verify_or_false) as spy:
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                lr = await c.post(
                    "/api/auth/login", json={"username": "offload2", "password": "Totp1234!"}
                )
                ticket = lr.json()["ticket"]
                resp = await c.post("/api/auth/login/totp", json={"ticket": ticket, "code": code})
        results.append(
            (
                resp.status_code,
                resp.json(),
                await _count(app, LoginAttempt, LoginAttempt.key == "offload2") - before_fail,
                await _count(app, AuditLog, AuditLog.action == "login.totp_failure") - before_audit,
            )
        )
        if code == "123456":
            assert spy.call_count == 0  # 形式不正では Argon2 を回さない
        else:
            assert spy.call_count == hashing.RECOVERY_CODE_COUNT

    assert results[0] == results[1]
    assert results[0][0] == 401
    assert results[0][2] == 1
    assert results[0][3] == 1


async def test_login_totp_recovery_code_does_not_block_loop(app):
    await _create_local_user(app, "offload3", "Totp1234!")
    codes = await _enable_totp(app, "offload3", "Totp1234!")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        lr = await c.post("/api/auth/login", json={"username": "offload3", "password": "Totp1234!"})
        ticket = lr.json()["ticket"]
        with patch.object(hashing, "_verify_or_false", _slow(hashing._verify_or_false)):
            # 末尾のコードは 10 件目で一致する → 10 回分の重い検証がループ外で走る
            resp, max_gap = await _run_with_ticker(
                c.post("/api/auth/login/totp", json={"ticket": ticket, "code": codes[-1]})
            )
    assert resp.status_code == 200
    assert max_gap < _MAX_ALLOWED_GAP


def test_sync_verify_code_kept_for_mcp_login():
    """mcp_server/login.py が同期で呼ぶ _verify_code は coroutine を返してはならない。

    coroutine を返すと ``_verify_code(...) is None`` が常に False になり、
    TOTP 検証を素通りさせてしまうため回帰防止として固定する。
    """
    import inspect

    from millicall.auth.router import _verify_code

    assert not inspect.iscoroutinefunction(_verify_code)


async def test_scim_bearer_verify_does_not_block_loop(tmp_path):
    from millicall.config import Settings
    from millicall.main import create_app

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        scim_enabled=True,
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with application.state.sessionmaker() as s:
            s.add(AppSetting(key="scim_bearer_token_hash", value=hash_password("scim_tok")))
            await s.commit()
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            with patch.object(hashing, "verify_password", _slow(hashing.verify_password)):
                ok, gap_ok = await _run_with_ticker(
                    c.get("/scim/v2/Users", headers={"Authorization": "Bearer scim_tok"})
                )
                bad, gap_bad = await _run_with_ticker(
                    c.get("/scim/v2/Users", headers={"Authorization": "Bearer wrong"})
                )
    assert ok.status_code == 200
    assert bad.status_code == 401
    assert max(gap_ok, gap_bad) < _MAX_ALLOWED_GAP


async def test_lane_rejects_when_pending_limit_reached():
    """未完了ジョブ数が上限に達したレーンは計算せずに Argon2Busy（503）を送出する。"""
    lane = hashing._LANES[hashing.SCIM_LANE]
    release = threading.Event()
    ran: list[int] = []

    def _blocker() -> None:
        release.wait(5)

    futs = [
        asyncio.ensure_future(hashing.run_argon2(_blocker, lane=hashing.SCIM_LANE))
        for _ in range(lane.max_pending)
    ]
    try:
        await asyncio.sleep(0.05)
        assert lane.pending == lane.max_pending
        try:
            await hashing.run_argon2(ran.append, 1, lane=hashing.SCIM_LANE)
        except hashing.Argon2Busy as exc:
            assert exc.status_code == 503
            assert exc.headers["Retry-After"] == str(hashing._BUSY_RETRY_AFTER_SECONDS)
        else:
            raise AssertionError("Argon2Busy が送出されなかった")
        # 別レーン（auth）は影響を受けない
        assert await hashing.run_argon2(lambda: "ok") == "ok"
    finally:
        release.set()
        await asyncio.gather(*futs)
    assert ran == []
    assert lane.pending == 0


async def test_shutdown_then_reuse_recreates_executor():
    hashing.shutdown()
    assert await hashing.run_argon2(lambda: 42) == 42


async def test_scim_flood_does_not_delay_login(tmp_path):
    """不正 SCIM トークンの大量同時送信中も /login は待たされず、超過分は 503 になる。

    レビューでの再現（不正トークン 400 件同時送信で /login が 11 秒待ち、その間
    SQLite 書き込みロックを保持）の回帰テスト。
    """
    from millicall.config import Settings
    from millicall.main import create_app

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        scim_enabled=True,
    )
    application = create_app(settings)
    real_verify = hashing.verify_password

    def _verify_50ms(*args):
        time.sleep(0.05)
        return real_verify(*args)

    async with application.router.lifespan_context(application):
        async with application.state.sessionmaker() as s:
            s.add(AppSetting(key="scim_bearer_token_hash", value=hash_password("scim_tok")))
            await s.commit()
        await _create_local_user(application, "flood1", "Passw0rd1")
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            with patch.object(hashing, "verify_password", _verify_50ms):
                flood = [
                    asyncio.ensure_future(
                        c.get("/scim/v2/Users", headers={"Authorization": f"Bearer bad{i}"})
                    )
                    for i in range(200)
                ]
                await asyncio.sleep(0.2)  # scim レーンを埋める
                t0 = time.perf_counter()
                login = await c.post(
                    "/api/auth/login", json={"username": "flood1", "password": "Passw0rd1"}
                )
                login_elapsed = time.perf_counter() - t0
                results = await asyncio.gather(*flood)

    assert login.status_code == 200
    # scim レーンは 1 本 × 50ms × 32 件 ≈ 1.6 秒分詰まっているが、/login は別レーン
    assert login_elapsed < 1.0
    codes = [r.status_code for r in results]
    assert set(codes) <= {401, 503}
    busy = [r for r in results if r.status_code == 503]
    assert busy, "上限超過分が 503 になっていない"
    assert all(r.headers.get("Retry-After") for r in busy)
    # 計算されたのは上限以内（＋解放後の後続分）に限られ、大半は計算せずに断っている
    assert codes.count(401) < len(codes) // 2


# ---------------------------------------------------------------------------
# AI プロバイダ生成（SSRF ガードの DNS 解決）
# ---------------------------------------------------------------------------


def _slow_getaddrinfo(ip: str):
    def _fake(*_args, **_kwargs):
        time.sleep(_BLOCK_SECONDS)
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 443))]

    return patch("millicall.net_guard.socket.getaddrinfo", _fake)


async def test_build_llm_async_does_not_block_loop_on_slow_dns():
    from millicall.ai import registry

    with _slow_getaddrinfo("93.184.216.34"):
        llm, max_gap = await _run_with_ticker(
            registry.build_llm_async(
                "openai_compatible", {"base_url": "https://llm.example.com/v1"}, "k"
            )
        )
    assert llm._transport._pinned_ip == "93.184.216.34"
    assert max_gap < _MAX_ALLOWED_GAP


async def test_build_tts_async_does_not_block_loop_on_slow_dns():
    from millicall.ai import registry

    with _slow_getaddrinfo("192.168.1.10"):
        tts, max_gap = await _run_with_ticker(
            registry.build_tts_async("voicevox", {"engine_url": "http://vv.lan:50021"}, None)
        )
    assert tts._transport._pinned_ip == "192.168.1.10"
    assert max_gap < _MAX_ALLOWED_GAP


async def test_build_llm_async_keeps_ssrf_rejection():
    import pytest

    from millicall.ai import registry

    with _slow_getaddrinfo("127.0.0.1"), pytest.raises(ValueError, match="SSRF"):
        await registry.build_llm_async(
            "openai_compatible", {"base_url": "https://evil.example.com/v1"}, "k"
        )
