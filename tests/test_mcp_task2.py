"""Task 2: ESL 通話プリミティブ (send_dtmf / transfer) + LiveCallView のテスト。

受入条件:
  - fake ESL で uuid_send_dtmf / uuid_transfer / uuid_kill の発行コマンド文字列を検証。
  - digits バリデーション (無効文字 → ValueError、空文字 → ValueError)。
  - SessionRegistry + CDR からの §9/§10 キー形整形テスト。
  - 存在しない uuid → get_status が None を返す。
  - list_active が SessionRegistry の全 UUID を返す。
"""

import asyncio
from datetime import datetime

import pytest
import pytest_asyncio

from millicall.mcp_server.live_calls import LiveCallView
from millicall.media.call_control import EslCallControl
from millicall.media.service import SessionRegistry
from millicall.models import Cdr
from millicall.telephony.esl import ESLConnectionClosed

# ---------------------------------------------------------------------------
# Fake ESL
# ---------------------------------------------------------------------------


class _FakeEsl:
    def __init__(self) -> None:
        self.cmds: list[str] = []

    async def bgapi(self, command: str) -> str:
        self.cmds.append(command)
        return "job-uuid"


# ===========================================================================
# EslCallControl.send_dtmf
# ===========================================================================


@pytest.mark.asyncio
async def test_send_dtmf_issues_correct_command():
    """有効な digits → uuid_send_dtmf <uuid> <digits> が bgapi に渡される。"""
    esl = _FakeEsl()
    cc = EslCallControl(esl, "test-uuid-1")
    await cc.send_dtmf("1234")
    assert esl.cmds[-1] == "uuid_send_dtmf test-uuid-1 1234"


@pytest.mark.asyncio
async def test_send_dtmf_star_hash():
    """* と # も有効 DTMF 桁として受け入れる。"""
    esl = _FakeEsl()
    cc = EslCallControl(esl, "u1")
    await cc.send_dtmf("*99#")
    assert esl.cmds[-1] == "uuid_send_dtmf u1 *99#"


@pytest.mark.asyncio
async def test_send_dtmf_abcd_and_w_valid():
    """ABCD と w（ポーズ）はすべて有効桁として受け入れる。"""
    esl = _FakeEsl()
    cc = EslCallControl(esl, "u1")
    await cc.send_dtmf("ABCDw")
    assert esl.cmds[-1] == "uuid_send_dtmf u1 ABCDw"


@pytest.mark.asyncio
async def test_send_dtmf_mixed_valid():
    """数字・記号・ABCDw が混在しても有効。"""
    esl = _FakeEsl()
    cc = EslCallControl(esl, "u1")
    await cc.send_dtmf("0*1234w#AB")
    assert esl.cmds[-1] == "uuid_send_dtmf u1 0*1234w#AB"


@pytest.mark.asyncio
async def test_send_dtmf_invalid_char_raises():
    """無効文字 (X) を含む場合 ValueError を送出する。"""
    esl = _FakeEsl()
    cc = EslCallControl(esl, "u1")
    with pytest.raises(ValueError):
        await cc.send_dtmf("123X")


@pytest.mark.asyncio
async def test_send_dtmf_lowercase_invalid():
    """小文字 abcd は無効（規格は大文字のみ）。"""
    esl = _FakeEsl()
    cc = EslCallControl(esl, "u1")
    with pytest.raises(ValueError):
        await cc.send_dtmf("1a2b")


@pytest.mark.asyncio
async def test_send_dtmf_empty_raises():
    """空文字列は ValueError を送出する。"""
    esl = _FakeEsl()
    cc = EslCallControl(esl, "u1")
    with pytest.raises(ValueError):
        await cc.send_dtmf("")


@pytest.mark.asyncio
async def test_send_dtmf_uses_shared_lock():
    """send_dtmf も共有ロック (I6) を尊重する（並行呼び出しが直列化される）。"""

    class _SlowEsl:
        def __init__(self) -> None:
            self.active = 0
            self.max_concurrent = 0
            self.cmds: list[str] = []

        async def bgapi(self, command: str) -> str:
            self.active += 1
            self.max_concurrent = max(self.max_concurrent, self.active)
            await asyncio.sleep(0.01)
            self.cmds.append(command)
            self.active -= 1
            return ""

    shared_lock = asyncio.Lock()
    esl = _SlowEsl()
    cc1 = EslCallControl(esl, "u1", lock=shared_lock)
    cc2 = EslCallControl(esl, "u2", lock=shared_lock)
    await asyncio.gather(cc1.send_dtmf("1"), cc2.send_dtmf("2"))
    assert esl.max_concurrent == 1


