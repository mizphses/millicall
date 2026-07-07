"""SCIM 2.0 プロビジョニングサーバ（RFC 7643 / RFC 7644）。

エンドポイント:
  POST /api/scim/token          — Bearer トークン生成（cookie+admin 認証、CSRF 保護済み）

  GET  /scim/v2/ServiceProviderConfig — 静的ディスカバリ
  GET  /scim/v2/ResourceTypes         — 静的ディスカバリ
  GET  /scim/v2/Schemas               — 静的ディスカバリ

  GET    /scim/v2/Users               — ユーザー一覧（filter 対応）
  POST   /scim/v2/Users               — ユーザー作成
  GET    /scim/v2/Users/{id}          — ユーザー取得
  PUT    /scim/v2/Users/{id}          — ユーザー全置換
  PATCH  /scim/v2/Users/{id}          — ユーザー部分更新
  DELETE /scim/v2/Users/{id}          — ユーザー無効化（deactivate）

  GET    /scim/v2/Groups              — グループ一覧（最小実装）
  POST   /scim/v2/Groups             — グループ作成（最小実装）
  GET    /scim/v2/Groups/{id}        — グループ取得（最小実装）
  PATCH  /scim/v2/Groups/{id}        — グループ更新（最小実装）

セキュリティ設計:
  - Bearer トークン: 平文は生成時に一度だけ返す。DB には Argon2 ハッシュのみ保存。
  - scim_enabled=False の場合、全 SCIM エンドポイントは 404 を返す。
  - origin="scim" のユーザーのみ SCIM mutating 操作の対象（local/saml は 404 扱い）。
  - active=false / DELETE → enabled=False + bump_session_epoch → 即時セッション失効。

グループ→ロールマッピング:
  グループは薄いインメモリストアとして実装する（DB 永続化なし）。
  displayName が "admins" のグループを管理者グループとして扱う。
  グループメンバー追加時にそのユーザーの role を "admin" に昇格させる運用は
  IdP 側の設定に委ねる（SCIM Groups は今回ロール昇格を行わない）。
  「Groups は 500 を返さない」「基本 CRUD が動く」レベルの最小実装。
"""

import secrets
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.audit import get_client_ip, record_audit
from millicall.auth.security import bump_session_epoch, hash_password
from millicall.deps import get_session, require_admin
from millicall.gen import generate_password
from millicall.models import AppSetting, User

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# AppSetting の key: Argon2 ハッシュ済み SCIM Bearer トークン。平文は格納しない。
_SCIM_TOKEN_HASH_KEY = "scim_bearer_token_hash"

# SCIM スキーマ URI
_SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
_SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
_SCHEMA_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
_SCHEMA_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
_SCHEMA_PATCHOP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"

_hasher = PasswordHasher()

# インメモリ グループストア（Groups の最小実装; 再起動でリセット）
# {group_id: {"id": str, "displayName": str, "members": list}}
_groups: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# ルーター宣言
# ---------------------------------------------------------------------------

# /api/scim/token は cookie+admin 認証 + CSRF 保護（/api/* なので CSRF 対象）。
api_router = APIRouter(prefix="/api/scim", tags=["scim-admin"])

# /scim/v2/* は Bearer 認証（CSRF 免除済み）。
scim_router = APIRouter(prefix="/scim/v2", tags=["scim"])


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------


def _scim_error(status_code: int, detail: str, scim_type: str | None = None) -> JSONResponse:
    """SCIM エラーレスポンス（RFC 7644 §3.12）。"""
    body: dict[str, Any] = {
        "schemas": [_SCHEMA_ERROR],
        "status": str(status_code),
        "detail": detail,
    }
    if scim_type:
        body["scimType"] = scim_type
    return JSONResponse(status_code=status_code, content=body)


async def _get_token_hash(session: AsyncSession) -> str | None:
    """AppSetting から SCIM トークンハッシュを取得する。"""
    row = await session.get(AppSetting, _SCIM_TOKEN_HASH_KEY)
    return row.value if row is not None else None


