import asyncio

import pytest

from millicall.media.call_control import EslCallControl
from millicall.telephony.esl import ESLConnectionClosed


class _FakeEsl:
    def __init__(self) -> None:
        self.cmds: list[str] = []

    async def bgapi(self, command: str) -> str:
        self.cmds.append(command)
        return "job-uuid"


@pytest.mark.asyncio
async def test_play_file_broadcasts_and_waits_for_done():
    esl = _FakeEsl()
    cc = EslCallControl(esl, "u1")

    async def _finish_soon():
        await asyncio.sleep(0.01)
        cc._notify_playback_done()

    asyncio.create_task(_finish_soon())
    await asyncio.wait_for(cc.play_file("/tmp/a.wav"), timeout=1.0)
    assert esl.cmds[0] == "uuid_broadcast u1 /tmp/a.wav aleg"


@pytest.mark.asyncio
async def test_stop_and_hangup():
    esl = _FakeEsl()
    cc = EslCallControl(esl, "u1")
    await cc.stop_playback()
    await cc.hangup()
    assert "uuid_break u1 all" in esl.cmds
    assert "uuid_kill u1" in esl.cmds


@pytest.mark.asyncio
async def test_play_file_clears_previous_done_flag():
    """連続再生: 前回の完了フラグが残っていても、新しい再生は待機する。"""
    esl = _FakeEsl()
    cc = EslCallControl(esl, "u1")
    cc._notify_playback_done()  # 前回の残留

    done = asyncio.Event()

    async def _play():
        await cc.play_file("/tmp/b.wav")
        done.set()

    task = asyncio.create_task(_play())
    await asyncio.sleep(0.02)
    # done フラグは clear されているので、まだ完了していないはず
    assert not done.is_set()
    cc._notify_playback_done()
    await asyncio.wait_for(task, timeout=1.0)
    assert esl.cmds == ["uuid_broadcast u1 /tmp/b.wav aleg"]


@pytest.mark.asyncio
async def test_commands_serialized_under_shared_lock():
    """共有 ESL 接続を守るため、注入された共有ロックで直列化される（I6）。"""

    class _SlowEsl:
        def __init__(self, lock_state: dict) -> None:
            self.lock_state = lock_state
            self.max_concurrent = 0
            self.active = 0

        async def bgapi(self, command: str) -> str:
            self.active += 1
            self.max_concurrent = max(self.max_concurrent, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return ""

    shared_lock = asyncio.Lock()
    esl = _SlowEsl({})
    cc1 = EslCallControl(esl, "u1", lock=shared_lock)
    cc2 = EslCallControl(esl, "u2", lock=shared_lock)
    await asyncio.gather(cc1.stop_playback(), cc2.stop_playback())
    assert esl.max_concurrent == 1


@pytest.mark.asyncio
async def test_reconnects_on_closed_connection():
    """共有接続が切れていたら、注入された reconnect で張り直して再送する（I6）。"""

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
    await cc.hangup()
    assert fresh.cmds == ["uuid_kill u1"]
    assert cc._esl is fresh


@pytest.mark.asyncio
async def test_no_reconnect_reraises_on_closed():
    """reconnect 未注入なら ESLConnectionClosed をそのまま伝播する。"""

    class _DeadEsl:
        async def bgapi(self, command: str) -> str:
            raise ESLConnectionClosed("dead")

    cc = EslCallControl(_DeadEsl(), "u1")
    with pytest.raises(ESLConnectionClosed):
        await cc.hangup()


@pytest.mark.asyncio
async def test_play_file_timeout_returns_and_logs_warning(caplog):
    """PLAYBACK_STOP が来ない場合、タイムアウト後に例外を出さず return し警告ログが出る。"""
    import logging

    esl = _FakeEsl()
    # タイムアウトを 0.3 秒に設定（フルスイート負荷下でも安定して発火する値）
    cc = EslCallControl(esl, "uuid-timeout-test", playback_timeout=0.3)

    # alembic fileConfig が disable_existing_loggers=True でロガーを無効化することがあるため
    # 明示的に有効化してから caplog で捕捉する
    target_logger = logging.getLogger("millicall.media.call_control")
    target_logger.disabled = False

    with caplog.at_level(logging.WARNING, logger="millicall.media.call_control"):
        # 例外が出ないことを確認（正常 return）
        await cc.play_file("/tmp/missing.wav")

    # 警告ログに uuid と path が含まれていることを確認
    assert any(
        "uuid-timeout-test" in r.message and "/tmp/missing.wav" in r.message
        for r in caplog.records
        if r.levelno == logging.WARNING
    ), f"期待した警告ログが見つからない。records={[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_play_file_timeout_does_not_raise():
    """タイムアウト後も例外が外に出ないことを追加で確認する（回帰防止）。"""
    esl = _FakeEsl()
    cc = EslCallControl(esl, "u-timeout", playback_timeout=0.05)
    # 例外が出ないことだけを確認する（AssertionError も TimeoutError も不可）
    result = await cc.play_file("/tmp/x.wav")
    assert result is None  # play_file は None を返す


@pytest.mark.asyncio
async def test_play_file_normal_returns_immediately_on_playback_stop():
    """正常系: _notify_playback_done() が呼ばれれば即 return し、タイムアウトは発火しない。"""
    import logging

    esl = _FakeEsl()
    # タイムアウトを短く設定してもイベントが来れば問題ないことを確認
    cc = EslCallControl(esl, "u-normal", playback_timeout=1.0)

    async def _fire_done():
        await asyncio.sleep(0.01)
        cc._notify_playback_done()

    with caplog_ctx() as caplog:
        asyncio.create_task(_fire_done())
        await cc.play_file("/tmp/normal.wav")

    # 警告ログが出ていないことを確認
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert not warning_msgs, f"正常系で予期しない警告が出た: {warning_msgs}"


import contextlib
import io


@contextlib.contextmanager
def caplog_ctx():
    """pytest caplog の代替: logging.handlers.MemoryHandler でレコードを収集する。"""
    import logging

    class _Collector(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    handler = _Collector()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