@pytest.mark.asyncio
async def test_send_dtmf_reconnects_on_closed_connection():
    """接続断時は注入された reconnect で張り直して再送する (I6)。"""

    class _DeadThenAliveEsl:
        def __init__(self, alive: bool) -> None:
            self.alive = alive
            self.cmds: list[str] = []

        async def bgapi(self, command: str) -> str:
            if not self.alive:
                raise ESLConnectionClosed("dead")
            self.cmds.append(command)
            return ""

    fresh = _DeadThenAliveEsl(alive=True)

    async def _reconnect():
        return fresh

    dead = _DeadThenAliveEsl(alive=False)
    cc = EslCallControl(dead, "u1", reconnect=_reconnect)
    await cc.send_dtmf("9")
    assert fresh.cmds == ["uuid_send_dtmf u1 9"]
    assert cc._esl is fresh


# ===========================================================================
# EslCallControl.transfer
# ===========================================================================


@pytest.mark.asyncio
async def test_transfer_issues_correct_command():
    """uuid_transfer <uuid> <dest> XML default コマンドが bgapi に渡される。"""
    esl = _FakeEsl()
    cc = EslCallControl(esl, "test-uuid-2")
    await cc.transfer("800")
    assert esl.cmds[-1] == "uuid_transfer test-uuid-2 800 XML default"


@pytest.mark.asyncio
async def test_transfer_long_destination():
    """外線番号を dest に指定した場合も正しいコマンドになる。"""
    esl = _FakeEsl()
    cc = EslCallControl(esl, "u1")
    await cc.transfer("0312345678")
    assert esl.cmds[-1] == "uuid_transfer u1 0312345678 XML default"