async def _verify_bearer(request: Request, session: AsyncSession) -> None:
    """Authorization: Bearer <token> を検証する。

    検証失敗または未設定の場合は SCIM 形式の 401 を raise する。
    scim_enabled=False の場合は 404 を raise する（feature off 扱い）。
    """
    settings = request.app.state.settings
    if not settings.scim_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SCIM not enabled")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
            detail="Missing Bearer token",
        )
    provided_token = auth_header[7:].strip()

    token_hash = await _get_token_hash(session)
    if token_hash is None:
        # トークン未設定 = SCIM 無効
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
            detail="SCIM not configured",
        )

    try:
        _hasher.verify(token_hash, provided_token)
    except VerifyMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
            detail="Invalid Bearer token",
        ) from exc


# ---------------------------------------------------------------------------
# SCIM User 変換
# ---------------------------------------------------------------------------


def _user_to_scim(user: User, base_url: str) -> dict[str, Any]:
    """User モデルを SCIM User 表現に変換する。

    機密フィールド（hashed_password / totp_secret / session_epoch）は含めない。
    """
    location = f"{base_url}/scim/v2/Users/{user.id}"
    name_parts = user.display_name.split(" ", 1) if user.display_name else ["", ""]
    given = name_parts[0]
    family = name_parts[1] if len(name_parts) > 1 else ""
    emails = []
    if user.email:
        emails = [{"value": user.email, "primary": True}]
    return {
        "schemas": [_SCHEMA_USER],
        "id": str(user.id),
        "userName": user.username,
        "displayName": user.display_name,
        "name": {
            "formatted": user.display_name,
            "givenName": given,
            "familyName": family,
        },
        "emails": emails,
        "active": user.enabled,
        "externalId": user.external_id,
        "meta": {
            "resourceType": "User",
            "location": location,
        },
    }


# ---------------------------------------------------------------------------
# フィルターパーサー（最小実装）
# ---------------------------------------------------------------------------


def _parse_simple_filter(filter_str: str) -> tuple[str, str] | None:
    """SCIM フィルター文字列をパースして (attribute, value) を返す。

    対応形式: `attribute eq "value"` または `attribute eq value`
    対応属性: userName / emails.value / externalId

    非対応形式（複合・not 等）は None を返す（呼び出し元が 400 を返す）。
    """
    supported = {"userName", "emails.value", "externalId"}
    parts = filter_str.strip().split(None, 2)
    if len(parts) != 3:  # noqa: PLR2004
        return None
    attr, op, raw_val = parts
    if op.lower() != "eq":
        return None
    if attr not in supported:
        return None
    # クォートを除去
    val = raw_val.strip().strip('"').strip("'")
    return (attr, val)


# ---------------------------------------------------------------------------
# 非活性化ヘルパー
# ---------------------------------------------------------------------------


async def _deactivate_user(user: User, session: AsyncSession, *, actor_label: str, ip: str | None, action: str) -> None:
    """ユーザーを無効化し、全セッションを即時失効させる。

    enabled=False + bump_session_epoch の両方を必ず実行する。
    audit は呼び出し元でコミット前に記録する。
    """
    user.enabled = False
    bump_session_epoch(user)
    await record_audit(
        session,
        actor_user_id=None,
        actor_label=actor_label,
        action=action,
        target_type="user",
        target_id=str(user.id),
        detail={"username": user.username},
        ip_address=ip,
    )


# ---------------------------------------------------------------------------
# Admin エンドポイント: トークン生成
# ---------------------------------------------------------------------------


class _TokenResponse(BaseModel):
    token: str


@api_router.post("/token", response_model=_TokenResponse, status_code=201)
async def rotate_scim_token(
    request: Request,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> _TokenResponse:
    """SCIM Bearer トークンを（再）生成する。

    平文トークンはこのレスポンスで一度だけ返す。
    DB には Argon2 ハッシュのみ保存する（平文は保存・ログ出力しない）。
    監査: scim.token.rotate
    """
    plaintext = f"scim_{generate_password(48)}"
    token_hash = _hasher.hash(plaintext)

    # AppSetting にハッシュを保存（upsert）
    row = await session.get(AppSetting, _SCIM_TOKEN_HASH_KEY)
    if row is None:
        row = AppSetting(
            key=_SCIM_TOKEN_HASH_KEY,
            value=token_hash,
            description="SCIM Bearer トークンの Argon2 ハッシュ（平文は保存しない）",
        )
        session.add(row)
    else:
        row.value = token_hash

    await record_audit(
        session,
        actor_user_id=_admin.id,
        actor_label=_admin.username,
        action="scim.token.rotate",
        ip_address=get_client_ip(request),
    )
    await session.commit()

    # 平文を一度だけ返す。ログ・audit の detail には含めない。
    return _TokenResponse(token=plaintext)


# ---------------------------------------------------------------------------
# ディスカバリエンドポイント
# ---------------------------------------------------------------------------


@scim_router.get("/ServiceProviderConfig")
async def service_provider_config(request: Request, session: AsyncSession = Depends(get_session)):
    await _verify_bearer(request, session)
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": "",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 1000},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "OAuth Bearer Token",
                "description": "Authentication scheme using the OAuth Bearer Token Standard",
            }
        ],
        "meta": {"resourceType": "ServiceProviderConfig"},
    }


