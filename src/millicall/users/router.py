"""ユーザー管理 API ルーター。"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.audit import get_client_ip, record_audit
from millicall.auth.schemas import UserRead
from millicall.auth.security import bump_session_epoch, hash_password
from millicall.deps import get_current_user, get_session, require_admin
from millicall.models import User

router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[Depends(require_admin)],
)

_VALID_ROLES = {"admin", "user"}


class UserCreate(BaseModel):
    username: str
    display_name: str
    password: str
    role: str
    email: str | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in _VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(_VALID_ROLES)}")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


class UserPatch(BaseModel):
    display_name: str | None = None
    role: str | None = None
    email: str | None = None
    enabled: bool | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(_VALID_ROLES)}")
        return v


class ResetPasswordBody(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("new_password must be at least 8 characters")
        return v


async def _count_enabled_admins(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(User).where(User.role == "admin", User.enabled == True)  # noqa: E712
    ) or 0


@router.get("", response_model=list[UserRead])
async def list_users(session: AsyncSession = Depends(get_session)) -> list[User]:
    result = await session.scalars(select(User).order_by(User.id))
    return list(result)


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> User:
    user = User(
        username=body.username,
        display_name=body.display_name,
        hashed_password=hash_password(body.password),
        role=body.role,
        email=body.email,
        origin="local",
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="username または email が既に使用されています",
        ) from None
    await record_audit(
        session,
        actor_user_id=current_user.id,
        actor_label=current_user.username,
        action="user.create",
        target_type="user",
        target_id=str(user.id),
        detail={"username": body.username, "role": body.role, "origin": "local"},
        ip_address=get_client_ip(request),
    )
    await session.commit()
    await session.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserRead)
async def patch_user(
    user_id: int,
    body: UserPatch,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ユーザーが見つかりません")

    # Last-admin guard
    admin_count = await _count_enabled_admins(session)

    if body.enabled is False and user.enabled and user.role == "admin" and admin_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="最後の管理者は変更できません",
        )

    if (
        body.role is not None
        and body.role != "admin"
        and user.role == "admin"
        and user.enabled
        and admin_count <= 1
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="最後の管理者は変更できません",
        )

    changed_fields = []
    should_bump_epoch = False

    if body.display_name is not None and body.display_name != user.display_name:
        user.display_name = body.display_name
        changed_fields.append("display_name")

    if body.role is not None and body.role != user.role:
        user.role = body.role
        changed_fields.append("role")
        should_bump_epoch = True

    if body.email is not None and body.email != user.email:
        user.email = body.email
        changed_fields.append("email")

    if body.enabled is not None and body.enabled != user.enabled:
        user.enabled = body.enabled
        changed_fields.append("enabled")
        if not body.enabled:
            should_bump_epoch = True

    if should_bump_epoch:
        bump_session_epoch(user)

    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email が既に使用されています",
        ) from None

    await record_audit(
        session,
        actor_user_id=current_user.id,
        actor_label=current_user.username,
        action="user.update",
        target_type="user",
        target_id=str(user_id),
        detail={"changed_fields": changed_fields, "user_id": user_id},
        ip_address=get_client_ip(request),
    )
    await session.commit()
    await session.refresh(user)
    return user


@router.post("/{user_id}/reset-password", response_model=UserRead)
async def reset_password(
    user_id: int,
    body: ResetPasswordBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ユーザーが見つかりません")

    if user.origin != "local":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ローカルユーザーのみパスワードをリセットできます",
        )

    user.hashed_password = hash_password(body.new_password)
    bump_session_epoch(user)

    await record_audit(
        session,
        actor_user_id=current_user.id,
        actor_label=current_user.username,
        action="user.reset_password",
        target_type="user",
        target_id=str(user_id),
        detail={"user_id": user_id},
        ip_address=get_client_ip(request),
    )
    await session.commit()
    await session.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    if current_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="自分自身は削除できません",
        )

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ユーザーが見つかりません")

    # Last-admin guard
    if user.role == "admin" and user.enabled:
        admin_count = await _count_enabled_admins(session)
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="最後の管理者は削除できません",
            )

    await record_audit(
        session,
        actor_user_id=current_user.id,
        actor_label=current_user.username,
        action="user.delete",
        target_type="user",
        target_id=str(user_id),
        detail={"username": user.username, "role": user.role},
        ip_address=get_client_ip(request),
    )
    await session.delete(user)
    await session.commit()
