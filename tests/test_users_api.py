"""ユーザー管理 API テスト (Task 9a)。"""
from sqlalchemy import select


async def _login_as(client, username, password):
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return client


# ---------------------------------------------------------------------------
# GET /api/users
# ---------------------------------------------------------------------------


async def test_list_users_unauthenticated(client):
    """未認証では 401。"""
    r = await client.get("/api/users")
    assert r.status_code == 401


async def test_list_users_non_admin(client, user_factory):
    """role=user では 403。"""
    username, password = await user_factory(username="regularuser", password="Passw0rd1", role="user")
    await _login_as(client, username, password)
    r = await client.get("/api/users")
    assert r.status_code == 403


async def test_list_users_admin(client, user_factory):
    """管理者は 200 かつ UserRead 形式（秘密フィールドなし）。"""
    username, password = await user_factory(username="listadmin", password="Passw0rd1", role="admin")
    await _login_as(client, username, password)
    r = await client.get("/api/users")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    user_obj = data[0]
    # 必須フィールドの存在確認
    assert "id" in user_obj
    assert "username" in user_obj
    # 秘密フィールドが含まれていないことを確認
    assert "hashed_password" not in user_obj
    assert "totp_secret" not in user_obj
    assert "session_epoch" not in user_obj
    assert "recovery_codes" not in user_obj


# ---------------------------------------------------------------------------
# POST /api/users
# ---------------------------------------------------------------------------