@scim_router.get("/ResourceTypes")
async def resource_types(request: Request, session: AsyncSession = Depends(get_session)):
    await _verify_bearer(request, session)
    base = str(request.base_url).rstrip("/")
    return {
        "schemas": [_SCHEMA_LIST],
        "totalResults": 2,
        "Resources": [
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": "User",
                "name": "User",
                "endpoint": "/scim/v2/Users",
                "schema": _SCHEMA_USER,
                "meta": {"resourceType": "ResourceType", "location": f"{base}/scim/v2/ResourceTypes/User"},
            },
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": "Group",
                "name": "Group",
                "endpoint": "/scim/v2/Groups",
                "schema": _SCHEMA_GROUP,
                "meta": {"resourceType": "ResourceType", "location": f"{base}/scim/v2/ResourceTypes/Group"},
            },
        ],
    }


@scim_router.get("/Schemas")
async def schemas(request: Request, session: AsyncSession = Depends(get_session)):
    await _verify_bearer(request, session)
    return {
        "schemas": [_SCHEMA_LIST],
        "totalResults": 2,
        "Resources": [
            {
                "id": _SCHEMA_USER,
                "name": "User",
                "description": "User account",
                "attributes": [
                    {"name": "userName", "type": "string", "required": True},
                    {"name": "displayName", "type": "string"},
                    {"name": "emails", "type": "complex", "multiValued": True},
                    {"name": "active", "type": "boolean"},
                    {"name": "externalId", "type": "string"},
                ],
                "meta": {"resourceType": "Schema"},
            },
            {
                "id": _SCHEMA_GROUP,
                "name": "Group",
                "description": "Group",
                "attributes": [
                    {"name": "displayName", "type": "string", "required": True},
                    {"name": "members", "type": "complex", "multiValued": True},
                ],
                "meta": {"resourceType": "Schema"},
            },
        ],
    }


# ---------------------------------------------------------------------------
# Users: 一覧取得
# ---------------------------------------------------------------------------


