"""電話帳 / PBX 情報アダプタ（MCP ツールの下回り、Task 5）。

既存の contacts / extensions / trunks の DB モデルへ直接クエリする薄いアダプタ。
router.py の CRUD ロジック（`select(...).order_by(...)` / `session.get` / add-commit /
delete-commit）と等価なクエリを sessionmaker 経由で実行し、返り値を契約 §11–§15 の
JSON 形（dict）へ整形する。`@mcp.tool()` 登録・json.dumps 文字列化は Task 6（tools.py）。

裁定準拠:
  - #5: list_trunks.outbound_prefixes は常に `[]`（v2 Trunk に該当カラム無し・互換キー維持）。
  - trunk password は返り値に含めない（TrunkRead 同様、秘密衛生）。

契約補足:
  - list_extensions.type: v2 Extension に type カラムは無い。旧実装のデフォルト "phone"
    （旧 domain model の `type: str = "phone"`）を互換のため定数で埋める。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from millicall.models import Contact, Extension, Trunk

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# 旧実装の Extension.type デフォルト（"phone" or "ai_agent"）。v2 は SIP ユーザー内線のみ。
_EXTENSION_TYPE = "phone"


def _contact_to_dict(c: Contact) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "phone_number": c.phone_number,
        "company": c.company,
        "department": c.department,
        "notes": c.notes,
    }


class Directory:
    """contacts / extensions / trunks を MCP ツール用に束ねるアダプタ。"""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    # -- contacts -----------------------------------------------------------
    async def list_contacts(self, query: str = "") -> dict:
        """§11。query が空なら name 昇順で全件。非空なら name/phone/company の部分一致。"""
        async with self._sessionmaker() as session:
            stmt = select(Contact)
            q = query.strip()
            if q:
                pattern = f"%{q}%"
                stmt = stmt.where(
                    or_(
                        Contact.name.ilike(pattern),
                        Contact.phone_number.like(pattern),
                        Contact.company.ilike(pattern),
                    )
                )
            stmt = stmt.order_by(Contact.name)
            rows = await session.scalars(stmt)
            contacts = [_contact_to_dict(c) for c in rows]
        return {"count": len(contacts), "contacts": contacts}

    async def add_contact(
        self,
        name: str,
        phone_number: str,
        company: str = "",
        department: str = "",
        notes: str = "",
    ) -> dict:
        """§12。追加後の contact を 6 キーでエコー。"""
        async with self._sessionmaker() as session:
            contact = Contact(
                name=name,
                phone_number=phone_number,
                company=company,
                department=department,
                notes=notes,
            )
            session.add(contact)
            await session.commit()
            await session.refresh(contact)
            echo = _contact_to_dict(contact)
        return {
            "status": "ok",
            "message": f"連絡先「{name}」を追加しました",
            "contact": echo,
        }

    async def delete_contact(self, contact_id: int) -> dict:
        """§13。存在しない ID でも ok（旧互換・冪等）。"""
        async with self._sessionmaker() as session:
            contact = await session.get(Contact, contact_id)
            if contact is not None:
                await session.delete(contact)
                await session.commit()
        return {
            "status": "ok",
            "message": f"連絡先 (ID: {contact_id}) を削除しました",
        }

    # -- extensions ---------------------------------------------------------
    async def list_extensions(self) -> dict:
        """§14。number 昇順。type は互換のため定数 "phone"。sip_password は返さない。"""
        async with self._sessionmaker() as session:
            rows = await session.scalars(select(Extension).order_by(Extension.number))
            extensions = [
                {
                    "id": e.id,
                    "number": e.number,
                    "display_name": e.display_name,
                    "enabled": e.enabled,
                    "type": _EXTENSION_TYPE,
                }
                for e in rows
            ]
        return {"count": len(extensions), "extensions": extensions}

    # -- trunks -------------------------------------------------------------
    async def list_trunks(self) -> dict:
        """§15。name 昇順。outbound_prefixes は常に []（裁定#5）。password は返さない。"""
        async with self._sessionmaker() as session:
            rows = await session.scalars(select(Trunk).order_by(Trunk.name))
            trunks = [
                {
                    "id": t.id,
                    "name": t.name,
                    "display_name": t.display_name,
                    "did_number": t.did_number,
                    "caller_id": t.caller_id,
                    "outbound_prefixes": [],
                    "enabled": t.enabled,
                }
                for t in rows
            ]
        return {"count": len(trunks), "trunks": trunks}
