"""DtmfCollector / BoundDtmf の単体テスト (Phase 4b Task 6).

テスト方針:
  - 実際の asyncio イベントループで実行（pytest-asyncio）
  - 「先に feed → 後で collect」と「先に collect → 後で feed」の両方を検証する
  - タイムアウト・終端キー・最大桁数の各終了条件を検証する
  - unregister 後のキュークリーンアップを確認する
"""

from __future__ import annotations

import asyncio

import pytest

from millicall.media.dtmf import BoundDtmf, DtmfCollector

# --------------------------------------------------------------------------- #
# ヘルパ
# --------------------------------------------------------------------------- #


def make_collector_with_digits(uuid: str, digits: str) -> tuple[DtmfCollector, BoundDtmf]:
    """コレクタを作成して桁を事前にキューに入れた BoundDtmf を返す。"""
    collector = DtmfCollector()
    collector.register(uuid)
    for d in digits:
        collector.feed(uuid, d)
    return collector, collector.bind(uuid)


# --------------------------------------------------------------------------- #
# feed → collect (キューへの事前積み込み)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_feed_then_collect_returns_digits() -> None:
    """feed した桁を collect で取り出せる。"""
    collector, bound = make_collector_with_digits("u1", "123")
    result = await bound.collect(max_digits=3, timeout=1.0, terminator="#")
    assert result == "123"


@pytest.mark.asyncio
async def test_collect_respects_max_digits() -> None:
    """max_digits に達したら収集を停止する（余分な桁は残る）。"""
    collector, bound = make_collector_with_digits("u1", "12345")
    result = await bound.collect(max_digits=3, timeout=1.0, terminator="")
    assert result == "123"


@pytest.mark.asyncio
async def test_terminator_ends_collection_and_excluded() -> None:
    """終端キーで収集が終わり、終端キー自体は結果に含まれない。"""
    collector, bound = make_collector_with_digits("u1", "12#9")
    result = await bound.collect(max_digits=10, timeout=1.0, terminator="#")
    # "#" で終了 → "12" のみ
    assert result == "12"


@pytest.mark.asyncio
async def test_empty_terminator_means_no_terminator() -> None:
    """terminator == "" のとき終端キーは機能しない（max_digits まで収集）。"""
    collector, bound = make_collector_with_digits("u1", "12#9")
    result = await bound.collect(max_digits=4, timeout=1.0, terminator="")
    assert result == "12#9"


@pytest.mark.asyncio
async def test_collect_single_digit() -> None:
    """max_digits=1 で単桁のみ返す。"""
    collector, bound = make_collector_with_digits("u1", "5")
    result = await bound.collect(max_digits=1, timeout=1.0, terminator="")
    assert result == "5"


# --------------------------------------------------------------------------- #
# collect → feed (バックグラウンドで後から桁を供給)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_collect_then_feed_buffering() -> None:
    """collect 開始後に別タスクから feed されても桁を受け取れる。"""
    collector = DtmfCollector()
    collector.register("u2")
    bound = collector.bind("u2")

    async def _feeder() -> None:
        await asyncio.sleep(0.05)
        collector.feed("u2", "7")
        await asyncio.sleep(0.05)
        collector.feed("u2", "8")

    asyncio.create_task(_feeder())
    result = await bound.collect(max_digits=2, timeout=2.0, terminator="")
    assert result == "78"


@pytest.mark.asyncio
async def test_collect_timeout_returns_partial() -> None:
    """タイムアウト前に収集できた桁だけを返す（一部収集）。"""
    collector = DtmfCollector()
    collector.register("u3")
    bound = collector.bind("u3")

    async def _feeder() -> None:
        await asyncio.sleep(0.02)
        collector.feed("u3", "4")
        # 2 桁目は timeout 後に届く
        await asyncio.sleep(0.3)
        collector.feed("u3", "5")

    asyncio.create_task(_feeder())
    result = await bound.collect(max_digits=2, timeout=0.1, terminator="")
    # timeout 内に "4" だけ収集できる
    assert result == "4"


@pytest.mark.asyncio
async def test_collect_timeout_empty_when_nothing_pressed() -> None:
    """何も入力されないままタイムアウトしたら空文字列を返す。"""
    collector = DtmfCollector()
    collector.register("u4")
    bound = collector.bind("u4")
    result = await bound.collect(max_digits=3, timeout=0.05, terminator="")
    assert result == ""


# --------------------------------------------------------------------------- #
# feed のトレランス
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_feed_to_unregistered_uuid_auto_registers() -> None:
    """未登録 uuid への feed は自動登録してキューに入れる（桁の取りこぼし防止）。"""
    collector = DtmfCollector()
    # register せずに feed
    collector.feed("u5", "9")
    # bind して収集できること
    bound = collector.bind("u5")
    result = await bound.collect(max_digits=1, timeout=0.1, terminator="")
    assert result == "9"


@pytest.mark.asyncio
async def test_feed_empty_digit_is_noop() -> None:
    """空文字列の digit は無視される。"""
    collector = DtmfCollector()
    collector.register("u6")
    collector.feed("u6", "")  # no-op
    collector.feed("u6", "3")
    bound = collector.bind("u6")
    result = await bound.collect(max_digits=1, timeout=0.1, terminator="")
    assert result == "3"


# --------------------------------------------------------------------------- #
# unregister クリーンアップ
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unregister_removes_queue() -> None:
    """unregister するとキューが削除される。"""
    collector = DtmfCollector()
    collector.register("u7")
    collector.feed("u7", "1")
    assert "u7" in collector._queues
    collector.unregister("u7")
    assert "u7" not in collector._queues


@pytest.mark.asyncio
async def test_unregister_unknown_uuid_is_noop() -> None:
    """未登録 uuid の unregister は例外を出さない。"""
    collector = DtmfCollector()
    collector.unregister("nonexistent")  # no exception


@pytest.mark.asyncio
async def test_feed_after_unregister_creates_new_queue() -> None:
    """unregister 後に feed すると新しいキューが自動生成される（遅延イベント吸収）。"""
    collector = DtmfCollector()
    collector.register("u8")
    collector.unregister("u8")
    assert "u8" not in collector._queues
    collector.feed("u8", "2")
    assert "u8" in collector._queues


# --------------------------------------------------------------------------- #
# terminator がちょうど max_digits 目に来た場合
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_terminator_at_max_digits_boundary() -> None:
    """max_digits 桁目に終端キーが来たら、終端キーの前の桁だけを返す。"""
    collector, bound = make_collector_with_digits("u9", "12#")
    result = await bound.collect(max_digits=3, timeout=1.0, terminator="#")
    # "#" は terminator なので除外 → "12"
    assert result == "12"


@pytest.mark.asyncio
async def test_only_terminator_pressed_returns_empty() -> None:
    """最初に終端キーだけが押されたら空文字列を返す。"""
    collector, bound = make_collector_with_digits("u10", "#")
    result = await bound.collect(max_digits=3, timeout=1.0, terminator="#")
    assert result == ""
