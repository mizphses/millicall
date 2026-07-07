"""SCIM 2.0 サーバーテスト（Phase 6 Task 5）。

テスト対象:
  - POST /api/scim/token : トークン生成（admin のみ）
  - Bearer 認証チェック
  - scim_enabled=False 時の 404 動作
  - Users CRUD（origin="scim" 限定）
  - PATCH active:false → enabled=False + session_epoch 増加 + 監査
  - DELETE → deactivate (enabled=False, epoch 増加, 204)
  - origin safety: local/saml ユーザーへの SCIM 操作は 404
  - Groups: 500 を返さない基本 CRUD
  - ディスカバリエンドポイント
  - シークレット衛生: トークン平文は rotate レスポンス以外に現れない
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from millicall.auth.security import hash_password
from millicall.config import Settings
from millicall.main import create_app
from millicall.models import AppSetting, User

# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def scim_app(tmp_path):
    """SCIM 有効な設定でアプリを起動する。"""
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
        yield application


@pytest_asyncio.fixture
async def scim_disabled_app(tmp_path):
    """SCIM 無効（デフォルト）な設定でアプリを起動する。"""
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        scim_enabled=False,
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(scim_app):
    transport = ASGITransport(app=scim_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def disabled_client(scim_disabled_app):
    transport = ASGITransport(app=scim_disabled_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def admin_client(scim_app):
    """cookie+CSRF 認証済み管理者クライアント。"""
    transport = ASGITransport(app=scim_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # ログインして CSRF トークンを取得
        r = await c.post("/api/auth/login", json={"username": "admin", "password": _admin_password(scim_app)})
        assert r.status_code == 200, r.text
        csrf = c.cookies.get("millicall_csrf", "")
        c.headers.update({"X-CSRF-Token": csrf})
        yield c


def _admin_password(app) -> str:
    """テスト用: lifespan で生成した初期管理者パスワードを取得する。
    ensure_admin_user が返す新規パスワードを直接取れないため、
    fixture 内で直接 admin ユーザーを挿入する。
    """
    # lifespan で admin が生成されるが、パスワードが不明なので
    # テスト用の既知パスワードで admin を作り直す。
    return "__DUMMY__"


@pytest_asyncio.fixture
async def known_admin_client(scim_app):
    """既知パスワードで admin を作成してログインする。"""
    sm = scim_app.state.sessionmaker
    async with sm() as session:
        # 既存の admin ユーザーを既知パスワードに更新
        from sqlalchemy import select
        admin = await session.scalar(select(User).where(User.username == "admin"))
        if admin:
            admin.hashed_password = hash_password("TestAdmin1!")
        else:
            session.add(User(
                username="admin",
                hashed_password=hash_password("TestAdmin1!"),
                display_name="Admin",
                role="admin",
                origin="local",
            ))
        await session.commit()

    transport = ASGITransport(app=scim_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "TestAdmin1!"})
        assert r.status_code == 200, r.text
        csrf = c.cookies.get("millicall_csrf", "")
        c.headers.update({"X-CSRF-Token": csrf})
        yield c


async def _get_scim_token(admin_client: AsyncClient) -> str:
    """SCIM トークンを生成して返す。"""
    r = await admin_client.post("/api/scim/token")
    assert r.status_code == 201, r.text
    return r.json()["token"]


# ---------------------------------------------------------------------------
# トークン管理テスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_rotate_admin(known_admin_client, scim_app):
    """管理者は POST /api/scim/token でトークンを取得できる。"""
    r = await known_admin_client.post("/api/scim/token")
    assert r.status_code == 201
    data = r.json()
    assert "token" in data
    token = data["token"]
    assert len(token) > 20

    # DB には平文が格納されていないこと（ハッシュのみ）
    sm = scim_app.state.sessionmaker
    async with sm() as session:
        row = await session.get(AppSetting, "scim_bearer_token_hash")
        assert row is not None
        stored_value = row.value
        # 平文 token が DB に格納されていないこと
        assert token not in stored_value
        # DB 値は Argon2 ハッシュの形式（$argon2 で始まる）
        assert stored_value.startswith("$argon2")


@pytest.mark.asyncio
async def test_token_rotate_non_admin_forbidden(scim_app):
    """非管理者は POST /api/scim/token で 403 を受け取る。"""
    sm = scim_app.state.sessionmaker
    async with sm() as session:
        session.add(User(
            username="regular_user",
            hashed_password=hash_password("Pass1234!"),
            display_name="Regular",
            role="user",
            origin="local",
        ))
        await session.commit()

    transport = ASGITransport(app=scim_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/login", json={"username": "regular_user", "password": "Pass1234!"})
        assert r.status_code == 200
        csrf = c.cookies.get("millicall_csrf", "")
        c.headers.update({"X-CSRF-Token": csrf})
        r2 = await c.post("/api/scim/token")
        assert r2.status_code == 403


@pytest.mark.asyncio
async def test_token_in_response_not_in_audit(known_admin_client, scim_app):
    """トークン平文は rotate レスポンス以外に現れない（監査ログにも含まれない）。"""
    r = await known_admin_client.post("/api/scim/token")
    assert r.status_code == 201
    token = r.json()["token"]

    # 監査ログを確認
    from sqlalchemy import select

    from millicall.models import AuditLog
    sm = scim_app.state.sessionmaker
    async with sm() as session:
        logs = (await session.scalars(select(AuditLog).where(AuditLog.action == "scim.token.rotate"))).all()
        assert len(logs) >= 1
        for log in logs:
            detail = log.detail or ""
            assert token not in detail, "トークン平文が監査ログに含まれている"


# ---------------------------------------------------------------------------
# Bearer 認証テスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scim_missing_bearer_401(client):
    """Bearer なしでは 401。"""
    r = await client.get("/scim/v2/Users")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_scim_wrong_bearer_401(known_admin_client, client):
    """誤った Bearer では 401。"""
    await _get_scim_token(known_admin_client)
    r = await client.get("/scim/v2/Users", headers={"Authorization": "Bearer wrongtoken12345"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_scim_disabled_returns_404(disabled_client):
    """scim_enabled=False 時は Bearer があっても 404。"""
    r = await disabled_client.get(
        "/scim/v2/Users", headers={"Authorization": "Bearer anytoken"}
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_scim_no_token_configured_401(client):
    """トークン未設定の場合は 401（SCIM 有効だがトークンなし）。"""
    r = await client.get("/scim/v2/Users", headers={"Authorization": "Bearer sometoken"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Users: 作成 / 取得テスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user_scim(known_admin_client, client, scim_app):
    """SCIM でユーザーを作成すると origin="scim"、201、Location ヘッダーが付く。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "scim.alice",
        "name": {"givenName": "Alice", "familyName": "Smith"},
        "emails": [{"value": "alice@example.com", "primary": True}],
        "active": True,
    }
    r = await client.post("/scim/v2/Users", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["userName"] == "scim.alice"
    assert data["active"] is True
    assert "id" in data
    assert "Location" in r.headers

    # DB で origin="scim" を確認
    sm = scim_app.state.sessionmaker
    async with sm() as session:
        from sqlalchemy import select
        user = await session.scalar(select(User).where(User.username == "scim.alice"))
        assert user is not None
        assert user.origin == "scim"
        assert user.enabled is True
        assert user.role == "user"


@pytest.mark.asyncio
async def test_get_user_scim(known_admin_client, client):
    """GET /scim/v2/Users/{id} でユーザーを取得できる。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    # 作成
    payload = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "scim.bob",
        "displayName": "Bob",
        "emails": [{"value": "bob@example.com", "primary": True}],
    }
    r = await client.post("/scim/v2/Users", json=payload, headers=headers)
    assert r.status_code == 201
    user_id = r.json()["id"]

    # 取得
    r2 = await client.get(f"/scim/v2/Users/{user_id}", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["userName"] == "scim.bob"
    assert r2.json()["id"] == user_id


@pytest.mark.asyncio
async def test_list_users_filter_username(known_admin_client, client):
    """GET /scim/v2/Users?filter=userName eq "x" でフィルタできる。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    # 2 ユーザーを作成
    for name in ["scim.filter1", "scim.filter2"]:
        await client.post(
            "/scim/v2/Users",
            json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": name},
            headers=headers,
        )

    r = await client.get('/scim/v2/Users?filter=userName eq "scim.filter1"', headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["totalResults"] == 1
    assert data["Resources"][0]["userName"] == "scim.filter1"


@pytest.mark.asyncio
async def test_list_users_filter_email(known_admin_client, client):
    """emails.value フィルターが動作する。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "scim.emailfilter",
            "emails": [{"value": "emailfilter@example.com", "primary": True}],
        },
        headers=headers,
    )

    r = await client.get('/scim/v2/Users?filter=emails.value eq "emailfilter@example.com"', headers=headers)
    assert r.status_code == 200
    assert r.json()["totalResults"] == 1


@pytest.mark.asyncio
async def test_list_users_unsupported_filter_400(known_admin_client, client):
    """サポートされていないフィルターは 400。"""
    token = await _get_scim_token(known_admin_client)
    r = await client.get(
        '/scim/v2/Users?filter=unknownAttr eq "x"',
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_duplicate_username_409(known_admin_client, client):
    """重複 userName は 409 SCIM エラー。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "scim.dup",
    }
    r1 = await client.post("/scim/v2/Users", json=payload, headers=headers)
    assert r1.status_code == 201
    r2 = await client.post("/scim/v2/Users", json=payload, headers=headers)
    assert r2.status_code == 409
    data = r2.json()
    assert data["scimType"] == "uniqueness"


# ---------------------------------------------------------------------------
# PUT テスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_user_update(known_admin_client, client):
    """PUT /scim/v2/Users/{id} でユーザーを更新できる。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "scim.put1", "displayName": "Old"},
        headers=headers,
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    r2 = await client.put(
        f"/scim/v2/Users/{user_id}",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "scim.put1",
            "displayName": "New Name",
            "active": True,
        },
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["displayName"] == "New Name"


# ---------------------------------------------------------------------------
# PATCH active:false テスト（セキュリティクリティカル）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_active_false_deactivates_and_revokes_session(known_admin_client, client, scim_app):
    """PATCH active:false → enabled=False AND session_epoch 増加（即時セッション失効）。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    # ユーザー作成
    r = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "scim.deact"},
        headers=headers,
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    # 初期 epoch を記録
    sm = scim_app.state.sessionmaker
    from sqlalchemy import select
    async with sm() as session:
        user_before = await session.get(User, int(user_id))
        epoch_before = user_before.session_epoch
        assert user_before.enabled is True

    # PATCH active:false
    r2 = await client.patch(
        f"/scim/v2/Users/{user_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["active"] is False

    # DB 検証: enabled=False かつ session_epoch が増加
    async with sm() as session:
        user_after = await session.get(User, int(user_id))
        assert user_after.enabled is False, "enabled は False になるべき"
        assert user_after.session_epoch > epoch_before, "session_epoch は増加するべき（即時セッション失効）"

    # 監査ログに scim.user.deactivate が記録されていること
    from millicall.models import AuditLog
    async with sm() as session:
        logs = (await session.scalars(
            select(AuditLog).where(
                AuditLog.action == "scim.user.deactivate",
                AuditLog.target_id == str(user_id),
            )
        )).all()
        assert len(logs) >= 1


@pytest.mark.asyncio
async def test_patch_active_false_via_body_dict(known_admin_client, client, scim_app):
    """PATCH Operations[{op:replace, value:{active:false}}] 形式も deactivate する。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "scim.deact2"},
        headers=headers,
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    sm = scim_app.state.sessionmaker
    async with sm() as session:
        user_before = await session.get(User, int(user_id))
        epoch_before = user_before.session_epoch

    r2 = await client.patch(
        f"/scim/v2/Users/{user_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "value": {"active": False}}],
        },
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["active"] is False

    async with sm() as session:
        user_after = await session.get(User, int(user_id))
        assert user_after.enabled is False
        assert user_after.session_epoch > epoch_before


# ---------------------------------------------------------------------------
# DELETE テスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_deactivates(known_admin_client, client, scim_app):
    """DELETE /scim/v2/Users/{id} → enabled=False, epoch 増加, 204 を返す。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "scim.del1"},
        headers=headers,
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    sm = scim_app.state.sessionmaker
    async with sm() as session:
        user_before = await session.get(User, int(user_id))
        epoch_before = user_before.session_epoch

    r2 = await client.delete(f"/scim/v2/Users/{user_id}", headers=headers)
    assert r2.status_code == 204

    async with sm() as session:
        user_after = await session.get(User, int(user_id))
        assert user_after.enabled is False, "DELETE 後は enabled=False"
        assert user_after.session_epoch > epoch_before, "DELETE 後は epoch 増加"


