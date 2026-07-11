"""SCIM グループ永続化 + グループ→ロール自動付与のテスト。

テスト対象:
  - Groups の DB 永続化（POST / GET / PATCH / DELETE、再起動を跨いだ保持は DB 行で確認）
  - members の add / remove / replace / members[value eq "..."] 形式の remove
  - scim_group_role_map 設定（allowlist・型/値検証・GET/PUT /api/settings）
  - ロール再計算:
      * マップ済みグループのメンバー → 最上位ロール（admin > user）を付与
      * どのマップ済みグループにも属さなくなったら "user" に戻す
      * マップに無いグループ名は何もしない
      * origin != "scim" のユーザーは絶対に変更しない
      * マップが空 {} のときは再計算そのものを行わない（feature off）
  - ロール変更の監査ログ（scim.user.role_change、detail に old/new）
"""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from millicall.auth.security import hash_password
from millicall.config import Settings
from millicall.main import create_app
from millicall.models import AuditLog, ScimGroup, ScimGroupMember, User

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
async def client(scim_app):
    transport = ASGITransport(app=scim_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def admin_client(scim_app):
    """既知パスワードで admin を作成してログインした cookie+CSRF クライアント。"""
    sm = scim_app.state.sessionmaker
    async with sm() as session:
        admin = await session.scalar(select(User).where(User.username == "admin"))
        if admin:
            admin.hashed_password = hash_password("TestAdmin1!")
        else:
            session.add(
                User(
                    username="admin",
                    hashed_password=hash_password("TestAdmin1!"),
                    display_name="Admin",
                    role="admin",
                    origin="local",
                )
            )
        await session.commit()

    transport = ASGITransport(app=scim_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "TestAdmin1!"})
        assert r.status_code == 200, r.text
        csrf = c.cookies.get("millicall_csrf", "")
        c.headers.update({"X-CSRF-Token": csrf})
        yield c


@pytest_asyncio.fixture
async def scim_headers(admin_client):
    """SCIM Bearer トークンを生成してヘッダー dict を返す。"""
    r = await admin_client.post("/api/scim/token")
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


async def _create_scim_user(client: AsyncClient, headers: dict, username: str) -> int:
    """SCIM API 経由で origin="scim" ユーザーを作成して id を返す。"""
    r = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": username},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def _get_role(app, user_id: int) -> str:
    sm = app.state.sessionmaker
    async with sm() as session:
        user = await session.get(User, user_id)
        assert user is not None
        return user.role


async def _set_role_map(admin_client: AsyncClient, role_map: dict) -> None:
    r = await admin_client.put("/api/settings", json={"values": {"scim_group_role_map": role_map}})
    assert r.status_code == 200, r.text


def _group_payload(display_name: str, member_ids: list[int] | None = None) -> dict:
    payload: dict = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "displayName": display_name,
    }
    if member_ids is not None:
        payload["members"] = [{"value": str(uid)} for uid in member_ids]
    return payload


# ---------------------------------------------------------------------------
# Groups 永続化テスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_persisted_in_db(scim_app, client, admin_client, scim_headers):
    """POST /Groups は DB（scim_groups / scim_group_members）に永続化される。"""
    uid = await _create_scim_user(client, scim_headers, "scim.persist")
    r = await client.post(
        "/scim/v2/Groups", json=_group_payload("staff", [uid]), headers=scim_headers
    )
    assert r.status_code == 201, r.text
    group_id = int(r.json()["id"])

    sm = scim_app.state.sessionmaker
    async with sm() as session:
        group = await session.get(ScimGroup, group_id)
        assert group is not None
        assert group.display_name == "staff"
        members = (
            await session.scalars(
                select(ScimGroupMember).where(ScimGroupMember.group_id == group_id)
            )
        ).all()
        assert [m.user_id for m in members] == [uid]

    # GET one / list に members が含まれる
    r2 = await client.get(f"/scim/v2/Groups/{group_id}", headers=scim_headers)
    assert r2.status_code == 200
    assert r2.json()["displayName"] == "staff"
    assert [m["value"] for m in r2.json()["members"]] == [str(uid)]

    r3 = await client.get("/scim/v2/Groups", headers=scim_headers)
    assert r3.status_code == 200
    assert any(g["id"] == str(group_id) for g in r3.json()["Resources"])


