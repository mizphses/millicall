"""issue #86: MCP OAuth / SAML の認証状態を DB 共有ストアへ移した結合試験。

受け入れ条件:
- 再起動: アプリ（ストア）を作り直しても登録・トークンが有効。
- 2 ワーカー相当: 同じ DB ファイルを共有する独立したエンジン/プロバイダ（または独立した
  アプリ）間で、登録 → 認可 → 交換 → リフレッシュが別インスタンスに届いても成功する。
- 並行消費: 同じ認可コード / リフレッシュトークン / SAML ID を 2 インスタンス（別プロセス含む）
  から同時に消費して片方だけ成功し、もう片方は例外（500）ではなく正しく拒否される。
- TTL 切れ・上限超過・ハッシュ保存（DB ファイルに平文トークンが無い）。

(b)(c) はインメモリ SQLite では意味を成さないため、必ず一時ファイルの SQLite を使う。
"""

import asyncio
import hashlib
import os
import sys
import textwrap
import time
import urllib.parse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from mcp.server.auth.provider import RegistrationError, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from millicall.auth.saml.replay import consume_once
from millicall.auth.security import hash_password
from millicall.config import Settings
from millicall.db import create_db_engine
from millicall.db_migrations import upgrade_to_head
from millicall.main import create_app
from millicall.mcp_server import oauth as oauth_module
from millicall.mcp_server.oauth import MillicallOAuthProvider
from millicall.models import McpOAuthClient, McpOAuthCode, McpOAuthToken, SamlConsumedId, User
from millicall.process_lock import acquire_single_process_lock
from tests.test_mcp_oauth import MCP_HEADERS, MCP_INIT, _make_user, _pkce, _ticket

REDIRECT = "https://claude.ai/cb"


# --------------------------------------------------------------------------
# 共通: 同じ DB ファイルを共有する独立エンジン（= 別ワーカー相当）
# --------------------------------------------------------------------------
@pytest_asyncio.fixture
async def shared_db(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'shared.db'}"
    await asyncio.to_thread(upgrade_to_head, url)
    engines = []

    def make_sessionmaker():
        engine = create_db_engine(url)
        engines.append(engine)
        return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with make_sessionmaker()() as session:
        session.add(
            User(
                username="worker",
                hashed_password=hash_password("Passw0rd1"),
                display_name="worker",
                role="admin",
                origin="local",
            )
        )
        await session.commit()

    yield url, make_sessionmaker, tmp_path / "shared.db"
    for engine in engines:
        await engine.dispose()


def _provider(sessionmaker) -> MillicallOAuthProvider:
    provider = MillicallOAuthProvider("http://localhost")
    provider.set_sessionmaker(sessionmaker)
    return provider


def _client(client_id="shared-client", **kwargs) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=[AnyUrl(REDIRECT)],
        grant_types=["authorization_code", "refresh_token"],
        token_endpoint_auth_method="none",
        **kwargs,
    )


async def _code(provider, client, username="worker"):
    state = await provider._current_user_state(username)
    return await provider.create_auth_code(
        client_id=client.client_id,
        username=username,
        code_challenge="challenge",
        redirect_uri=REDIRECT,
        scopes=[],
        user_state=state[1],
    )


async def _count(sessionmaker, model, *where):
    async with sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(model).where(*where))


# --------------------------------------------------------------------------
# (b) 2 ワーカー相当: 登録 → 認可 → 交換 → リフレッシュを別インスタンスへ
# --------------------------------------------------------------------------
async def test_flow_spans_independent_provider_instances(shared_db):
    _, make_sm, _ = shared_db
    worker_a, worker_b = _provider(make_sm()), _provider(make_sm())

    client = _client()
    await worker_a.register_client(client)
    assert await worker_b.get_client(client.client_id) is not None

    code = await _code(worker_b, client)
    loaded = await worker_a.load_authorization_code(client, code)
    assert loaded is not None
    tokens = await worker_b.exchange_authorization_code(client, loaded)

    access = await worker_a.load_access_token(tokens.access_token)
    assert access is not None and access.subject == "worker"

    refresh = await worker_a.load_refresh_token(client, tokens.refresh_token)
    rotated = await worker_b.exchange_refresh_token(client, refresh, [])
    assert await worker_a.load_access_token(rotated.access_token) is not None
    # ローテーション済みの旧 refresh はどのインスタンスからも使えない。
    assert await worker_a.load_refresh_token(client, tokens.refresh_token) is None
    with pytest.raises(TokenError):
        await worker_a.exchange_refresh_token(client, refresh, [])


