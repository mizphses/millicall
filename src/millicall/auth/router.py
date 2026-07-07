"""認証エンドポイント（ログイン / ログアウト / 自己情報取得）。

TOTP 2FA が有効なユーザーのログインフロー:
  1. POST /login → パスワード検証成功後、{totp_required: true, ticket: <signed>} を返す
     （セッション Cookie はセットしない）
  2. POST /login/totp → ticket + TOTP コードまたはリカバリコードを検証し、
     成功したらセッション Cookie をセットして UserRead を返す

TOTP 2FA が無効なユーザーのログインフロー:
  1. POST /login → パスワード検証成功後、直接セッション Cookie をセットして UserRead を返す
"""
import json

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.audit import get_client_ip, record_audit
from millicall.auth.schemas import LoginRequest, UserRead
from millicall.auth.security import (
    bump_session_epoch,
    hash_password,
    issue_session,
    issue_totp_ticket,
    read_session,
    read_totp_ticket,
    verify_password,
)
from millicall.crypto import SecretBox
from millicall.deps import get_current_user, get_session
from millicall.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

# タイミング均一化: 存在しないユーザーに対してもArgon2を必ず実行し、
# レスポンス時間によるユーザー列挙を防ぐ
_DUMMY_HASH = hash_password("millicall-dummy-timing-guard")

# リカバリコード用 Argon2 ハッシャー（TOTP ルーターと共用せずここで宣言）
_hasher = PasswordHasher()


# ---------------------------------------------------------------------------
# リクエスト / レスポンス スキーマ
# ---------------------------------------------------------------------------


class LoginTotpRequest(BaseModel):
    """TOTP 2 段階ログインのリクエスト。"""

    ticket: str
    code: str


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------


def _check_recovery_code(stored_hashes: list[str], code: str) -> int | None:
    """リカバリコードを Argon2 で比較して一致したインデックスを返す。"""
    for i, h in enumerate(stored_hashes):
        try:
            if _hasher.verify(h, code):
                return i
        except VerifyMismatchError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return None


def _set_session_cookie(response: Response, settings, token: str) -> None:
    """セッション Cookie をセットするヘルパー。"""
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """ログインエンドポイント。

    TOTP が無効な場合は UserRead を直接返す。
    TOTP が有効な場合は {totp_required: true, ticket: <signed>} を返す
    （セッション Cookie はセットしない）。
    """
    settings = request.app.state.settings
    secrets = request.app.state.secrets
    user = await session.scalar(select(User).where(User.username == body.username))
    check_hash = user.hashed_password if user is not None else _DUMMY_HASH
    password_ok = verify_password(check_hash, body.password)
    if user is None or not password_ok:
        # ログイン失敗を監査記録（パスワードは絶対に記録しない）
        await record_audit(
            session,
            actor_user_id=None,
            actor_label=body.username,
            action="login.failure",
            ip_address=get_client_ip(request),
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # TOTP が有効な場合は 2 段階目へ誘導
    if user.totp_enabled:
        ticket = issue_totp_ticket(secrets.session_secret, user.id, user.session_epoch)
        await record_audit(
            session,
            actor_user_id=user.id,
            actor_label=user.username,
            action="login.totp_challenge",
            ip_address=get_client_ip(request),
        )
        await session.commit()
        # セッション Cookie をセットせずにチケットのみ返す
        return {"totp_required": True, "ticket": ticket}

    # TOTP なしのユーザー: 従来どおりセッションを発行
    token = issue_session(secrets.session_secret, user.id, user.session_epoch)
    _set_session_cookie(response, settings, token)
    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="login.success",
        ip_address=get_client_ip(request),
    )
    await session.commit()
    return UserRead.model_validate(user)


@router.post("/login/totp", response_model=UserRead)
async def login_totp(
    body: LoginTotpRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    """TOTP 2 段階ログイン: チケット + TOTP コード / リカバリコードを検証する。

    チケット検証 → ユーザー取得 → enabled / epoch 確認 → コード検証の順で行い、
    いずれかが失敗しても同じ 401 を返す（情報漏洩を防ぐ）。
    """
    settings = request.app.state.settings
    secrets = request.app.state.secrets

    # チケット検証
    ticket_data = read_totp_ticket(
        secrets.session_secret, body.ticket, settings.totp_ticket_max_age
    )
    if ticket_data is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired ticket")

    # ユーザー取得
    user = await session.get(User, ticket_data.uid)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # ユーザー状態チェック（disabled / epoch 変更）
    if not user.enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if user.session_epoch != ticket_data.epoch:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # TOTP コードまたはリカバリコードを検証
    method = _verify_code(user, secrets, body.code)
    if method is None:
        await record_audit(
            session,
            actor_user_id=user.id,
            actor_label=user.username,
            action="login.totp_failure",
            ip_address=get_client_ip(request),
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # リカバリコードの場合は消費済みを DB に反映する（_verify_code が user.recovery_codes を更新済み）
    if method.startswith("recovery:"):
        session.add(user)

    token = issue_session(secrets.session_secret, user.id, user.session_epoch)
    _set_session_cookie(response, settings, token)

    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="login.success",
        detail={"method": method.split(":")[0]},  # "totp" or "recovery"
        ip_address=get_client_ip(request),
    )
    await session.commit()
    return UserRead.model_validate(user)


def _verify_code(user: User, secrets, code: str) -> str | None:
    """TOTP コードまたはリカバリコードを検証する。

    Returns:
        "totp"      : TOTP コードが一致
        "recovery:N": N 番目のリカバリコードが一致（消費済みに更新）
        None        : いずれも一致しない
    """
    if user.totp_secret is None:
        return None

    box = SecretBox(secrets.master_key)
    try:
        plain_secret = box.decrypt(user.totp_secret)
    except Exception:  # noqa: BLE001
        return None

    # TOTP 検証
    totp = pyotp.TOTP(plain_secret)
    if totp.verify(code, valid_window=1):
        return "totp"

    # リカバリコード検証
    if user.recovery_codes is None:
        return None
    try:
        stored: list[str] = json.loads(user.recovery_codes)
    except (json.JSONDecodeError, TypeError):
        return None

    idx = _check_recovery_code(stored, code)
    if idx is None:
        return None

    # 使用済みエントリを消費（None を除外して保存）
    stored[idx] = None  # type: ignore[call-overload]
    user.recovery_codes = json.dumps([h for h in stored if h is not None])
    return f"recovery:{idx}"


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    settings = request.app.state.settings
    # ログアウトは認証必須ではない。可能ならCookieからユーザーを特定して監査記録するが、
    # 特定できない場合も失敗させない。
    actor_user_id: int | None = None
    actor_label = "unknown"
    try:
        secrets = request.app.state.secrets
        token = request.cookies.get(settings.session_cookie_name)
        if token:
            session_data = read_session(
                secrets.session_secret, token, settings.session_max_age
            )
            if session_data is not None:
                user = await session.get(User, session_data.uid)
                if user is not None:
                    actor_user_id = user.id
                    actor_label = user.username
    except Exception:  # noqa: BLE001
        actor_user_id = None
        actor_label = "unknown"
    await record_audit(
        session,
        actor_user_id=actor_user_id,
        actor_label=actor_label,
        action="logout",
        ip_address=get_client_ip(request),
    )
    await session.commit()
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return {"ok": True}


@router.post("/logout-all")
async def logout_all(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """全セッションを失効させてCookieを削除する。"""
    bump_session_epoch(user)
    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="logout.all",
        ip_address=get_client_ip(request),
    )
    await session.commit()
    settings = request.app.state.settings
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