@pytest.mark.asyncio
async def test_group_list_filter_by_display_name(client, admin_client, scim_headers):
    """GET /Groups は displayName eq フィルターに対応する（Entra 互換）。"""
    await client.post("/scim/v2/Groups", json=_group_payload("alpha"), headers=scim_headers)
    await client.post("/scim/v2/Groups", json=_group_payload("beta"), headers=scim_headers)

    r = await client.get('/scim/v2/Groups?filter=displayName eq "alpha"', headers=scim_headers)
    assert r.status_code == 200
    names = [g["displayName"] for g in r.json()["Resources"]]
    assert names == ["alpha"]


@pytest.mark.asyncio
async def test_group_member_ignores_unknown_and_non_scim_users(
    scim_app, client, admin_client, scim_headers
):
    """members に存在しない id / origin!=scim の id が来ても保存されない（無視）。"""
    sm = scim_app.state.sessionmaker
    async with sm() as session:
        local_user = User(
            username="local.member",
            hashed_password=hash_password("Passw0rd1"),
            display_name="Local",
            role="user",
            origin="local",
        )
        session.add(local_user)
        await session.commit()
        local_id = local_user.id

    r = await client.post(
        "/scim/v2/Groups",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "mixed",
            "members": [{"value": str(local_id)}, {"value": "999999"}, {"value": "abc"}],
        },
        headers=scim_headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["members"] == []


# ---------------------------------------------------------------------------
# scim_group_role_map 設定テスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_map_setting_editable(admin_client):
    """scim_group_role_map は GET/PUT /api/settings で編集できる。"""
    r = await admin_client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["values"]["scim_group_role_map"] == {}

    await _set_role_map(admin_client, {"millicall-admins": "admin"})
    r2 = await admin_client.get("/api/settings")
    assert r2.json()["values"]["scim_group_role_map"] == {"millicall-admins": "admin"}
    assert "scim_group_role_map" in r2.json()["overridden"]


@pytest.mark.asyncio
async def test_role_map_setting_validation(admin_client):
    """未知ロール・非 dict・空グループ名は 400。"""
    r = await admin_client.put(
        "/api/settings", json={"values": {"scim_group_role_map": {"g": "superuser"}}}
    )
    assert r.status_code == 400

    r2 = await admin_client.put(
        "/api/settings", json={"values": {"scim_group_role_map": ["g", "admin"]}}
    )
    assert r2.status_code == 400

    r3 = await admin_client.put(
        "/api/settings", json={"values": {"scim_group_role_map": {"  ": "admin"}}}
    )
    assert r3.status_code == 400


# ---------------------------------------------------------------------------
# ロール再計算: グループ作成・メンバー変更
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_create_promotes_mapped_members(scim_app, client, admin_client, scim_headers):
    """マップ済みグループの作成でメンバーが admin に昇格し、監査ログが残る。"""
    await _set_role_map(admin_client, {"millicall-admins": "admin"})
    uid = await _create_scim_user(client, scim_headers, "scim.promoted")
    assert await _get_role(scim_app, uid) == "user"

    r = await client.post(
        "/scim/v2/Groups", json=_group_payload("millicall-admins", [uid]), headers=scim_headers
    )
    assert r.status_code == 201, r.text
    assert await _get_role(scim_app, uid) == "admin"

    sm = scim_app.state.sessionmaker
    async with sm() as session:
        logs = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "scim.user.role_change")
            )
        ).all()
        assert len(logs) == 1
        assert logs[0].target_id == str(uid)
        detail = json.loads(logs[0].detail)
        assert detail["old"] == "user"
        assert detail["new"] == "admin"


