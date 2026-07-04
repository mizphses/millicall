from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.auth.security import hash_password
from millicall.gen import generate_password
from millicall.models import User


async def ensure_admin_user(session: AsyncSession) -> str | None:
    count = await session.scalar(select(func.count()).select_from(User))
    if count and count > 0:
        return None
    password = generate_password()
    session.add(
        User(
            username="admin",
            hashed_password=hash_password(password),
            display_name="Administrator",
            role="admin",
            origin="local",
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        # 同時起動によるレース: 別プロセスが先にINSERTを完了した場合は無視する
        await session.rollback()
        return None
    return password