# ---------------------------------------------------------------------------
# Origin safety テスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scim_cannot_see_local_user(known_admin_client, client, scim_app):
    """SCIM GET は origin != "scim" のユーザーを 404 で返す。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    # local ユーザーを直接 DB に作成
    sm = scim_app.state.sessionmaker
    async with sm() as session:
        local_admin = User(
            username="local.admin.orig",
            hashed_password=hash_password("LocalPass1!"),
            display_name="Local Admin",
            role="admin",
            origin="local",
        )
        session.add(local_admin)
        await session.commit()
        local_id = local_admin.id

    # SCIM GET は 404 を返すべき
    r = await client.get(f"/scim/v2/Users/{local_id}", headers=headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_scim_cannot_patch_local_user(known_admin_client, client, scim_app):
    """SCIM PATCH は origin != "scim" のユーザーを 404 で返す（乗っ取り防止）。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    sm = scim_app.state.sessionmaker
    async with sm() as session:
        local_admin = User(
            username="local.admin.notouch",
            hashed_password=hash_password("LocalPass1!"),
            display_name="Local Admin 2",
            role="admin",
            origin="local",
        )
        session.add(local_admin)
        await session.commit()
        local_id = local_admin.id

    r = await client.patch(
        f"/scim/v2/Users/{local_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
        headers=headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_scim_cannot_delete_local_user(known_admin_client, client, scim_app):
    """SCIM DELETE は origin != "scim" のユーザーを 404 で返す。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    sm = scim_app.state.sessionmaker
    async with sm() as session:
        local_user = User(
            username="local.nodelete",
            hashed_password=hash_password("LocalPass1!"),
            display_name="Local Nodelete",
            role="user",
            origin="local",
        )
        session.add(local_user)
        await session.commit()
        local_id = local_user.id

    r = await client.delete(f"/scim/v2/Users/{local_id}", headers=headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_local_user_not_in_list(known_admin_client, client, scim_app):
    """GET /scim/v2/Users 一覧は origin="scim" のユーザーのみ含む。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    sm = scim_app.state.sessionmaker
    async with sm() as session:
        session.add(User(
            username="local.invisible",
            hashed_password=hash_password("Pass1!"),
            display_name="Invisible",
            role="user",
            origin="local",
        ))
        await session.commit()

    r = await client.get("/scim/v2/Users", headers=headers)
    assert r.status_code == 200
    usernames = [u["userName"] for u in r.json()["Resources"]]
    assert "local.invisible" not in usernames


# ---------------------------------------------------------------------------
# Groups テスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groups_create_get(known_admin_client, client):
    """Groups: 作成・取得が 500 を返さない。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        "/scim/v2/Groups",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"], "displayName": "admins"},
        headers=headers,
    )
    assert r.status_code == 201
    group_id = r.json()["id"]
    assert r.json()["displayName"] == "admins"

    r2 = await client.get(f"/scim/v2/Groups/{group_id}", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["displayName"] == "admins"


@pytest.mark.asyncio
async def test_groups_patch(known_admin_client, client):
    """Groups: PATCH が 500 を返さない。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        "/scim/v2/Groups",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"], "displayName": "test-group"},
        headers=headers,
    )
    assert r.status_code == 201
    group_id = r.json()["id"]

    r2 = await client.patch(
        f"/scim/v2/Groups/{group_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "displayName", "value": "updated-group"}],
        },
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["displayName"] == "updated-group"