@pytest.mark.asyncio
async def test_member_add_and_remove_recalc(scim_app, client, admin_client, scim_headers):
    """PATCH add でメンバー昇格、remove（members[value eq]形式）で user に戻る。"""
    await _set_role_map(admin_client, {"millicall-admins": "admin"})
    uid = await _create_scim_user(client, scim_headers, "scim.addremove")

    r = await client.post(
        "/scim/v2/Groups", json=_group_payload("millicall-admins"), headers=scim_headers
    )
    group_id = r.json()["id"]

    # add members → 昇格
    r2 = await client.patch(
        f"/scim/v2/Groups/{group_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "add", "path": "members", "value": [{"value": str(uid)}]}],
        },
        headers=scim_headers,
    )
    assert r2.status_code == 200, r2.text
    assert await _get_role(scim_app, uid) == "admin"

    # Entra 形式の remove（members[value eq "id"]）→ user に戻る
    r3 = await client.patch(
        f"/scim/v2/Groups/{group_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "remove", "path": f'members[value eq "{uid}"]'}],
        },
        headers=scim_headers,
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["members"] == []
    assert await _get_role(scim_app, uid) == "user"


@pytest.mark.asyncio
async def test_member_replace_recalc(scim_app, client, admin_client, scim_headers):
    """replace members で外れた旧メンバーは降格、新メンバーは昇格する。"""
    await _set_role_map(admin_client, {"millicall-admins": "admin"})
    uid_a = await _create_scim_user(client, scim_headers, "scim.rep.a")
    uid_b = await _create_scim_user(client, scim_headers, "scim.rep.b")

    r = await client.post(
        "/scim/v2/Groups", json=_group_payload("millicall-admins", [uid_a]), headers=scim_headers
    )
    group_id = r.json()["id"]
    assert await _get_role(scim_app, uid_a) == "admin"

    r2 = await client.patch(
        f"/scim/v2/Groups/{group_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "members", "value": [{"value": str(uid_b)}]}],
        },
        headers=scim_headers,
    )
    assert r2.status_code == 200, r2.text
    assert await _get_role(scim_app, uid_a) == "user"
    assert await _get_role(scim_app, uid_b) == "admin"


@pytest.mark.asyncio
async def test_display_name_change_recalc(scim_app, client, admin_client, scim_headers):
    """displayName の変更でマップ対象/対象外が切り替わりロールが再計算される。"""
    await _set_role_map(admin_client, {"millicall-admins": "admin"})
    uid = await _create_scim_user(client, scim_headers, "scim.rename")

    r = await client.post(
        "/scim/v2/Groups", json=_group_payload("plain-group", [uid]), headers=scim_headers
    )
    group_id = r.json()["id"]
    assert await _get_role(scim_app, uid) == "user"

    # マップ対象の名前に変更 → 昇格
    r2 = await client.patch(
        f"/scim/v2/Groups/{group_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "displayName", "value": "millicall-admins"}],
        },
        headers=scim_headers,
    )
    assert r2.status_code == 200
    assert await _get_role(scim_app, uid) == "admin"

    # マップ対象外の名前に戻す → 降格
    r3 = await client.patch(
        f"/scim/v2/Groups/{group_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "displayName", "value": "plain-group"}],
        },
        headers=scim_headers,
    )
    assert r3.status_code == 200
    assert await _get_role(scim_app, uid) == "user"


