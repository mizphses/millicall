"""internal プロファイルの再バインド（sofia profile internal restart）結合テスト。

reloadxml では sofia プロファイルの sip-ip / bind は再バインドされない（ディレクトリの
ユーザー再読込には有効だが、プロファイルのバインドIP変更には profile restart が必要）。
そのため internal の実効バインド（sip-ip / domain）が前回生成時から変化した場合、
および初回 notify（core 起動相当。稼働中 FreeSWITCH が古い設定の可能性がある）では
`sofia profile internal restart` を reloadxml の後に送る。

一方、internal バインドが不変の増分変更（内線追加など）では restart を送らない
（登録中の電話を無駄に切断しないため。ディレクトリのユーザー追加は reloadxml で反映）。
"""

from contextlib import asynccontextmanager

from millicall.models import NetworkConfig
from millicall.telephony.service import TelephonyChangeListener

# ---------------------------------------------------------------------------
# Fake ESL client: wire コマンドを記録する（実TCP不要）
# ---------------------------------------------------------------------------


class _FakeESL:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    async def connect(self) -> None:
        return None

    async def reloadxml(self) -> None:
        self._sink.append("reloadxml")

    async def api(self, cmd: str) -> str:
        self._sink.append(cmd)
        return "+OK"

    async def close(self) -> None:
        return None


@asynccontextmanager
async def _listener_with_sink(app):
    """app の既存 writer を使い、fake ESL でコマンドを記録する listener を作る。"""
    sink: list[str] = []
    writer = app.state.change_listener._writer
    listener = TelephonyChangeListener(writer, lambda: _FakeESL(sink), esl_timeout=2.0)
    yield listener, sink


async def _set_network_config(app, *, lan_ip: str, applied: bool) -> None:
    sm = app.state.sessionmaker
    async with sm() as session:
        cfg = await session.get(NetworkConfig, 1)
        if cfg is None:
            cfg = NetworkConfig(id=1)
            session.add(cfg)
        cfg.lan_ip = lan_ip
        cfg.applied = applied
        await session.commit()


def _internal_restarts(sink: list[str]) -> list[str]:
    return [c for c in sink if "sofia profile internal restart" in c]


async def test_first_notify_sends_internal_restart(app) -> None:
    """初回 notify（core 起動相当）では internal restart を必ず送る。"""
    async with _listener_with_sink(app) as (listener, sink):
        sm = app.state.sessionmaker
        async with sm() as session:
            await listener.notify(session)

    assert "reloadxml" in sink, f"reloadxml が送られていない: {sink}"
    assert _internal_restarts(sink), f"初回 notify で internal restart が送られていない: {sink}"
    # reloadxml → internal restart の順序
    assert sink.index("reloadxml") < sink.index("sofia profile internal restart")


async def test_second_notify_without_change_skips_internal_restart(app) -> None:
    """internal バインド不変の 2 回目の増分 notify では internal restart を送らない。"""
    async with _listener_with_sink(app) as (listener, sink):
        sm = app.state.sessionmaker
        async with sm() as session:
            await listener.notify(session)
        assert _internal_restarts(sink), "初回で restart が送られる前提"
        sink.clear()
        async with sm() as session:
            await listener.notify(session)

    assert "reloadxml" in sink, f"2回目でも reloadxml は送る: {sink}"
    assert not _internal_restarts(sink), (
        f"バインド不変の 2 回目で internal restart を送ってはいけない: {sink}"
    )


async def test_notify_sends_internal_restart_when_bind_changes(app) -> None:
    """internal バインドが変化（NetworkConfig applied で lan_ip 追加）した notify で restart。"""
    async with _listener_with_sink(app) as (listener, sink):
        sm = app.state.sessionmaker
        async with sm() as session:
            await listener.notify(session)  # 初回
        sink.clear()
        # 子LAN 適用でバインドIP／ドメインが変化する
        await _set_network_config(app, lan_ip="172.20.0.1", applied=True)
        async with sm() as session:
            await listener.notify(session)

    assert _internal_restarts(sink), f"バインド変化時に internal restart が送られていない: {sink}"


async def test_reloadxml_still_sent_on_every_notify(app) -> None:
    """reloadxml は毎回送られる（既存挙動の維持）。"""
    async with _listener_with_sink(app) as (listener, sink):
        sm = app.state.sessionmaker
        async with sm() as session:
            await listener.notify(session)
        sink.clear()
        async with sm() as session:
            await listener.notify(session)

    assert sink.count("reloadxml") == 1, f"2回目でも reloadxml を 1 回送る: {sink}"


async def test_internal_restart_independent_of_sync_gateway(app) -> None:
    """sync_gateway 指定の増分 notify でも、初回であれば internal restart を送る。"""
    async with _listener_with_sink(app) as (listener, sink):
        sm = app.state.sessionmaker
        async with sm() as session:
            await listener.notify(session, sync_gateway="hgw")

    assert _internal_restarts(sink), f"sync_gateway 指定でも初回は internal restart を送る: {sink}"


async def test_internal_restart_survives_esl_failure(app, monkeypatch) -> None:
    """ESL 接続失敗でも例外を投げない（best-effort。core 起動/API を落とさない）。"""

    class _FailingESL:
        async def connect(self) -> None:
            raise OSError("boom")

        async def reloadxml(self) -> None:  # pragma: no cover - 到達しない
            raise AssertionError

        async def api(self, cmd: str) -> str:  # pragma: no cover - 到達しない
            raise AssertionError

        async def close(self) -> None:
            return None

    writer = app.state.change_listener._writer
    listener = TelephonyChangeListener(writer, lambda: _FailingESL(), esl_timeout=2.0)
    sm = app.state.sessionmaker
    async with sm() as session:
        await listener.notify(session)  # 例外が伝播しなければ OK
