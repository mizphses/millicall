from sqlalchemy import func, select
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
    await session.commit()
    return password
