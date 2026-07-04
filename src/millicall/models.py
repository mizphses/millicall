from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy import true as sa_true
from sqlalchemy.orm import Mapped, mapped_column

from millicall.db import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="admin", server_default="admin"
    )
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default="local", server_default="local"
    )
    # Phase 6 (TOTP 2FA) 用に予約。Phase 1 では常に NULL。
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Extension(Base):
    __tablename__ = "extensions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 強ランダム自動生成のみ。ユーザー/API から指定不可。
    sip_password: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Trunk(Base):
    __tablename__ = "trunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # sofia gateway 名にも使う slug（英数と - _ のみ想定）
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    host: Mapped[str] = mapped_column(String(100), nullable=False)  # HGW の LAN 側 IP
    username: Mapped[str] = mapped_column(String(50), nullable=False)  # 認証ID = 内線番号
    # HGW 側で決まるユーザー入力値。平文保存(暗号化は Phase 6)。API レスポンスには出さない。
    password: Mapped[str] = mapped_column(String(128), nullable=False)
    did_number: Mapped[str] = mapped_column(
        String(30), nullable=False, default="", server_default=""
    )
    caller_id: Mapped[str] = mapped_column(  # 表示番号（自局番号）
        String(30), nullable=False, default="", server_default=""
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        """Custom repr that excludes the password field."""
        attrs = [
            f"{k}={v!r}" for k, v in self.__dict__.items()
            if not k.startswith("_") and k != "password"
        ]
        return f"<{self.__class__.__name__}({', '.join(attrs)})>"


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    # RouteTargetType の値。Phase 2 は "extension" のみ。将来 ring_group/workflow/ai_agent。
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_value: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