@pytest.mark.asyncio
async def test_group_delete_recalc(scim_app, client, admin_client, scim_headers):
    """DELETE /Groups/{id} でグループが消え、メンバーは user に戻る。"""
    await _set_role_map(admin_client, {"millicall-admins": "admin"})
    uid = await _create_scim_user(client, scim_headers, "scim.deleted")

    r = await client.post(
        "/scim/v2/Groups", json=_group_payload("millicall-admins", [uid]), headers=scim_headers
    )
    group_id = r.json()["id"]
    assert await _get_role(scim_app, uid) == "admin"

    r2 = await client.delete(f"/scim/v2/Groups/{group_id}", headers=scim_headers)
    assert r2.status_code == 204
    assert await _get_role(scim_app, uid) == "user"

    r3 = await client.get(f"/scim/v2/Groups/{group_id}", headers=scim_headers)
    assert r3.status_code == 404

    sm = scim_app.state.sessionmaker
    async with sm() as session:
        members = (
            await session.scalars(
                select(ScimGroupMember).where(ScimGroupMember.group_id == int(group_id))
            )
        ).all()
        assert members == []


# ---------------------------------------------------------------------------
# ロール再計算: マップ設定変更
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_map_setting_change_recalcs_all(scim_app, client, admin_client, scim_headers):
    """マップ設定の変更で既存メンバーのロールが再計算される。"""
    uid = await _create_scim_user(client, scim_headers, "scim.mapchange")
    await client.post(
        "/scim/v2/Groups", json=_group_payload("millicall-admins", [uid]), headers=scim_headers
    )
    # マップ未設定 → 何も起きない
    assert await _get_role(scim_app, uid) == "user"

    # マップ追加 → 昇格
    await _set_role_map(admin_client, {"millicall-admins": "admin"})
    assert await _get_role(scim_app, uid) == "admin"

    # admin → user へマップ変更 → 降格
    await _set_role_map(admin_client, {"millicall-admins": "user"})
    assert await _get_role(scim_app, uid) == "user"


@pytest.mark.asyncio
async def test_empty_map_is_noop(scim_app, client, admin_client, scim_headers):
    """マップを空 {} に戻しても再計算は行われない（feature off、既存ロール保持）。"""
    uid = await _create_scim_user(client, scim_headers, "scim.emptymap")
    await _set_role_map(admin_client, {"millicall-admins": "admin"})
    await client.post(
        "/scim/v2/Groups", json=_group_payload("millicall-admins", [uid]), headers=scim_headers
    )
    assert await _get_role(scim_app, uid) == "admin"

    await _set_role_map(admin_client, {})
    assert await _get_role(scim_app, uid) == "admin"


# ---------------------------------------------------------------------------
# 安全性: マップ外グループ / origin != scim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unmapped_group_does_not_touch_roles(scim_app, client, admin_client, scim_headers):
    """マップに無いグループ名はロールに影響しない。"""
    await _set_role_map(admin_client, {"millicall-admins": "admin"})
    uid = await _create_scim_user(client, scim_headers, "scim.unmapped")

    r = await client.post(
        "/scim/v2/Groups", json=_group_payload("random-team", [uid]), headers=scim_headers
    )
    assert r.status_code == 201
    assert await _get_role(scim_app, uid) == "user"

    sm = scim_app.state.sessionmaker
    async with sm() as session:
        logs = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "scim.user.role_change")
            )
        ).all()
        assert logs == []


@pytest.mark.asyncio
async def test_non_scim_users_never_changed(scim_app, client, admin_client, scim_headers):
    """origin != "scim" のユーザーはメンバー指定されてもロールが変わらない。"""
    sm = scim_app.state.sessionmaker
    async with sm() as session:
        local_admin = User(
            username="local.protected",
            hashed_password=hash_password("Passw0rd1"),
            display_name="Protected",
            role="admin",
            origin="local",
        )
        session.add(local_admin)
        await session.commit()
        local_id = local_admin.id

    # マップに「user へ降格」する設定を入れてもマップ外 origin は無傷
    await _set_role_map(admin_client, {"millicall-admins": "user"})
    r = await client.post(
        "/scim/v2/Groups",
        json=_group_payload("millicall-admins", [local_id]),
        headers=scim_headers,
    )
    assert r.status_code == 201
    assert await _get_role(scim_app, local_id) == "admin"