@pytest.mark.asyncio
async def test_transfer_uses_shared_lock():
    """transfer も共有ロック (I6) を尊重する。"""

    class _SlowEsl:
        def __init__(self) -> None:
            self.active = 0
            self.max_concurrent = 0

        async def bgapi(self, command: str) -> str:
            self.active += 1
            self.max_concurrent = max(self.max_concurrent, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return ""

    shared_lock = asyncio.Lock()
    esl = _SlowEsl()
    cc1 = EslCallControl(esl, "u1", lock=shared_lock)
    cc2 = EslCallControl(esl, "u2", lock=shared_lock)
    await asyncio.gather(cc1.transfer("800"), cc2.transfer("801"))
    assert esl.max_concurrent == 1


@pytest.mark.asyncio
async def test_transfer_reconnects_on_closed_connection():
    """接続断時は reconnect で張り直して再送する (I6)。"""

    class _DeadThenAliveEsl:
        def __init__(self, alive: bool) -> None:
            self.alive = alive
            self.cmds: list[str] = []

        async def bgapi(self, command: str) -> str:
            if not self.alive:
                raise ESLConnectionClosed("dead")
            self.cmds.append(command)
            return ""

    fresh = _DeadThenAliveEsl(alive=True)

    async def _reconnect():
        return fresh

    dead = _DeadThenAliveEsl(alive=False)
    cc = EslCallControl(dead, "u1", reconnect=_reconnect)
    await cc.transfer("900")
    assert fresh.cmds == ["uuid_transfer u1 900 XML default"]
    assert cc._esl is fresh


# ===========================================================================
# SessionRegistry.all_uuids
# ===========================================================================


def test_session_registry_all_uuids_empty():
    """登録なしのとき all_uuids は空リストを返す。"""
    reg = SessionRegistry()
    assert reg.all_uuids() == []


def test_session_registry_all_uuids_after_register():
    """register 後に all_uuids でそのキーが見える。"""
    reg = SessionRegistry()
    reg.register("uuid-1", object(), object())
    reg.register("uuid-2", object(), object())
    uuids = reg.all_uuids()
    assert "uuid-1" in uuids
    assert "uuid-2" in uuids
    assert len(uuids) == 2


def test_session_registry_all_uuids_after_pop():
    """pop 後は all_uuids から消える。"""
    reg = SessionRegistry()
    reg.register("uuid-1", object(), object())
    reg.pop("uuid-1")
    assert reg.all_uuids() == []


# ===========================================================================
# LiveCallView — get_status / list_active
# (DB 依存テストは app fixture を再利用)
# ===========================================================================


@pytest_asyncio.fixture
async def live_view(app):
    """LiveCallView を空の SessionRegistry + test DB の sessionmaker で組み立てる。"""
    registry = SessionRegistry()
    return LiveCallView(registry, app.state.sessionmaker), registry, app.state.sessionmaker


@pytest.mark.asyncio
async def test_get_status_unknown_uuid_returns_none(live_view):
    """SessionRegistry にない uuid → get_status は None を返す。"""
    view, _reg, _sm = live_view
    result = await view.get_status("nonexistent-uuid")
    assert result is None


@pytest.mark.asyncio
async def test_get_status_active_session_no_cdr_returns_nulls(live_view):
    """進行中通話（CDR なし）は state=Up、CDR 由来フィールドは None。"""
    view, reg, _sm = live_view
    reg.register("active-uuid", object(), object())

    result = await view.get_status("active-uuid")
    assert result is not None
    assert result["channel_id"] == "active-uuid"
    assert result["state"] == "Up"
    # CDR がないので None
    assert result["caller_name"] is None
    assert result["caller_number"] is None
    assert result["connected_name"] is None
    assert result["connected_number"] is None
    assert result["created_at"] is None


@pytest.mark.asyncio
async def test_get_status_active_session_with_cdr(live_view):
    """CDR レコードがある場合は §9 キーに整形される。"""
    view, reg, sm = live_view
    reg.register("cdr-uuid", object(), object())

    started = datetime(2026, 7, 6, 10, 0, 0)
    async with sm() as db:
        db.add(
            Cdr(
                call_uuid="cdr-uuid",
                direction="outbound",
                src_number="1001",
                dst_number="0312345678",
                caller_id_name="Alice",
                started_at=started,
                duration_seconds=0,
                billsec_seconds=0,
                hangup_cause="",
            )
        )
        await db.commit()

    result = await view.get_status("cdr-uuid")
    assert result is not None
    assert result["channel_id"] == "cdr-uuid"
    assert result["state"] == "Up"
    assert result["caller_name"] == "Alice"
    assert result["caller_number"] == "1001"
    assert result["connected_name"] is None  # show channels 不使用
    assert result["connected_number"] == "0312345678"
    assert result["created_at"] == started.isoformat()


@pytest.mark.asyncio
async def test_list_active_empty_registry(live_view):
    """SessionRegistry が空のとき list_active は空リストを返す。"""
    view, _reg, _sm = live_view
    result = await view.list_active()
    assert result == []


@pytest.mark.asyncio
async def test_list_active_returns_all_uuids(live_view):
    """SessionRegistry の全 UUID が §10 calls 要素形で返される。"""
    view, reg, _sm = live_view
    reg.register("ua", object(), object())
    reg.register("ub", object(), object())

    result = await view.list_active()
    assert len(result) == 2
    channel_ids = {r["channel_id"] for r in result}
    assert channel_ids == {"ua", "ub"}
    # CDR なし → 各フィールドは None
    for r in result:
        assert r["state"] == "Up"
        assert r["caller_number"] is None
        assert r["connected_number"] is None
        assert r["created_at"] is None


@pytest.mark.asyncio
async def test_list_active_entry_has_correct_keys(live_view):
    """list_active の各要素が §10 必須キー (channel_id, state, caller_number,
    connected_number, created_at) を持つ。"""
    view, reg, _sm = live_view
    reg.register("u1", object(), object())

    result = await view.list_active()
    assert len(result) == 1
    entry = result[0]
    for key in ("channel_id", "state", "caller_number", "connected_number", "created_at"):
        assert key in entry, f"key {key!r} missing from list_active entry"


@pytest.mark.asyncio
async def test_list_active_with_cdr_data(live_view):
    """CDR があるセッションは list_active でも CDR 由来の値を返す。"""
    view, reg, sm = live_view
    reg.register("cdr-active", object(), object())

    started = datetime(2026, 7, 6, 12, 0, 0)
    async with sm() as db:
        db.add(
            Cdr(
                call_uuid="cdr-active",
                direction="inbound",
                src_number="0398765432",
                dst_number="800",
                caller_id_name="Bob",
                started_at=started,
                duration_seconds=0,
                billsec_seconds=0,
                hangup_cause="",
            )
        )
        await db.commit()

    result = await view.list_active()
    entry = next(r for r in result if r["channel_id"] == "cdr-active")
    assert entry["caller_number"] == "0398765432"
    assert entry["connected_number"] == "800"
    assert entry["created_at"] == started.isoformat()


@pytest.mark.asyncio
async def test_get_status_after_pop_returns_none(live_view):
    """pop 後（通話終了後）は get_status が None を返す。"""
    view, reg, _sm = live_view
    reg.register("popped-uuid", object(), object())
    reg.pop("popped-uuid")
    result = await view.get_status("popped-uuid")
    assert result is None
