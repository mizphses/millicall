from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.auth.security import read_session
from millicall.models import User

if TYPE_CHECKING:
    from millicall.config import Settings
    from millicall.secrets_store import Secrets


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


def get_settings_dep(request: Request) -> "Settings":
    return request.app.state.settings


def get_secrets_dep(request: Request) -> "Secrets":
    return request.app.state.secrets


def get_change_listener(request: Request):
    return request.app.state.change_listener


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    settings = request.app.state.settings
    secrets = request.app.state.secrets
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    uid = read_session(secrets.session_secret, token, settings.session_max_age)
    if uid is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    user = await session.get(User, uid)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user
