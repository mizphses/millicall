"""call_workflow ハンドラ（ネスト実行・循環防止・graceful スキップ）のテスト。"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import pytest

import millicall.workflows.handlers  # noqa: F401 — ハンドラ登録の副作用
from millicall.workflows.context import ChannelContext
from millicall.workflows.executor import HANDLERS
from millicall.workflows.handlers.special import handle_call_workflow


def make_call_workflow_node(workflow_id: int) -> Any:
    from millicall.workflows.nodes import CallWorkflowConfig, CallWorkflowNode

    return CallWorkflowNode(
        id="cw1",
        type="call_workflow",
        config=CallWorkflowConfig(workflow_id=workflow_id),
    )


class _FakeWorkflowRow:
    def __init__(self, definition: dict, *, enabled: bool = True) -> None:
        self.definition_json = json.dumps(definition)
        self.enabled = enabled


class _FakeSession:
    def __init__(self, rows: dict[int, _FakeWorkflowRow]) -> None:
        self._rows = rows

    async def get(self, _model: Any, pk: int) -> _FakeWorkflowRow | None:
        return self._rows.get(pk)


def _sessionmaker(rows: dict[int, _FakeWorkflowRow]):
    @contextlib.asynccontextmanager
    async def _cm():
        yield _FakeSession(rows)

    return _cm


# 最小のサブワークフロー定義: start → set_variable → end
_SUBFLOW = {
    "nodes": [
        {"id": "s", "type": "start"},
        {
            "id": "sv",
            "type": "set_variable",
            "config": {"variable": "sub_ran", "value": "yes"},
        },
        {"id": "e", "type": "end"},
    ],
    "edges": [
        {"id": "e1", "source": "s", "sourceHandle": "out", "target": "sv"},
        {"id": "e2", "source": "sv", "sourceHandle": "out", "target": "e"},
    ],
}


def test_call_workflow_registered() -> None:
    assert "call_workflow" in HANDLERS


@pytest.mark.asyncio
async def test_call_workflow_runs_subflow_sharing_ctx() -> None:
    """サブフローが同一 ctx で実行され、変数が親に反映される。"""
    ctx = ChannelContext(uuid="u1", sessionmaker=_sessionmaker({7: _FakeWorkflowRow(_SUBFLOW)}))
    ctx.active_workflow_ids = {1}  # 最上位は id=1

    result = await handle_call_workflow(make_call_workflow_node(7), ctx)

    assert result is None
    assert ctx.get_var("sub_ran") == "yes"
    # 実行後は呼び出しスタックから外れている
    assert ctx.active_workflow_ids == {1}


@pytest.mark.asyncio
async def test_call_workflow_cycle_is_skipped() -> None:
    """呼び出しスタックに既にある id はスキップ（無限再帰防止）。"""
    ctx = ChannelContext(uuid="u1", sessionmaker=_sessionmaker({1: _FakeWorkflowRow(_SUBFLOW)}))
    ctx.active_workflow_ids = {1}  # 自身が既に active

    result = await handle_call_workflow(make_call_workflow_node(1), ctx)

    assert result is None
    assert ctx.get_var("sub_ran") == ""  # サブフローは走っていない


@pytest.mark.asyncio
async def test_call_workflow_missing_or_disabled_skips() -> None:
    """対象不在/無効はスキップして親フロー継続。"""
    ctx = ChannelContext(uuid="u1", sessionmaker=_sessionmaker({9: _FakeWorkflowRow(_SUBFLOW, enabled=False)}))

    # 不在
    assert await handle_call_workflow(make_call_workflow_node(404), ctx) is None
    # 無効
    assert await handle_call_workflow(make_call_workflow_node(9), ctx) is None


@pytest.mark.asyncio
async def test_call_workflow_no_sessionmaker_skips() -> None:
    """sessionmaker 未配線（engine-core / unit）ではスキップ。"""
    ctx = ChannelContext(uuid="u1")  # sessionmaker=None
    assert await handle_call_workflow(make_call_workflow_node(7), ctx) is None


@pytest.mark.asyncio
async def test_call_workflow_bad_definition_skips() -> None:
    """不正な定義 JSON はスキップして親フロー継続（通話を落とさない）。"""
    ctx = ChannelContext(uuid="u1", sessionmaker=_sessionmaker({7: _FakeWorkflowRow({"nodes": "broken"})}))
    assert await handle_call_workflow(make_call_workflow_node(7), ctx) is None
