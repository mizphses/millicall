from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.audit import get_client_ip, record_audit
from millicall.auth.schemas import LoginRequest, UserRead
from millicall.auth.security import (
    bump_session_epoch,
    hash_password,
    issue_session,
    read_session,
    verify_password,
)
from millicall.deps import get_current_user, get_session
from millicall.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

# タイミング均一化: 存在しないユーザーに対してもArgon2を必ず実行し、
# レスポンス時間によるユーザー列挙を防ぐ
_DUMMY_HASH = hash_password("millicall-dummy-timing-guard")


@router.post("/login", response_model=UserRead)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> User:
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
    token = issue_session(secrets.session_secret, user.id, user.session_epoch)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="login.success",
        ip_address=get_client_ip(request),
    )
    await session.commit()
    return user


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
