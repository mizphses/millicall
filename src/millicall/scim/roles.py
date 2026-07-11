"""SCIM グループ → ロール自動付与（ロール再計算）。

app_settings の ``scim_group_role_map``（グループ displayName → ロール）に基づき、
origin="scim" ユーザーのロールを再計算する。

再計算ルール:
  - ユーザーが属する「マップに載っているグループ」のロールのうち最上位
    （admin > user）を採用する。
  - どのマップ済みグループにも属さなくなったユーザーは "user" に戻す。
  - origin != "scim" のユーザーは絶対に変更しない（SELECT 対象にすら含めない）。
  - マップが空 {} の場合は機能オフとして何もしない（既存ロールを保持する）。
  - ロール変更は監査ログ scim.user.role_change に old/new 付きで記録する。

呼び出しタイミング:
  - SCIM Groups の作成 / 変更 / 削除（影響メンバーのみ）
  - scim_group_role_map 設定の変更（全 origin="scim" ユーザー）
"""

from collections import defaultdict
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.audit import record_audit
from millicall.models import ScimGroup, ScimGroupMember, User

# 存在するロール。scim_group_role_map の値はこの集合のみ許可（app_settings 側で検証）。
ROLE_ADMIN = "admin"
ROLE_USER = "user"


async def recalc_scim_roles(
    session: AsyncSession,
    role_map: dict[str, str],
    *,
    user_ids: Collection[int] | None = None,
    actor_user_id: int | None = None,
    actor_label: str = "scim",
    ip: str | None = None,
) -> list[dict]:
    """origin="scim" ユーザーのロールを再計算する（commit は呼び出し元の責務）。

    Args:
        session: 呼び出し元が管理する AsyncSession（監査ログと同一トランザクション）。
        role_map: 実効設定の scim_group_role_map。空 dict なら何もしない。
        user_ids: 再計算対象を限定するユーザー id 集合。None なら全 scim ユーザー。
        actor_user_id / actor_label / ip: 監査ログ用の操作主体情報。

    Returns:
        変更されたユーザーのリスト（{"user_id", "old", "new"}）。
    """
    if not role_map:
        # マップ未設定 = 機能オフ。誤って全 scim ユーザーを "user" に戻さない。
        return []
    if user_ids is not None and not user_ids:
        return []

    stmt = select(User).where(User.origin == "scim")
    if user_ids is not None:
        stmt = stmt.where(User.id.in_(list(user_ids)))
    users = (await session.scalars(stmt)).all()
    if not users:
        return []

    # 対象ユーザーの所属グループ displayName を一括取得する。
    membership_rows = await session.execute(
        select(ScimGroupMember.user_id, ScimGroup.display_name)
        .join(ScimGroup, ScimGroup.id == ScimGroupMember.group_id)
        .where(ScimGroupMember.user_id.in_([u.id for u in users]))
    )
    group_names_by_user: dict[int, set[str]] = defaultdict(set)
    for uid, display_name in membership_rows:
        group_names_by_user[uid].add(display_name)

    changes: list[dict] = []
    for user in users:
        mapped_roles = {
            role_map[name] for name in group_names_by_user.get(user.id, set()) if name in role_map
        }
        # 最上位ロールを採用（admin > user）。マップ済みグループに属さない場合は "user"。
        desired = ROLE_ADMIN if ROLE_ADMIN in mapped_roles else ROLE_USER
        if user.role == desired:
            continue
        old = user.role
        user.role = desired
        await record_audit(
            session,
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            action="scim.user.role_change",
            target_type="user",
            target_id=str(user.id),
            detail={"username": user.username, "old": old, "new": desired},
            ip_address=ip,
        )
        changes.append({"user_id": user.id, "old": old, "new": desired})
    return changes