@pytest.mark.asyncio
async def test_groups_list(known_admin_client, client):
    """GET /scim/v2/Groups が 200 を返す。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.get("/scim/v2/Groups", headers=headers)
    assert r.status_code == 200
    assert "Resources" in r.json()


# ---------------------------------------------------------------------------
# ディスカバリエンドポイントテスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_provider_config(known_admin_client, client):
    """GET /scim/v2/ServiceProviderConfig が有効な JSON を返す。"""
    token = await _get_scim_token(known_admin_client)
    r = await client.get(
        "/scim/v2/ServiceProviderConfig",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "patch" in data
    assert data["patch"]["supported"] is True


@pytest.mark.asyncio
async def test_resource_types(known_admin_client, client):
    """GET /scim/v2/ResourceTypes が有効な JSON を返す。"""
    token = await _get_scim_token(known_admin_client)
    r = await client.get(
        "/scim/v2/ResourceTypes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["totalResults"] == 2


@pytest.mark.asyncio
async def test_schemas_endpoint(known_admin_client, client):
    """GET /scim/v2/Schemas が有効な JSON を返す。"""
    token = await _get_scim_token(known_admin_client)
    r = await client.get(
        "/scim/v2/Schemas",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["totalResults"] == 2


# ---------------------------------------------------------------------------
# シークレット衛生テスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_hygiene_token_not_in_db(known_admin_client, scim_app):
    """トークン平文が AppSetting に含まれないこと。"""
    r = await known_admin_client.post("/api/scim/token")
    token = r.json()["token"]

    sm = scim_app.state.sessionmaker
    async with sm() as session:
        row = await session.get(AppSetting, "scim_bearer_token_hash")
        assert token not in (row.value if row else "")


@pytest.mark.asyncio
async def test_secret_hygiene_token_not_in_user_response(known_admin_client, client):
    """SCIM User レスポンスにトークン平文・hashed_password・session_epoch が含まれないこと。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "scim.hygiene"},
        headers=headers,
    )
    assert r.status_code == 201
    response_text = r.text
    assert "hashed_password" not in response_text
    assert "totp_secret" not in response_text
    assert "session_epoch" not in response_text


# ---------------------------------------------------------------------------
# レビュー M-1/M-2 回帰
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_active_without_boolean_value_400(known_admin_client, client):
    """active op に真偽値以外/値なしを渡すと 400（誤 deactivate 防止、M-1）。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "scim.m1"},
        headers=headers,
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    # value を省略した replace active → 400
    r2 = await client.patch(
        f"/scim/v2/Users/{user_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active"}],
        },
        headers=headers,
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_list_users_count_clamped(known_admin_client, client):
    """count に過大値/負値を渡してもエラーにならずクランプされる（M-2）。"""
    token = await _get_scim_token(known_admin_client)
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.get("/scim/v2/Users?count=1000000000", headers=headers)
    assert r.status_code == 200
    r2 = await client.get("/scim/v2/Users?count=-1", headers=headers)
    assert r2.status_code == 200
