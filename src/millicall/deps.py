from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.auth.security import SessionData, read_session
from millicall.models import User

if TYPE_CHECKING:
    from millicall.config import Settings
    from millicall.network.client import NetdClient
    from millicall.secrets_store import Secrets


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


def get_settings_dep(request: Request) -> "Settings":
    return request.app.state.settings


def get_secrets_dep(request: Request) -> "Secrets":
    return request.app.state.secrets


def get_secret_box(request: Request):
    from millicall.crypto import SecretBox

    return SecretBox(request.app.state.secrets.master_key)


def get_change_listener(request: Request):
    return request.app.state.change_listener


def get_esl_factory(request: Request):
    return request.app.state.esl_factory


def get_netd_client(request: Request) -> "NetdClient":
    """app.state から NetdClient を取得する FastAPI 依存関係。

    netd UNIX ソケットクライアントを返す。クライアントは接続を遅延生成するため、
    netd が未起動の状態でも依存関係の解決自体は成功する。

    Args:
        request: FastAPI リクエストオブジェクト。

    Returns:
        app.state に設定済みの NetdClient インスタンス。
    """
    return request.app.state.netd_client


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    settings = request.app.state.settings
    secrets = request.app.state.secrets
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    session_data: SessionData | None = read_session(
        secrets.session_secret, token, settings.session_max_age
    )
    if session_data is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    user = await session.get(User, session_data.uid)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    # セッション失効チェック: epochが不一致なら無効
    if user.session_epoch != session_data.epoch:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")
    # 無効化ユーザーチェック
    if not user.enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account disabled")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user
