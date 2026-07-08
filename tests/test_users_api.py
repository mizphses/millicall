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
    username, password = await user_factory(
        username="regularuser", password="Passw0rd1", role="user"
    )
    await _login_as(client, username, password)
    r = await client.get("/api/users")
    assert r.status_code == 403


async def test_list_users_admin(client, user_factory):
    """管理者は 200 かつ UserRead 形式（秘密フィールドなし）。"""
    username, password = await user_factory(
        username="listadmin", password="Passw0rd1", role="admin"
    )
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
        json={
            "username": "newuser1",
            "display_name": "New User",
            "password": "Passw0rd1",
            "role": "user",
        },
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
        json={
            "username": "newadmin1",
            "display_name": "New Admin",
            "password": "Passw0rd1",
            "role": "admin",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["role"] == "admin"


async def test_create_user_duplicate_username(auth_client):
    """同じユーザー名は 409。"""
    payload = {
        "username": "dupuser",
        "display_name": "Dup",
        "password": "Passw0rd1",
        "role": "user",
    }
    r1 = await auth_client.post("/api/users", json=payload)
    assert r1.status_code == 201
    r2 = await auth_client.post("/api/users", json=payload)
    assert r2.status_code == 409


async def test_create_user_duplicate_email(auth_client):
    """同じメールアドレスは 409。"""
    r1 = await auth_client.post(
        "/api/users",
        json={
            "username": "emailuser1",
            "display_name": "E1",
            "password": "Passw0rd1",
            "role": "user",
            "email": "dup@example.com",
        },
    )
    assert r1.status_code == 201
    r2 = await auth_client.post(
        "/api/users",
        json={
            "username": "emailuser2",
            "display_name": "E2",
            "password": "Passw0rd1",
            "role": "user",
            "email": "dup@example.com",
        },
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
        json={
            "username": "badroleu",
            "display_name": "Bad",
            "password": "Passw0rd1",
            "role": "superuser",
        },
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /api/users/{id}
# ---------------------------------------------------------------------------


async def test_patch_user_disable_bumps_epoch(app, auth_client, user_factory):
    """ユーザーを無効化すると session_epoch がインクリメントされる。"""
    from millicall.models import User

    username, password = await user_factory(
        username="epochtest1", password="Passw0rd1", role="user"
    )
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

    username, password = await user_factory(
        username="epochtest2", password="Passw0rd1", role="user"
    )
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

    username, password = await user_factory(
        username="onlyadmin", password="Passw0rd1", role="admin"
    )
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

    username, password = await user_factory(
        username="onlyadmin2", password="Passw0rd1", role="admin"
    )
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
        json={
            "username": "emailpatch1",
            "display_name": "EP1",
            "password": "Passw0rd1",
            "role": "user",
            "email": "unique@example.com",
        },
    )
    assert r1.status_code == 201
    r2 = await auth_client.post(
        "/api/users",
        json={
            "username": "emailpatch2",
            "display_name": "EP2",
            "password": "Passw0rd1",
            "role": "user",
        },
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

    username, password = await user_factory(
        username="localreset", password="Passw0rd1", role="user"
    )
    sm = app.state.sessionmaker
    async with sm() as session:
        user = await session.scalar(select(User).where(User.username == username))
        user_id = user.id
        epoch_before = user.session_epoch

    r = await auth_client.post(
        f"/api/users/{user_id}/reset-password", json={"new_password": "NewPassw0rd!"}
    )
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

    r = await auth_client.post(
        f"/api/users/{user_id}/reset-password", json={"new_password": "NewPassw0rd!"}
    )
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

    r = await auth_client.post(
        f"/api/users/{user_id}/reset-password", json={"new_password": "NewPassw0rd!"}
    )
    assert r.status_code == 400


async def test_reset_password_weak(auth_client):
    """短いパスワードは 400 または 422。"""
    r = await auth_client.post(
        "/api/users",
        json={
            "username": "weakresetuser",
            "display_name": "Weak",
            "password": "Passw0rd1",
            "role": "user",
        },
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    r2 = await auth_client.post(
        f"/api/users/{user_id}/reset-password", json={"new_password": "abc"}
    )
    assert r2.status_code in (400, 422)


async def test_reset_password_never_in_response(auth_client):
    """レスポンスにパスワード文字列が含まれない。"""
    r = await auth_client.post(
        "/api/users",
        json={
            "username": "nopwinresp",
            "display_name": "NP",
            "password": "Passw0rd1",
            "role": "user",
        },
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    new_pw = "MySecretPw99!"
    r2 = await auth_client.post(
        f"/api/users/{user_id}/reset-password", json={"new_password": new_pw}
    )
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
        json={
            "username": "deleteme",
            "display_name": "Del",
            "password": "Passw0rd1",
            "role": "user",
        },
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

    username, password = await user_factory(
        username="selfdelete", password="Passw0rd1", role="admin"
    )
    await _login_as(client, username, password)

    sm = app.state.sessionmaker
    async with sm() as session:
        user = await session.scalar(select(User).where(User.username == username))
        user_id = user.id

    r = await client.delete(f"/api/users/{user_id}")
    assert r.status_code == 400


async def test_delete_last_admin_blocked(app, user_factory):
    """delete_user ルートハンドラーの last-admin guard を直接呼び出してユニットテストする。

    【なぜ直接呼び出しか】
    HTTP スタック経由で last-admin guard（router.py lines 256-263）を到達させることは
    構造的に不可能: ガードに到達するには actor が有効な admin である必要があるが、
    actor が有効な admin の場合は enabled admin が必ず 2 人以上存在するため
    _count_enabled_admins が 1 以下になれない。self-delete 分岐（lines 246-250）が
    先にある問題も別途あるが、上記の理由が本質的な原因。

    【テスト戦略】
    - target: 唯一の enabled admin としてDB に作成する。
    - actor: 別の admin として DB に作成後、DB 上で enabled=False に設定する。
      これにより _count_enabled_admins(session) == 1（target のみ）となる。
      actor.id != target.id なので self-delete 分岐はスキップされ、
      last-admin guard コードパスに正確に到達する。
    - require_admin / get_current_user をバイパスして delete_user を直接 await する。
    - HTTPException(status_code=400, detail="最後の管理者...") が送出されることを検証。
    """
    from unittest.mock import MagicMock

    import pytest
    from fastapi import HTTPException

    from millicall.models import User
    from millicall.users.router import delete_user

    sm = app.state.sessionmaker

    # target: 唯一の有効な管理者
    target_name, _ = await user_factory(
        username="lastadmin_target", password="Passw0rd1", role="admin"
    )
    # actor: 別の管理者（後で DB 上で無効化する）
    actor_name, _ = await user_factory(
        username="lastadmin_actor", password="Passw0rd1", role="admin"
    )

    async with sm() as session:
        target = await session.scalar(select(User).where(User.username == target_name))
        actor = await session.scalar(select(User).where(User.username == actor_name))
        target_id = target.id

        # target 以外のすべての admin（初期 admin 含む）を無効化 →
        # target が唯一の enabled admin になる
        others = await session.scalars(
            select(User).where(User.role == "admin", User.id != target_id)
        )
        for other in others:
            other.enabled = False
        await session.commit()

        # actor オブジェクトを最新状態（enabled=False）で再取得
        await session.refresh(actor)

        # request.client を None にしておくことで get_client_ip が None を返す
        fake_request = MagicMock()
        fake_request.client = None

        # last-admin guard に到達する: actor.id != target.id → self-delete スキップ
        # enabled admin は target のみ → admin_count == 1 → guard 発動
        with pytest.raises(HTTPException) as exc_info:
            await delete_user(
                user_id=target_id,
                request=fake_request,
                session=session,
                current_user=actor,
            )

    assert exc_info.value.status_code == 400
    assert "最後の管理者" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Audit log tests
# ---------------------------------------------------------------------------


async def test_audit_create_recorded(app, auth_client):
    """ユーザー作成時に audit log が記録される。"""
    from millicall.models import AuditLog

    r = await auth_client.post(
        "/api/users",
        json={
            "username": "auditcreate",
            "display_name": "AC",
            "password": "Passw0rd1",
            "role": "user",
        },
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
        json={
            "username": "auditupdate",
            "display_name": "AU",
            "password": "Passw0rd1",
            "role": "user",
        },
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
        json={
            "username": "auditreset",
            "display_name": "AR",
            "password": "Passw0rd1",
            "role": "user",
        },
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
        json={
            "username": "auditdelete",
            "display_name": "AD",
            "password": "Passw0rd1",
            "role": "user",
        },
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
