from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.auth.schemas import LoginRequest, UserRead
from millicall.auth.security import issue_session, verify_password
from millicall.deps import get_current_user, get_session
from millicall.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
    if user is None or not verify_password(user.hashed_password, body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    token = issue_session(secrets.session_secret, user.id)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    return user


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    settings = request.app.state.settings
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
