"""Task 5: 電話帳 / PBX 情報アダプタ (Directory) + guide リソース本文。

受入条件（プラン §11–§15 準拠）:
  - in-memory DB で各アダプタが §11–§15 のキー形を返す。
  - list_contacts: query 部分一致（name/phone/company）・空 query で全件。
  - add_contact: 追加後の contact エコー（6 キー）。
  - delete_contact: ok メッセージ。
  - list_extensions: id/number/display_name/enabled/type。
  - list_trunks: id/name/display_name/did_number/caller_id/outbound_prefixes/enabled。
    outbound_prefixes は常に [] （裁定#5）。
  - guide 本文が dial/say_and_listen/hangup と番号形式・184/186 を含む。
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from millicall.db import Base
from millicall.mcp_server.directory import Directory
from millicall.mcp_server.guide import OUTBOUND_CALLING_GUIDE
from millicall.models import Contact, Extension, Trunk


@pytest_asyncio.fixture
async def sessionmaker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def directory(sessionmaker):
    return Directory(sessionmaker)


# ---------------------------------------------------------------------------
# list_contacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_contacts_empty(directory):
    result = await directory.list_contacts()
    assert result == {"count": 0, "contacts": []}


@pytest.mark.asyncio
async def test_list_contacts_all(directory, sessionmaker):
    async with sessionmaker() as s:
        s.add(Contact(name="田中", phone_number="09011112222", company="ABC商事"))
        s.add(Contact(name="佐藤", phone_number="08033334444", company="XYZ株式会社"))
        await s.commit()
    result = await directory.list_contacts()
    assert result["count"] == 2
    # name 昇順（佐藤 < 田中 の Unicode 順）
    keys = set(result["contacts"][0].keys())
    assert keys == {"id", "name", "phone_number", "company", "department", "notes"}


@pytest.mark.asyncio
async def test_list_contacts_query_name(directory, sessionmaker):
    async with sessionmaker() as s:
        s.add(Contact(name="田中太郎", phone_number="09011112222", company="ABC"))
        s.add(Contact(name="佐藤花子", phone_number="08033334444", company="XYZ"))
        await s.commit()
    result = await directory.list_contacts("田中")
    assert result["count"] == 1
    assert result["contacts"][0]["name"] == "田中太郎"


@pytest.mark.asyncio
async def test_list_contacts_query_phone(directory, sessionmaker):
    async with sessionmaker() as s:
        s.add(Contact(name="田中", phone_number="09011112222", company="ABC"))
        s.add(Contact(name="佐藤", phone_number="08033334444", company="XYZ"))
        await s.commit()
    result = await directory.list_contacts("0801")
    assert result["count"] == 0
    result = await directory.list_contacts("0803")
    assert result["count"] == 1
    assert result["contacts"][0]["name"] == "佐藤"


@pytest.mark.asyncio
async def test_list_contacts_query_company(directory, sessionmaker):
    async with sessionmaker() as s:
        s.add(Contact(name="田中", phone_number="09011112222", company="ABC商事"))
        await s.commit()
    result = await directory.list_contacts("ABC")
    assert result["count"] == 1


# ---------------------------------------------------------------------------
# add_contact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_contact_echoes(directory):
    result = await directory.add_contact(
        name="山田", phone_number="09055556666", company="C社", department="営業", notes="メモ"
    )
    assert result["status"] == "ok"
    assert result["message"] == "連絡先「山田」を追加しました"
    contact = result["contact"]
    assert set(contact.keys()) == {
        "id",
        "name",
        "phone_number",
        "company",
        "department",
        "notes",
    }
    assert contact["name"] == "山田"
    assert contact["phone_number"] == "09055556666"
    assert contact["department"] == "営業"
    assert isinstance(contact["id"], int)


@pytest.mark.asyncio
async def test_add_contact_defaults(directory):
    result = await directory.add_contact(name="鈴木", phone_number="0311112222")
    assert result["status"] == "ok"
    assert result["contact"]["company"] == ""
    assert result["contact"]["department"] == ""
    assert result["contact"]["notes"] == ""


@pytest.mark.asyncio
async def test_add_contact_then_listed(directory):
    await directory.add_contact(name="高橋", phone_number="09099998888")
    result = await directory.list_contacts("高橋")
    assert result["count"] == 1


# ---------------------------------------------------------------------------
# delete_contact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_contact_ok(directory):
    added = await directory.add_contact(name="削除対象", phone_number="0300000000")
    cid = added["contact"]["id"]
    result = await directory.delete_contact(cid)
    assert result == {
        "status": "ok",
        "message": f"連絡先 (ID: {cid}) を削除しました",
    }
    assert (await directory.list_contacts())["count"] == 0


@pytest.mark.asyncio
async def test_delete_contact_missing_still_ok(directory):
    # 旧互換: 存在しない ID でも ok（冪等）。
    result = await directory.delete_contact(9999)
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# list_extensions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_extensions_empty(directory):
    result = await directory.list_extensions()
    assert result == {"count": 0, "extensions": []}


@pytest.mark.asyncio
async def test_list_extensions_shape(directory, sessionmaker):
    async with sessionmaker() as s:
        s.add(Extension(number="800", display_name="内線A", sip_password="x", enabled=True))
        s.add(Extension(number="801", display_name="内線B", sip_password="y", enabled=False))
        await s.commit()
    result = await directory.list_extensions()
    assert result["count"] == 2
    ext = result["extensions"][0]
    assert set(ext.keys()) == {"id", "number", "display_name", "enabled", "type"}
    # number 昇順
    assert result["extensions"][0]["number"] == "800"
    assert result["extensions"][0]["enabled"] is True
    assert result["extensions"][1]["enabled"] is False
    assert ext["type"] == "phone"


# ---------------------------------------------------------------------------
# list_trunks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_trunks_empty(directory):
    result = await directory.list_trunks()
    assert result == {"count": 0, "trunks": []}


@pytest.mark.asyncio
async def test_list_trunks_shape(directory, sessionmaker):
    async with sessionmaker() as s:
        s.add(
            Trunk(
                name="hgw1",
                display_name="HGW 1",
                host="192.168.0.1",
                username="u",
                password="p",
                did_number="0312345678",
                caller_id="0312345678",
                enabled=True,
            )
        )
        await s.commit()
    result = await directory.list_trunks()
    assert result["count"] == 1
    t = result["trunks"][0]
    assert set(t.keys()) == {
        "id",
        "name",
        "display_name",
        "did_number",
        "caller_id",
        "outbound_prefixes",
        "enabled",
    }
    assert t["name"] == "hgw1"
    assert t["did_number"] == "0312345678"
    # 裁定#5: 常に空配列
    assert t["outbound_prefixes"] == []
    # password は絶対に露出しない
    assert "password" not in t


# ---------------------------------------------------------------------------
# guide リソース本文
# ---------------------------------------------------------------------------


def test_guide_contains_flow_and_prefixes():
    g = OUTBOUND_CALLING_GUIDE
    assert "dial" in g
    assert "say_and_listen" in g
    assert "hangup" in g
    assert "184" in g
    assert "186" in g
    assert "内線" in g