async def test_revocation_by_epoch_seen_by_other_instance(shared_db):
    _, make_sm, _ = shared_db
    sm_a = make_sm()
    worker_a, worker_b = _provider(sm_a), _provider(make_sm())
    client = _client()
    await worker_a.register_client(client)
    tokens = await worker_a.exchange_authorization_code(
        client, await worker_a.load_authorization_code(client, await _code(worker_a, client))
    )
    async with sm_a() as session:
        user = await session.scalar(select(User).where(User.username == "worker"))
        user.session_epoch += 1
        await session.commit()
    assert await worker_b.load_access_token(tokens.access_token) is None
    refresh = await worker_b.load_refresh_token(client, tokens.refresh_token)
    with pytest.raises(TokenError):
        await worker_b.exchange_refresh_token(client, refresh, [])


# --------------------------------------------------------------------------
# (c) 並行消費: 別エンジン（別接続・別スレッド）から同時に消費して片方だけ成功
# --------------------------------------------------------------------------
async def test_concurrent_code_exchange_across_instances(shared_db):
    _, make_sm, _ = shared_db
    worker_a, worker_b = _provider(make_sm()), _provider(make_sm())
    client = _client()
    await worker_a.register_client(client)
    for _ in range(10):
        code = await _code(worker_a, client)
        loaded_a = await worker_a.load_authorization_code(client, code)
        loaded_b = await worker_b.load_authorization_code(client, code)
        results = await asyncio.gather(
            worker_a.exchange_authorization_code(client, loaded_a),
            worker_b.exchange_authorization_code(client, loaded_b),
            return_exceptions=True,
        )
        assert sum(isinstance(r, OAuthToken) for r in results) == 1, results
        assert sum(isinstance(r, TokenError) for r in results) == 1, results


async def test_concurrent_refresh_rotation_across_instances(shared_db):
    _, make_sm, _ = shared_db
    sm = make_sm()
    worker_a, worker_b = _provider(sm), _provider(make_sm())
    client = _client()
    await worker_a.register_client(client)
    tokens = await worker_a.exchange_authorization_code(
        client, await worker_a.load_authorization_code(client, await _code(worker_a, client))
    )
    for _ in range(10):
        refresh_a = await worker_a.load_refresh_token(client, tokens.refresh_token)
        refresh_b = await worker_b.load_refresh_token(client, tokens.refresh_token)
        results = await asyncio.gather(
            worker_a.exchange_refresh_token(client, refresh_a, []),
            worker_b.exchange_refresh_token(client, refresh_b, []),
            return_exceptions=True,
        )
        winners = [r for r in results if isinstance(r, OAuthToken)]
        assert len(winners) == 1, results
        assert sum(isinstance(r, TokenError) for r in results) == 1, results
        tokens = winners[0]
    # 各ラウンドで発行された refresh は 1 本だけ（敗者は何も発行していない）。
    assert await _count(sm, McpOAuthToken, McpOAuthToken.kind == "refresh") == 1


async def test_concurrent_saml_consumption_across_instances(shared_db):
    _, make_sm, _ = shared_db
    sm_a, sm_b = make_sm(), make_sm()
    for i in range(10):
        entries = [(f"assertion:_a{i}", 300), (f"request:_r{i}", 600)]
        results = await asyncio.gather(consume_once(sm_a, entries), consume_once(sm_b, entries))
        assert sorted(results, key=lambda r: r is not None) == [None, 0], results


_EXCHANGE_SCRIPT = textwrap.dedent(
    """
    import asyncio, os, sys, time
    from mcp.server.auth.provider import TokenError
    from mcp.shared.auth import OAuthClientInformationFull
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from millicall.db import create_db_engine
    from millicall.mcp_server.oauth import MillicallOAuthProvider

    url, client_json, code, go = sys.argv[1:]

    async def main():
        engine = create_db_engine(url)
        provider = MillicallOAuthProvider("http://localhost")
        provider.set_sessionmaker(
            async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        )
        client = OAuthClientInformationFull.model_validate_json(client_json)
        loaded = await provider.load_authorization_code(client, code)
        print("ready", flush=True)
        while not os.path.exists(go):
            time.sleep(0.001)
        try:
            await provider.exchange_authorization_code(client, loaded)
            print("ok", flush=True)
        except TokenError:
            print("rejected", flush=True)
        finally:
            await engine.dispose()

    asyncio.run(main())
    """
)