async def test_create_user_success(auth_client):
    """ユーザー作成成功: 201 と UserRead が返る。"""
    r = await auth_client.post(
        "/api/users",
        json={"username": "newuser1", "display_name": "New User", "password": "Passw0rd1", "role": "user"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["username"] == "newuser1"
    assert data["role"] == "user"
    assert data["origin"] == "local"
    assert "hashed_password" not in data


async def test_create_admin_user(auth_client):
    """管理者ユーザーの作成: 201。"""
    r = await auth_client.post(
        "/api/users",
        json={"username": "newadmin1", "display_name": "New Admin", "password": "Passw0rd1", "role": "admin"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["role"] == "admin"


async def test_create_user_duplicate_username(auth_client):
    """同じユーザー名は 409。"""
    payload = {"username": "dupuser", "display_name": "Dup", "password": "Passw0rd1", "role": "user"}
    r1 = await auth_client.post("/api/users", json=payload)
    assert r1.status_code == 201
    r2 = await auth_client.post("/api/users", json=payload)
    assert r2.status_code == 409


async def test_create_user_duplicate_email(auth_client):
    """同じメールアドレスは 409。"""
    r1 = await auth_client.post(
        "/api/users",
        json={"username": "emailuser1", "display_name": "E1", "password": "Passw0rd1", "role": "user", "email": "dup@example.com"},
    )
    assert r1.status_code == 201
    r2 = await auth_client.post(
        "/api/users",
        json={"username": "emailuser2", "display_name": "E2", "password": "Passw0rd1", "role": "user", "email": "dup@example.com"},
    )
    assert r2.status_code == 409


async def test_create_user_weak_password(auth_client):
    """短いパスワードは 422 または 400。"""
    r = await auth_client.post(
        "/api/users",
        json={"username": "weakpwuser", "display_name": "Weak", "password": "abc", "role": "user"},
    )
    assert r.status_code in (400, 422)


async def test_create_user_invalid_role(auth_client):
    """無効なロールは 422。"""
    r = await auth_client.post(
        "/api/users",
        json={"username": "badroleu", "display_name": "Bad", "password": "Passw0rd1", "role": "superuser"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /api/users/{id}
# ---------------------------------------------------------------------------


async def test_patch_user_disable_bumps_epoch(app, auth_client, user_factory):
    """ユーザーを無効化すると session_epoch がインクリメントされる。"""
    from millicall.models import User

    username, password = await user_factory(username="epochtest1", password="Passw0rd1", role="user")
    sm = app.state.sessionmaker
    async with sm() as session:
        user = await session.scalar(select(User).where(User.username == username))
        user_id = user.id
        epoch_before = user.session_epoch

    r = await auth_client.patch(f"/api/users/{user_id}", json={"enabled": False})
    assert r.status_code == 200

    async with sm() as session:
        user = await session.get(User, user_id)
        assert user.session_epoch == epoch_before + 1


async def test_patch_user_role_change_bumps_epoch(app, auth_client, user_factory):
    """ロール変更でも session_epoch がインクリメントされる。"""
    from millicall.models import User

    username, password = await user_factory(username="epochtest2", password="Passw0rd1", role="user")
    sm = app.state.sessionmaker
    async with sm() as session:
        user = await session.scalar(select(User).where(User.username == username))
        user_id = user.id
        epoch_before = user.session_epoch

    r = await auth_client.patch(f"/api/users/{user_id}", json={"role": "admin"})
    assert r.status_code == 200

    async with sm() as session:
        user = await session.get(User, user_id)
        assert user.session_epoch == epoch_before + 1


async def test_patch_last_admin_disable_blocked(app, client, user_factory):
    """唯一の有効な管理者を無効化しようとすると 400。"""
    from millicall.models import User

    username, password = await user_factory(username="onlyadmin", password="Passw0rd1", role="admin")
    await _login_as(client, username, password)

    sm = app.state.sessionmaker
    async with sm() as session:
        user = await session.scalar(select(User).where(User.username == username))
        user_id = user.id
        # 他の管理者(初期admin)を無効化して、onlyadmin が唯一の有効な管理者になるようにする
        other_admins = await session.scalars(
            select(User).where(User.role == "admin", User.id != user_id)
        )
        for other in other_admins:
            other.enabled = False
        await session.commit()

    r = await client.patch(f"/api/users/{user_id}", json={"enabled": False})
    assert r.status_code == 400


async def test_patch_last_admin_demote_blocked(app, client, user_factory):
    """唯一の有効な管理者のロールを変更しようとすると 400。"""
    from millicall.models import User

    username, password = await user_factory(username="onlyadmin2", password="Passw0rd1", role="admin")
    await _login_as(client, username, password)

    sm = app.state.sessionmaker
    async with sm() as session:
        user = await session.scalar(select(User).where(User.username == username))
        user_id = user.id
        # 他の管理者(初期admin)を無効化して、onlyadmin2 が唯一の有効な管理者になるようにする
        other_admins = await session.scalars(
            select(User).where(User.role == "admin", User.id != user_id)
        )
        for other in other_admins:
            other.enabled = False
        await session.commit()

    r = await client.patch(f"/api/users/{user_id}", json={"role": "user"})
    assert r.status_code == 400


async def test_patch_email_uniqueness(auth_client):
    """別ユーザーのメールアドレスに変更しようとすると 409。"""
    r1 = await auth_client.post(
        "/api/users",
        json={"username": "emailpatch1", "display_name": "EP1", "password": "Passw0rd1", "role": "user", "email": "unique@example.com"},
    )
    assert r1.status_code == 201
    r2 = await auth_client.post(
        "/api/users",
        json={"username": "emailpatch2", "display_name": "EP2", "password": "Passw0rd1", "role": "user"},
    )
    assert r2.status_code == 201
    user2_id = r2.json()["id"]

    r3 = await auth_client.patch(f"/api/users/{user2_id}", json={"email": "unique@example.com"})
    assert r3.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/users/{id}/reset-password
# ---------------------------------------------------------------------------


async def test_reset_password_local_user(app, auth_client, user_factory):
    """ローカルユーザーのパスワードリセット: 200 + UserRead、epoch インクリメント。"""
    from millicall.models import User

    username, password = await user_factory(username="localreset", password="Passw0rd1", role="user")
    sm = app.state.sessionmaker
    async with sm() as session:
        user = await session.scalar(select(User).where(User.username == username))
        user_id = user.id
        epoch_before = user.session_epoch

    r = await auth_client.post(f"/api/users/{user_id}/reset-password", json={"new_password": "NewPassw0rd!"})
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
    assert "hashed_password" not in data

    async with sm() as session:
        user = await session.get(User, user_id)
        assert user.session_epoch == epoch_before + 1


async def test_reset_password_saml_user(app, auth_client):
    """SAML ユーザーへのパスワードリセットは 400。"""
    from millicall.auth.security import hash_password
    from millicall.models import User

    sm = app.state.sessionmaker
    async with sm() as session:
        saml_user = User(
            username="samluser",
            hashed_password=hash_password("dummy"),
            display_name="SAML User",
            role="user",
            origin="saml",
        )
        session.add(saml_user)
        await session.commit()
        await session.refresh(saml_user)
        user_id = saml_user.id

    r = await auth_client.post(f"/api/users/{user_id}/reset-password", json={"new_password": "NewPassw0rd!"})
    assert r.status_code == 400


async def test_reset_password_scim_user(app, auth_client):
    """SCIM ユーザーへのパスワードリセットは 400。"""
    from millicall.auth.security import hash_password
    from millicall.models import User

    sm = app.state.sessionmaker
    async with sm() as session:
        scim_user = User(
            username="scimuser",
            hashed_password=hash_password("dummy"),
            display_name="SCIM User",
            role="user",
            origin="scim",
        )
        session.add(scim_user)
        await session.commit()
        await session.refresh(scim_user)
        user_id = scim_user.id

    r = await auth_client.post(f"/api/users/{user_id}/reset-password", json={"new_password": "NewPassw0rd!"})
    assert r.status_code == 400


async def test_reset_password_weak(auth_client):
    """短いパスワードは 400 または 422。"""
    r = await auth_client.post(
        "/api/users",
        json={"username": "weakresetuser", "display_name": "Weak", "password": "Passw0rd1", "role": "user"},
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    r2 = await auth_client.post(f"/api/users/{user_id}/reset-password", json={"new_password": "abc"})
    assert r2.status_code in (400, 422)


async def test_reset_password_never_in_response(auth_client):
    """レスポンスにパスワード文字列が含まれない。"""
    r = await auth_client.post(
        "/api/users",
        json={"username": "nopwinresp", "display_name": "NP", "password": "Passw0rd1", "role": "user"},
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    new_pw = "MySecretPw99!"
    r2 = await auth_client.post(f"/api/users/{user_id}/reset-password", json={"new_password": new_pw})
    assert r2.status_code == 200
    assert new_pw not in r2.text


# ---------------------------------------------------------------------------
# DELETE /api/users/{id}
# ---------------------------------------------------------------------------


async def test_delete_user_success(app, auth_client):
    """非管理者ユーザーの削除: 204。"""
    from millicall.models import User

    r = await auth_client.post(
        "/api/users",
        json={"username": "deleteme", "display_name": "Del", "password": "Passw0rd1", "role": "user"},
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    r2 = await auth_client.delete(f"/api/users/{user_id}")
    assert r2.status_code == 204

    sm = app.state.sessionmaker
    async with sm() as session:
        user = await session.get(User, user_id)
        assert user is None


async def test_delete_self_blocked(app, client, user_factory):
    """管理者が自分自身を削除しようとすると 400。"""
    from millicall.models import User

    username, password = await user_factory(username="selfdelete", password="Passw0rd1", role="admin")
    await _login_as(client, username, password)

    sm = app.state.sessionmaker
    async with sm() as session:
        user = await session.scalar(select(User).where(User.username == username))
        user_id = user.id

    r = await client.delete(f"/api/users/{user_id}")
    assert r.status_code == 400


async def test_delete_last_admin_blocked(app, client, user_factory):
    """唯一の管理者(自分以外)を削除しようとすると 400。

    シナリオ:
    - admin_a と admin_b の 2 人を作成。
    - admin_b でログイン。
    - DB で admin_a 以外の全 admin(admin_b と初期 admin)を無効化 → admin_a が唯一の有効 admin。
    - admin_b でログインしたクライアントとして admin_a を削除しようとする → 400。
    ただし admin_b が無効化されているとセッションが revoked になるため、
    代わりに admin_b が有効な状態で admin_a を唯一の有効 admin にする方法を使う:
    - admin_a を有効な唯一の admin にする。
    - admin_b を有効な admin としてログインしたまま admin_a を削除しようとする
      → これは成功してしまう(admin_b がまだ有効 admin として残るから)。
    結論: last-admin guard を DELETE でテストするには:
    - admin_b でログインして admin_a を削除。admin_b が唯一に。
    - admin_b でもう一度 admin_a を削除しようとする → 404(既に削除済み)。
    実際のテスト: admin_x(唯一 enabled admin) が admin_y(別 admin)から削除されることを防ぐために
    admin_x と admin_y が両方 enabled のときは削除できる。
    → 唯一 enabled admin の削除ブロックは:
      「削除しようとする対象が唯一の enabled admin」のとき。
      「削除操作を行う人」も admin でないと API にアクセスできない。
      → 削除操作を行う人が admin → 2 人以上の admin がいる → guard は発動しない。
    → 唯一可能なシナリオ: 削除操作者 admin_b が有効で、ターゲット admin_a が唯一の enabled admin。
      これは admin_b も enabled → 2 人なので guard 発動しない。
    よって DELETE last-admin guard は self-delete シナリオでのみ観測できる。
    本テストでは: admin_a が唯一の enabled admin の状態で admin_a が自分を DELETE → 400(self-delete)。
    """
    from millicall.models import User

    admin_a_name, admin_a_pw = await user_factory(
        username="lastadmindel", password="Passw0rd1", role="admin"
    )
    await _login_as(client, admin_a_name, admin_a_pw)

    sm = app.state.sessionmaker
    async with sm() as session:
        admin_a = await session.scalar(select(User).where(User.username == admin_a_name))
        admin_a_id = admin_a.id
        # admin_a 以外の全 admin を無効化 → admin_a が唯一の有効 admin
        others = await session.scalars(
            select(User).where(User.role == "admin", User.id != admin_a_id)
        )
        for other in others:
            other.enabled = False
        await session.commit()

    # admin_a が自分を削除しようとする → 400(self-delete が先, last-admin guard も適用可)
    r = await client.delete(f"/api/users/{admin_a_id}")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Audit log tests
# ---------------------------------------------------------------------------


async def test_audit_create_recorded(app, auth_client):
    """ユーザー作成時に audit log が記録される。"""
    from millicall.models import AuditLog

    r = await auth_client.post(
        "/api/users",
        json={"username": "auditcreate", "display_name": "AC", "password": "Passw0rd1", "role": "user"},
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    sm = app.state.sessionmaker
    async with sm() as session:
        log = await session.scalar(
            select(AuditLog)
            .where(AuditLog.action == "user.create")
            .where(AuditLog.target_id == str(user_id))
        )
    assert log is not None


async def test_audit_update_recorded(app, auth_client):
    """ユーザー更新時に audit log が記録される。"""
    from millicall.models import AuditLog

    r = await auth_client.post(
        "/api/users",
        json={"username": "auditupdate", "display_name": "AU", "password": "Passw0rd1", "role": "user"},
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    r2 = await auth_client.patch(f"/api/users/{user_id}", json={"display_name": "Updated Name"})
    assert r2.status_code == 200

    sm = app.state.sessionmaker
    async with sm() as session:
        log = await session.scalar(
            select(AuditLog)
            .where(AuditLog.action == "user.update")
            .where(AuditLog.target_id == str(user_id))
        )
    assert log is not None


async def test_audit_reset_password_recorded(app, auth_client):
    """パスワードリセット時に audit log が記録され、パスワードが含まれない。"""
    from millicall.models import AuditLog

    r = await auth_client.post(
        "/api/users",
        json={"username": "auditreset", "display_name": "AR", "password": "Passw0rd1", "role": "user"},
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    new_pw = "ResetPw99!"
    r2 = await auth_client.post(
        f"/api/users/{user_id}/reset-password", json={"new_password": new_pw}
    )
    assert r2.status_code == 200

    sm = app.state.sessionmaker
    async with sm() as session:
        log = await session.scalar(
            select(AuditLog)
            .where(AuditLog.action == "user.reset_password")
            .where(AuditLog.target_id == str(user_id))
        )
    assert log is not None
    if log.detail:
        assert new_pw not in log.detail


async def test_audit_delete_recorded(app, auth_client):
    """ユーザー削除時に audit log が記録される。"""
    from millicall.models import AuditLog

    r = await auth_client.post(
        "/api/users",
        json={"username": "auditdelete", "display_name": "AD", "password": "Passw0rd1", "role": "user"},
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    r2 = await auth_client.delete(f"/api/users/{user_id}")
    assert r2.status_code == 204

    sm = app.state.sessionmaker
    async with sm() as session:
        log = await session.scalar(
            select(AuditLog)
            .where(AuditLog.action == "user.delete")
            .where(AuditLog.target_id == str(user_id))
        )
    assert log is not None
