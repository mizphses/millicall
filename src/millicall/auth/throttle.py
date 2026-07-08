"""ログイン試行レート制限・ロックアウト サービス。

設計:
  - login_attempts テーブルに失敗行を挿入し、ウィンドウ内カウントで判定する。
  - IP とユーザー名の 2 軸でチェックし、どちらが上限を超えても 429 を返す。
  - ロックアウト発生時は audit に "login.lockout" を記録する。
  - ログイン成功時は clear_failures を呼んで、該当ユーザー名の失敗行を削除する。
    （IP 失敗は残す。IP は複数ユーザーが共有する可能性があるため）
"""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.audit import record_audit
from millicall.models import LoginAttempt


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _count_recent(
    session: AsyncSession,
    *,
    key: str,
    window_seconds: int,
) -> int:
    """ウィンドウ内の失敗数を返す。"""
    since = _now_utc() - timedelta(seconds=window_seconds)
    result = await session.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.key == key,
            LoginAttempt.created_at >= since,
        )
    )
    return result or 0


async def _prune_expired(session: AsyncSession, *, window_seconds: int) -> None:
    """ウィンドウより古い失敗行を削除してテーブルの無制限成長を防ぐ（レビュー N-3）。"""
    cutoff = _now_utc() - timedelta(seconds=window_seconds)
    await session.execute(delete(LoginAttempt).where(LoginAttempt.created_at < cutoff))


async def check_and_raise(
    session: AsyncSession,
    *,
    ip: str | None,
    username: str,
    ip_max_attempts: int,
    username_max_attempts: int,
    lockout_seconds: int,
    actor_user_id: int | None = None,
) -> None:
    """ロックアウト状態なら HTTPException(429) を送出する。

    IP とユーザー名で **別々のしきい値** を用いる（レビュー H-1）:
      - IP しきい値 (低め) が一次防御。単一 IP からの総当たりを止める。
      - ユーザー名しきい値 (高め) が二次防御。分散総当たりに備える。
    ユーザー名しきい値を IP しきい値より高くすることで、単一 IP の攻撃者は自分の IP が
    先にロックされ、正規ユーザーのアカウントを容易にロックアウト（DoS）できない。

    commit は呼び出し元の責務（audit record の flush のため）。
    """
    retry_after = str(lockout_seconds)
    await _prune_expired(session, window_seconds=lockout_seconds)

    if ip:
        ip_count = await _count_recent(session, key=ip, window_seconds=lockout_seconds)
        if ip_count >= ip_max_attempts:
            await record_audit(
                session,
                actor_user_id=actor_user_id,
                actor_label=username,
                action="login.lockout",
                detail={"reason": "ip", "key": ip, "count": ip_count},
                ip_address=ip,
            )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts",
                headers={"Retry-After": retry_after},
            )

    username_count = await _count_recent(session, key=username, window_seconds=lockout_seconds)
    if username_count >= username_max_attempts:
        await record_audit(
            session,
            actor_user_id=actor_user_id,
            actor_label=username,
            action="login.lockout",
            detail={"reason": "username", "key": username, "count": username_count},
            ip_address=ip,
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts",
            headers={"Retry-After": retry_after},
        )


async def record_failure(
    session: AsyncSession,
    *,
    ip: str | None,
    username: str,
    action: str = "login",
) -> None:
    """失敗行を挿入する。

    IP キーとユーザー名キーの 2 行を挿入して、両軸のカウントに反映させる。
    commit は呼び出し元の責務。
    """
    now = _now_utc()
    if ip:
        session.add(
            LoginAttempt(
                key=ip,
                key_type="ip",
                ip_address=ip,
                username=username,
                action=action,
                created_at=now,
            )
        )
    session.add(
        LoginAttempt(
            key=username,
            key_type="username",
            ip_address=ip,
            username=username,
            action=action,
            created_at=now,
        )
    )


async def clear_failures(
    session: AsyncSession,
    *,
    username: str,
) -> None:
    """ユーザー名に紐づく失敗行を削除してカウンタをリセットする。

    - ユーザー名キー（key_type="username", key=username）の全行を削除する。
    - IP キーの行のうち username フィールドが一致するもの（= このユーザーの失敗）も削除する。
      これにより、ログイン成功後に同一 IP の次の試行がすぐにロックアウトされない。

    commit は呼び出し元の責務。
    """
    # ユーザー名キーの行を削除
    await session.execute(
        delete(LoginAttempt).where(
            LoginAttempt.key == username, LoginAttempt.key_type == "username"
        )
    )
    # IP キーのうち、このユーザーの失敗として記録された行も削除
    await session.execute(
        delete(LoginAttempt).where(LoginAttempt.key_type == "ip", LoginAttempt.username == username)
    )