async def test_concurrent_code_exchange_across_processes(shared_db, tmp_path):
    """実プロセス 2 つから同じ認可コードを同時に交換し、片方だけが成功する。"""
    url, make_sm, _ = shared_db
    provider = _provider(make_sm())
    client = _client()
    await provider.register_client(client)
    code = await _code(provider, client)
    go = tmp_path / "go"
    procs = [
        await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _EXCHANGE_SCRIPT,
            url,
            client.model_dump_json(),
            code,
            str(go),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        for _ in range(2)
    ]
    for proc in procs:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=60)
        assert line.strip() == b"ready", (await proc.stderr.read()).decode()
    go.touch()
    outputs = []
    for proc in procs:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        assert proc.returncode == 0, err.decode()
        outputs.append(out.decode().strip())
    assert sorted(outputs) == ["ok", "rejected"]


# --------------------------------------------------------------------------
# (a) 再起動相当: ストアを作り直しても有効
# --------------------------------------------------------------------------
async def test_state_survives_new_store_instance(shared_db):
    _, make_sm, _ = shared_db
    before = _provider(make_sm())
    client = _client()
    await before.register_client(client)
    tokens = await before.exchange_authorization_code(
        client, await before.load_authorization_code(client, await _code(before, client))
    )
    pending_code = await _code(before, client)
    sm_a = make_sm()
    assert await consume_once(sm_a, [("assertion:_restart", 300)]) is None

    after = _provider(make_sm())  # 新しいエンジン = プロセス再起動相当
    assert await after.get_client(client.client_id) is not None
    assert await after.load_access_token(tokens.access_token) is not None
    assert await after.load_refresh_token(client, tokens.refresh_token) is not None
    assert await after.load_authorization_code(client, pending_code) is not None
    assert await consume_once(make_sm(), [("assertion:_restart", 300)]) == 0


def _mcp_settings(tmp_path, data_dir, db_path):
    return Settings(
        data_dir=data_dir,
        database_url=f"sqlite+aiosqlite:///{db_path}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        mcp_issuer_url="http://localhost",
        mcp_allowed_hosts=["localhost", "127.0.0.1"],
        static_dir=tmp_path / "no-static",
    )


async def _http_login(app, http, client_id, challenge):
    """/mcp-login/callback でログインし、発行された認可コードを返す。"""
    ticket = _ticket(app, client_id=client_id, code_challenge=challenge)
    callback = await http.post(
        "/mcp-login/callback",
        data={"ticket": ticket, "username": "restart", "password": "Passw0rd1"},
    )
    assert callback.status_code == 302
    query = urllib.parse.parse_qs(urllib.parse.urlparse(callback.headers["location"]).query)
    return query["code"][0]


async def _register_confidential(http):
    reg = await http.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "client_secret_post",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert reg.status_code == 201
    body = reg.json()
    assert body["client_secret"]
    return body["client_id"], body["client_secret"]


async def test_app_restart_keeps_mcp_authorization(tmp_path):
    """core 再起動後も DCR 登録・access/refresh が有効で、再認証が不要。"""
    settings = _mcp_settings(tmp_path, tmp_path, tmp_path / "test.db")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        await _make_user(app, username="restart", password="Passw0rd1")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
            client_id, client_secret = await _register_confidential(c)
            verifier, challenge = _pkce()
            code = await _http_login(app, c, client_id, challenge)
            tok = await c.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": verifier,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": REDIRECT,
                },
            )
            assert tok.status_code == 200, tok.text
            tokens = tok.json()

    restarted = create_app(_mcp_settings(tmp_path, tmp_path, tmp_path / "test.db"))
    async with restarted.router.lifespan_context(restarted):
        transport = ASGITransport(app=restarted)
        async with AsyncClient(transport=transport, base_url="http://localhost") as c:
            ok = await c.post(
                "/mcp",
                json=MCP_INIT,
                headers={**MCP_HEADERS, "Authorization": f"Bearer {tokens['access_token']}"},
            )
            assert ok.status_code == 200
            refreshed = await c.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            assert refreshed.status_code == 200, refreshed.text