@scim_router.get("/Users")
async def list_users(  # noqa: N802
    request: Request,
    filter: str | None = None,  # noqa: A002
    startIndex: int = 1,  # noqa: N803
    count: int = 100,
    session: AsyncSession = Depends(get_session),
):
    await _verify_bearer(request, session)
    base = str(request.base_url).rstrip("/")

    # SCIM ビューは origin="scim" のユーザーのみ
    stmt = select(User).where(User.origin == "scim")

    if filter:
        parsed = _parse_simple_filter(filter)
        if parsed is None:
            return _scim_error(400, f"Filter not supported: {filter}", "invalidFilter")
        attr, val = parsed
        if attr == "userName":
            stmt = stmt.where(User.username == val)
        elif attr == "emails.value":
            stmt = stmt.where(User.email == val)
        elif attr == "externalId":
            stmt = stmt.where(User.external_id == val)

    # 総数取得
    all_users = (await session.scalars(stmt)).all()
    total = len(all_users)

    # ページング（startIndex は 1-based）
    start = max(startIndex, 1)
    idx = start - 1
    paged = all_users[idx : idx + count]

    resources = [_user_to_scim(u, base) for u in paged]
    return {
        "schemas": [_SCHEMA_LIST],
        "totalResults": total,
        "startIndex": start,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


# ---------------------------------------------------------------------------
# Users: 単体取得
# ---------------------------------------------------------------------------


@scim_router.get("/Users/{user_id}")
async def get_user(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    await _verify_bearer(request, session)
    base = str(request.base_url).rstrip("/")
    user = await session.get(User, user_id)
    if user is None or user.origin != "scim":
        return _scim_error(404, f"User {user_id} not found")
    return _user_to_scim(user, base)


# ---------------------------------------------------------------------------
# Users: 作成
# ---------------------------------------------------------------------------


@scim_router.post("/Users", status_code=201)
async def create_user(
    body: dict,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    await _verify_bearer(request, session)
    base = str(request.base_url).rstrip("/")

    # userName は必須
    username = body.get("userName", "").strip()
    if not username:
        return _scim_error(400, "userName is required", "invalidValue")

    # displayName 組み立て: name.formatted > displayName > givenName+familyName
    display_name = _extract_display_name(body)

    # email 抽出（primary 優先）
    email = _extract_primary_email(body)

    # active（デフォルト True）
    active = bool(body.get("active", True))

    # externalId
    external_id = body.get("externalId") or None

    # 重複チェック（userName）
    existing_un = await session.scalar(select(User).where(User.username == username))
    if existing_un is not None:
        return _scim_error(409, f"userName '{username}' already exists", "uniqueness")

    # 重複チェック（email）
    if email:
        existing_em = await session.scalar(select(User).where(User.email == email))
        if existing_em is not None:
            return _scim_error(409, f"email '{email}' already exists", "uniqueness")

    # 使用不可パスワード（SCIM ユーザーはローカルログイン不可）
    unusable_pw = hash_password(f"__scim_unusable_{secrets.token_hex(16)}__")

    user = User(
        username=username,
        hashed_password=unusable_pw,
        display_name=display_name,
        role="user",  # SCIM 作成ユーザーは "user" デフォルト
        origin="scim",
        email=email,
        enabled=active,
        external_id=external_id,
        session_epoch=0,
    )
    session.add(user)
    await session.flush()  # id を確定させる

    await record_audit(
        session,
        actor_user_id=None,
        actor_label="scim",
        action="scim.user.create",
        target_type="user",
        target_id=str(user.id),
        detail={"username": username, "email": email},
        ip_address=get_client_ip(request),
    )
    await session.commit()

    scim_user = _user_to_scim(user, base)
    location = scim_user["meta"]["location"]
    response.headers["Location"] = location
    return JSONResponse(content=scim_user, status_code=201, headers={"Location": location})


# ---------------------------------------------------------------------------
# Users: 全置換 (PUT)
# ---------------------------------------------------------------------------


@scim_router.put("/Users/{user_id}")
async def put_user(
    user_id: int,
    body: dict,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    await _verify_bearer(request, session)
    base = str(request.base_url).rstrip("/")
    ip = get_client_ip(request)

    user = await session.get(User, user_id)
    if user is None or user.origin != "scim":
        return _scim_error(404, f"User {user_id} not found")

    # フィールド更新
    new_display = _extract_display_name(body)
    new_email = _extract_primary_email(body)
    new_active = bool(body.get("active", True))
    new_external_id = body.get("externalId") or None
    new_username = body.get("userName", user.username).strip()

    # userName 重複チェック（自分以外）
    if new_username != user.username:
        dup = await session.scalar(select(User).where(User.username == new_username, User.id != user_id))
        if dup is not None:
            return _scim_error(409, f"userName '{new_username}' already exists", "uniqueness")
        user.username = new_username

    # email 重複チェック（自分以外）
    if new_email and new_email != user.email:
        dup_em = await session.scalar(select(User).where(User.email == new_email, User.id != user_id))
        if dup_em is not None:
            return _scim_error(409, f"email '{new_email}' already exists", "uniqueness")

    was_active = user.enabled
    user.display_name = new_display
    user.email = new_email
    user.external_id = new_external_id

    if not new_active and was_active:
        # active が false に変わった → 即時セッション失効
        await _deactivate_user(user, session, actor_label="scim", ip=ip, action="scim.user.deactivate")
    else:
        user.enabled = new_active
        await record_audit(
            session,
            actor_user_id=None,
            actor_label="scim",
            action="scim.user.update",
            target_type="user",
            target_id=str(user.id),
            detail={"username": user.username},
            ip_address=ip,
        )

    await session.commit()
    return _user_to_scim(user, base)


# ---------------------------------------------------------------------------
# Users: 部分更新 (PATCH)
# ---------------------------------------------------------------------------


@scim_router.patch("/Users/{user_id}")
async def patch_user(
    user_id: int,
    body: dict,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    await _verify_bearer(request, session)
    base = str(request.base_url).rstrip("/")
    ip = get_client_ip(request)

    user = await session.get(User, user_id)
    if user is None or user.origin != "scim":
        return _scim_error(404, f"User {user_id} not found")

    operations = body.get("Operations", [])
    if not isinstance(operations, list):
        return _scim_error(400, "Operations must be a list", "invalidValue")

    changed = False
    deactivated = False

    for op in operations:
        op_name = str(op.get("op", "")).lower()
        path = op.get("path", "")
        value = op.get("value")

        if op_name not in {"replace", "add", "remove"}:
            return _scim_error(400, f"Unsupported op: {op.get('op')}", "invalidValue")

        if op_name == "replace":
            # パスなしの replace: value が dict の場合は複数属性を一括更新
            if not path and isinstance(value, dict):
                if "active" in value:
                    new_active = bool(value["active"])
                    was = user.enabled
                    if not new_active and was:
                        user.enabled = False
                        bump_session_epoch(user)
                        deactivated = True
                    else:
                        user.enabled = new_active
                        changed = True
                if "displayName" in value:
                    user.display_name = str(value["displayName"])
                    changed = True
                if "emails" in value:
                    em = _extract_primary_email({"emails": value["emails"]})
                    if em:
                        user.email = em
                    changed = True
                if "userName" in value:
                    user.username = str(value["userName"])
                    changed = True
                if "externalId" in value:
                    user.external_id = value["externalId"] or None
                    changed = True
            elif path == "active":
                was = user.enabled
                new_val = bool(value)
                if not new_val and was:
                    user.enabled = False
                    bump_session_epoch(user)
                    deactivated = True
                else:
                    user.enabled = new_val
                    changed = True
            elif path == "displayName":
                user.display_name = str(value)
                changed = True
            elif path in {"name.formatted", "name"}:
                if isinstance(value, dict):
                    user.display_name = value.get("formatted", user.display_name)
                else:
                    user.display_name = str(value)
                changed = True
            elif path == "emails" or path == "emails[type eq \"work\"].value" or path.startswith("emails"):
                if isinstance(value, list):
                    em = _extract_primary_email({"emails": value})
                    if em:
                        user.email = em
                elif isinstance(value, str):
                    user.email = value
                changed = True
            elif path == "userName":
                user.username = str(value)
                changed = True
            elif path == "externalId":
                user.external_id = value or None
                changed = True
            # 未知のパスは無視（SCIM 仕様では unknown path は ignoreUnknownPaths or 400）

        elif op_name in {"add", "remove"} and (
            path == "active" or (not path and isinstance(value, dict) and "active" in value)
        ):
            act_val = bool(value) if path == "active" else bool(value.get("active"))  # type: ignore[union-attr]
            was = user.enabled
            if not act_val and was:
                user.enabled = False
                bump_session_epoch(user)
                deactivated = True
            else:
                user.enabled = act_val
                changed = True

    # 監査
    if deactivated:
        await record_audit(
            session,
            actor_user_id=None,
            actor_label="scim",
            action="scim.user.deactivate",
            target_type="user",
            target_id=str(user.id),
            detail={"username": user.username},
            ip_address=ip,
        )
    elif changed:
        await record_audit(
            session,
            actor_user_id=None,
            actor_label="scim",
            action="scim.user.patch",
            target_type="user",
            target_id=str(user.id),
            detail={"username": user.username},
            ip_address=ip,
        )

    await session.commit()
    return _user_to_scim(user, base)


# ---------------------------------------------------------------------------
# Users: 削除（= deactivate）
# ---------------------------------------------------------------------------


@scim_router.delete("/Users/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """SCIM DELETE = deactivate（有効無効化 + セッション失効）。

    SCIM DELETE はハード削除ではなく deactivate として実装する。
    理由: 可逆性・監査ログ保全・FK 参照の安全性。
    IdP 側の再プロビジョニングで再活性化したい場合は PUT/PATCH active:true で対応する。
    """
    await _verify_bearer(request, session)
    ip = get_client_ip(request)

    user = await session.get(User, user_id)
    if user is None or user.origin != "scim":
        return _scim_error(404, f"User {user_id} not found")

    await _deactivate_user(user, session, actor_label="scim", ip=ip, action="scim.user.deactivate")
    await record_audit(
        session,
        actor_user_id=None,
        actor_label="scim",
        action="scim.user.delete",
        target_type="user",
        target_id=str(user.id),
        detail={"username": user.username},
        ip_address=ip,
    )
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Groups: 最小実装（インメモリ）
# ---------------------------------------------------------------------------


@scim_router.get("/Groups")
async def list_groups(request: Request, session: AsyncSession = Depends(get_session)):
    await _verify_bearer(request, session)
    base = str(request.base_url).rstrip("/")
    groups = list(_groups.values())
    return {
        "schemas": [_SCHEMA_LIST],
        "totalResults": len(groups),
        "startIndex": 1,
        "itemsPerPage": len(groups),
        "Resources": [_group_to_scim(g, base) for g in groups],
    }


@scim_router.get("/Groups/{group_id}")
async def get_group(group_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    await _verify_bearer(request, session)
    base = str(request.base_url).rstrip("/")
    g = _groups.get(group_id)
    if g is None:
        return _scim_error(404, f"Group {group_id} not found")
    return _group_to_scim(g, base)


@scim_router.post("/Groups", status_code=201)
async def create_group(
    body: dict,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    await _verify_bearer(request, session)
    base = str(request.base_url).rstrip("/")
    display_name = body.get("displayName", "").strip()
    if not display_name:
        return _scim_error(400, "displayName is required", "invalidValue")

    group_id = secrets.token_urlsafe(12)
    members = body.get("members", [])
    g = {"id": group_id, "displayName": display_name, "members": members}
    _groups[group_id] = g

    scim_group = _group_to_scim(g, base)
    location = scim_group["meta"]["location"]
    response.headers["Location"] = location
    return JSONResponse(content=scim_group, status_code=201, headers={"Location": location})


@scim_router.patch("/Groups/{group_id}")
async def patch_group(
    group_id: str,
    body: dict,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    await _verify_bearer(request, session)
    base = str(request.base_url).rstrip("/")
    g = _groups.get(group_id)
    if g is None:
        return _scim_error(404, f"Group {group_id} not found")

    for op in body.get("Operations", []):
        op_name = str(op.get("op", "")).lower()
        path = op.get("path", "")
        value = op.get("value")
        if op_name == "replace":
            if path == "displayName" and isinstance(value, str):
                g["displayName"] = value
            elif path == "members" and isinstance(value, list):
                g["members"] = value
            elif not path and isinstance(value, dict):
                if "displayName" in value:
                    g["displayName"] = value["displayName"]
                if "members" in value:
                    g["members"] = value["members"]
        elif op_name == "add" and path == "members" and isinstance(value, list):
            existing_refs = {m.get("value") for m in g["members"]}
            for m in value:
                if m.get("value") not in existing_refs:
                    g["members"].append(m)
        elif op_name == "remove" and path == "members":
            remove_vals = {v.get("value") for v in (value if isinstance(value, list) else [])}
            if remove_vals:
                g["members"] = [m for m in g["members"] if m.get("value") not in remove_vals]
            else:
                g["members"] = []

    return _group_to_scim(g, base)


# ---------------------------------------------------------------------------
# グループ変換
# ---------------------------------------------------------------------------


def _group_to_scim(g: dict, base_url: str) -> dict[str, Any]:
    location = f"{base_url}/scim/v2/Groups/{g['id']}"
    return {
        "schemas": [_SCHEMA_GROUP],
        "id": g["id"],
        "displayName": g["displayName"],
        "members": g.get("members", []),
        "meta": {
            "resourceType": "Group",
            "location": location,
        },
    }


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------


def _extract_display_name(body: dict) -> str:
    """SCIM ボディから表示名を抽出する。優先順: name.formatted > displayName > givenName+familyName。"""
    name_obj = body.get("name") or {}
    if isinstance(name_obj, dict):
        formatted = name_obj.get("formatted", "").strip()
        if formatted:
            return formatted
        given = name_obj.get("givenName", "").strip()
        family = name_obj.get("familyName", "").strip()
        if given or family:
            return f"{given} {family}".strip()
    display = body.get("displayName", "").strip()
    if display:
        return display
    return body.get("userName", "").strip()


def _extract_primary_email(body: dict) -> str | None:
    """SCIM ボディから primary メールを抽出する。"""
    emails = body.get("emails", [])
    if not isinstance(emails, list) or not emails:
        return None
    # primary=True のものを優先、なければ最初のもの
    for em in emails:
        if isinstance(em, dict) and em.get("primary"):
            return em.get("value") or None
    first = emails[0]
    if isinstance(first, dict):
        return first.get("value") or None
    return None