async def test_two_apps_share_oauth_state_over_http(tmp_path):
    """同じ DB を共有する 2 つのアプリ: A で登録・ログイン、B で交換、A でリフレッシュ、B で /mcp。

    ログインチケットは data_dir の鍵で署名されるため、実運用の複数ワーカーと同様に
    ログイン往復（/mcp-login → callback）は同じインスタンスで行う。
    """
    db_path = tmp_path / "shared.db"
    app_a = create_app(_mcp_settings(tmp_path, tmp_path / "a", db_path))
    app_b = create_app(_mcp_settings(tmp_path, tmp_path / "b", db_path))
    async with app_a.router.lifespan_context(app_a), app_b.router.lifespan_context(app_b):
        await _make_user(app_a, username="restart", password="Passw0rd1")
        async with (
            AsyncClient(transport=ASGITransport(app=app_a), base_url="http://localhost") as a,
            AsyncClient(transport=ASGITransport(app=app_b), base_url="http://localhost") as b,
        ):
            client_id, client_secret = await _register_confidential(a)
            verifier, challenge = _pkce()
            code = await _http_login(app_a, a, client_id, challenge)
            exchange = {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": REDIRECT,
            }
            tok = await b.post("/token", data=exchange)
            assert tok.status_code == 200, tok.text
            # 同じコードの再交換は別インスタンスでも拒否（400; 500 ではない）。
            again = await a.post("/token", data=exchange)
            assert again.status_code == 400
            refreshed = await a.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tok.json()["refresh_token"],
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            assert refreshed.status_code == 200, refreshed.text
            ok = await b.post(
                "/mcp",
                json=MCP_INIT,
                headers={
                    **MCP_HEADERS,
                    "Authorization": f"Bearer {refreshed.json()['access_token']}",
                },
            )
            assert ok.status_code == 200


# --------------------------------------------------------------------------
# client_secret のハッシュ照合
# --------------------------------------------------------------------------
async def test_client_secret_checked_against_hash(tmp_path):
    settings = _mcp_settings(tmp_path, tmp_path, tmp_path / "test.db")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        await _make_user(app, username="restart", password="Passw0rd1")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
            client_id, client_secret = await _register_confidential(c)
            stored_hash = hashlib.sha256(client_secret.encode()).hexdigest()
            async with app.state.sessionmaker() as session:
                row = await session.get(McpOAuthClient, client_id)
            assert row.client_secret_hash == stored_hash
            assert client_secret not in row.client_info
            # get_client は平文を返さない（SDK 標準認証に回っても一致しない値）。
            client = await app.state.mcp_oauth_provider.get_client(client_id)
            assert client.client_secret not in (client_secret, stored_hash)

            verifier, challenge = _pkce()
            code = await _http_login(app, c, client_id, challenge)
            base = {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "client_id": client_id,
                "redirect_uri": REDIRECT,
            }
            # 秘密なし / 誤り / DB ダンプのハッシュをそのまま提示 → すべて 401。
            for secret in (None, "wrong", stored_hash):
                data = dict(base) if secret is None else {**base, "client_secret": secret}
                r = await c.post("/token", data=data)
                assert r.status_code == 401, (secret, r.text)
            r = await c.post("/token", data={**base, "client_secret": client_secret})
            assert r.status_code == 200, r.text


# --------------------------------------------------------------------------
# (d) ハッシュ保存: DB ファイル（WAL 含む）に平文が残らない
# --------------------------------------------------------------------------
async def test_db_files_contain_no_plaintext_secrets(shared_db):
    _, make_sm, db_path = shared_db
    sm = make_sm()
    provider = _provider(sm)
    client = OAuthClientInformationFull(
        client_id="secret-client",
        client_secret="s3cr3t-" + "x" * 40,
        redirect_uris=[AnyUrl(REDIRECT)],
        grant_types=["authorization_code", "refresh_token"],
        token_endpoint_auth_method="client_secret_post",
    )
    await provider.register_client(client)
    pending = await _code(provider, client)
    tokens = await provider.exchange_authorization_code(
        client, await provider.load_authorization_code(client, await _code(provider, client))
    )
    secrets_ = [client.client_secret, pending, tokens.access_token, tokens.refresh_token]

    raw = b"".join(
        p.read_bytes() for p in (db_path, db_path.with_name(db_path.name + "-wal")) if p.exists()
    )
    for value in secrets_:
        assert value.encode() not in raw
    async with sm() as session:
        hashes = set(await session.scalars(select(McpOAuthToken.token_hash)))
        hashes |= set(await session.scalars(select(McpOAuthCode.code_hash)))
    for value in secrets_[1:]:
        assert hashlib.sha256(value.encode()).hexdigest() in hashes


# --------------------------------------------------------------------------
# (d) TTL
# --------------------------------------------------------------------------
async def test_expired_code_and_tokens_rejected_and_deleted(shared_db):
    _, make_sm, _ = shared_db
    sm = make_sm()
    provider = _provider(sm)
    client = _client()
    await provider.register_client(client)
    code = await _code(provider, client)
    loaded = await provider.load_authorization_code(client, code)
    tokens = await provider.exchange_authorization_code(
        client, await provider.load_authorization_code(client, await _code(provider, client))
    )
    expired_code = await _code(provider, client)
    expired_loaded = await provider.load_authorization_code(client, expired_code)
    past = time.time() - 1
    async with sm() as session:
        await session.execute(update(McpOAuthToken).values(expires_at=past))
        await session.execute(
            update(McpOAuthCode)
            .where(McpOAuthCode.code_hash == hashlib.sha256(expired_code.encode()).hexdigest())
            .values(expires_at=past)
        )
        await session.commit()

    assert await provider.load_access_token(tokens.access_token) is None
    assert await provider.load_refresh_token(client, tokens.refresh_token) is None
    assert await provider.load_authorization_code(client, expired_code) is None
    with pytest.raises(TokenError):
        await provider.exchange_authorization_code(client, expired_loaded)
    # アクセス時の遅延削除で期限切れ行は消えている。
    assert await _count(sm, McpOAuthToken) == 0
    # 期限内のコードは残り、交換できる。
    assert await provider.exchange_authorization_code(client, loaded)


async def test_unused_client_registration_expires(shared_db, monkeypatch):
    _, make_sm, _ = shared_db
    sm = make_sm()
    provider = _provider(sm)
    stale, used = _client("stale"), _client("used")
    await provider.register_client(stale)
    await provider.register_client(used)
    await _code(provider, used)  # 認可済み = 使用中
    old = time.time() - oauth_module._UNUSED_CLIENT_TTL - 10
    async with sm() as session:
        await session.execute(update(McpOAuthClient).values(created_at=old))
        await session.commit()
    await provider.register_client(_client("fresh"))  # 登録時の一括清掃
    assert await provider.get_client("stale") is None
    assert await provider.get_client("used") is not None
    assert await provider.get_client("fresh") is not None


async def test_saml_consumed_id_expires(shared_db):
    _, make_sm, _ = shared_db
    sm = make_sm()
    assert await consume_once(sm, [("assertion:_ttl", 300)]) is None
    assert await consume_once(sm, [("assertion:_ttl", 300)]) == 0
    async with sm() as session:
        await session.execute(update(SamlConsumedId).values(expires_at=time.time() - 1))
        await session.commit()
    assert await consume_once(sm, [("assertion:_other", 300)]) is None  # 一括清掃
    assert await _count(sm, SamlConsumedId) == 1


async def test_saml_request_replay_does_not_consume_assertion(shared_db):
    _, make_sm, _ = shared_db
    sm = make_sm()
    assert await consume_once(sm, [("assertion:_a1", 300), ("request:_r", 600)]) is None
    assert await consume_once(sm, [("assertion:_a2", 300), ("request:_r", 600)]) == 1
    # 失敗したトランザクションはロールバックされ、_a2 は未消費のまま。
    assert await consume_once(sm, [("assertion:_a2", 300)]) is None


# --------------------------------------------------------------------------
# (d) 上限
# --------------------------------------------------------------------------
async def test_registration_cap_evicts_unused_then_rejects(shared_db, monkeypatch):
    _, make_sm, _ = shared_db
    monkeypatch.setattr(oauth_module, "_MAX_CLIENTS", 3)
    sm = make_sm()
    provider = _provider(sm)
    for i in range(3):
        await provider.register_client(_client(f"c{i}"))
    # 満杯: 未使用登録の最古（c0）を追い出して受け入れる。
    await provider.register_client(_client("c3"))
    assert await provider.get_client("c0") is None
    assert await _count(sm, McpOAuthClient) == 3
    # 全登録が認可済みなら追い出さずに拒否する。
    for client_id in ("c1", "c2", "c3"):
        await _code(provider, _client(client_id))
    with pytest.raises(RegistrationError):
        await provider.register_client(_client("c4"))
    assert await _count(sm, McpOAuthClient) == 3


async def test_registration_rejects_oversized_metadata(shared_db):
    _, make_sm, _ = shared_db
    sm = make_sm()
    provider = _provider(sm)
    with pytest.raises(RegistrationError):
        await provider.register_client(_client("big", client_name="x" * 20000))
    assert await _count(sm, McpOAuthClient) == 0


async def test_dcr_over_http_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth_module, "_MAX_CLIENTS", 2)
    app = create_app(_mcp_settings(tmp_path, tmp_path, tmp_path / "test.db"))
    async with app.router.lifespan_context(app):
        await _make_user(app, username="restart", password="Passw0rd1")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
            ids = [(await _register_confidential(c))[0] for _ in range(2)]
            verifier, challenge = _pkce()
            for client_id in ids:
                await _http_login(app, c, client_id, challenge)
            r = await c.post(
                "/register",
                json={
                    "redirect_uris": [REDIRECT],
                    "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                },
            )
            assert r.status_code == 400
            assert r.json()["error"] == "invalid_client_metadata"
        assert await _count(app.state.sessionmaker, McpOAuthClient) == 2


async def test_subject_token_caps(shared_db, monkeypatch):
    _, make_sm, _ = shared_db
    monkeypatch.setattr(oauth_module, "_MAX_REFRESH_PER_SUBJECT", 2)
    sm = make_sm()
    provider = _provider(sm)
    client = _client()
    await provider.register_client(client)
    grants = []
    for _ in range(3):
        loaded = await provider.load_authorization_code(client, await _code(provider, client))
        grants.append(await provider.exchange_authorization_code(client, loaded))
    # 最古の認可は access ごと失効し、新しい 2 つは有効。
    assert await provider.load_refresh_token(client, grants[0].refresh_token) is None
    assert await provider.load_access_token(grants[0].access_token) is None
    for tokens in grants[1:]:
        assert await provider.load_access_token(tokens.access_token) is not None
    assert await _count(sm, McpOAuthToken, McpOAuthToken.kind == "refresh") == 2


async def test_state_store_requires_db():
    provider = MillicallOAuthProvider("http://localhost")
    with pytest.raises(RuntimeError):
        await provider.get_client("x")


# --------------------------------------------------------------------------
# 単一プロセスガード
# --------------------------------------------------------------------------
def test_process_lock_rejects_second_holder(tmp_path):
    first = acquire_single_process_lock(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="1 プロセス"):
            acquire_single_process_lock(tmp_path)
    finally:
        first.release()
    acquire_single_process_lock(tmp_path).release()


def test_process_lock_detects_other_process(tmp_path):
    import subprocess

    script = (
        "import sys, time; from pathlib import Path;"
        "from millicall.process_lock import acquire_single_process_lock as a;"
        "l = a(Path(sys.argv[1])); print('held', flush=True); sys.stdin.readline()"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    try:
        assert proc.stdout.readline().strip() == b"held"
        with pytest.raises(RuntimeError):
            acquire_single_process_lock(tmp_path)
    finally:
        proc.stdin.write(b"\n")
        proc.stdin.close()
        proc.wait(timeout=30)
    # 保持プロセス終了でカーネルがロックを解放する（異常終了後の再起動で誤検出しない）。
    acquire_single_process_lock(tmp_path).release()


async def test_second_core_on_same_data_dir_refuses_to_start(tmp_path):
    settings = _mcp_settings(tmp_path, tmp_path, tmp_path / "test.db")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        second = create_app(_mcp_settings(tmp_path, tmp_path, tmp_path / "test.db"))
        with pytest.raises(RuntimeError):
            async with second.router.lifespan_context(second):
                pass
    assert os.path.exists(tmp_path / "core.lock")
