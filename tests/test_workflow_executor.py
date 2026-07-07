"""Task 3: stackless workflow execution engine core.

Covers the executor (WorkflowExecutor), ChannelContext, template rendering,
handler dispatch/registration, strict transition resolution, step limit and
cycle guards. No real ESL — fake handlers + a bare ChannelContext.
"""

from __future__ import annotations

import pytest

from millicall.workflows.context import ChannelContext, render_template
from millicall.workflows.errors import WorkflowExecutionError
from millicall.workflows.executor import (
    HANDLERS,
    RunResult,
    WorkflowExecutor,
    get_handlers,
    register_handler,
)
from millicall.workflows.schema import WorkflowDefinition


def make_def(nodes: list[dict], edges: list[dict]) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({"nodes": nodes, "edges": edges})


def edge(eid: str, source: str, handle: str, target: str) -> dict:
    return {"id": eid, "source": source, "sourceHandle": handle, "target": target}


@pytest.fixture
def ctx() -> ChannelContext:
    return ChannelContext(uuid="test-uuid-1")


@pytest.fixture(autouse=True)
def _restore_registry():
    snapshot = dict(HANDLERS)
    yield
    HANDLERS.clear()
    HANDLERS.update(snapshot)


# --------------------------------------------------------------------------- #
# render_template
# --------------------------------------------------------------------------- #


def test_render_template_substitutes_known_var() -> None:
    assert render_template("Hello {{name}}", {"name": "Bob"}) == "Hello Bob"


def test_render_template_undefined_var_becomes_empty() -> None:
    assert render_template("x={{missing}}!", {}) == "x=!"


def test_render_template_tolerates_whitespace_in_braces() -> None:
    assert render_template("{{ name }}", {"name": "Al"}) == "Al"


def test_render_template_leaves_invalid_var_name_literal() -> None:
    # '1abc' is not a valid variable name -> not treated as a reference
    assert render_template("{{1abc}}", {"1abc": "no"}) == "{{1abc}}"


def test_render_template_multiple_vars() -> None:
    out = render_template("{{a}}-{{b}}-{{a}}", {"a": "1", "b": "2"})
    assert out == "1-2-1"


# --------------------------------------------------------------------------- #
# ChannelContext
# --------------------------------------------------------------------------- #


def test_context_render_uses_own_variables(ctx: ChannelContext) -> None:
    ctx.variables["who"] = "world"
    assert ctx.render("hi {{who}}") == "hi world"


def test_context_set_var_rejects_invalid_name(ctx: ChannelContext) -> None:
    with pytest.raises(ValueError):
        ctx.set_var("1bad", "x")


def test_context_set_var_stringifies(ctx: ChannelContext) -> None:
    ctx.set_var("n", 5)
    assert ctx.variables["n"] == "5"


@pytest.mark.asyncio
async def test_context_hangup_sets_flag_and_calls_control() -> None:
    calls: list[str] = []

    class FakeControl:
        async def hangup(self) -> None:
            calls.append("hangup")

    ctx = ChannelContext(uuid="u", call_control=FakeControl())
    await ctx.hangup()
    assert ctx.hung_up is True
    assert calls == ["hangup"]


@pytest.mark.asyncio
async def test_context_hangup_without_control_just_sets_flag(ctx: ChannelContext) -> None:
    await ctx.hangup()
    assert ctx.hung_up is True


# --------------------------------------------------------------------------- #
# Executor: linear flow
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_linear_flow_completes(ctx: ChannelContext) -> None:
    visited: list[str] = []

    async def fake_set_var(node, c):
        visited.append(node.id)
        return None

    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "a", "type": "set_variable", "config": {"variable": "x", "value": "1"}},
            {"id": "e", "type": "end"},
        ],
        [edge("e1", "s", "out", "a"), edge("e2", "a", "out", "e")],
    )
    result = await WorkflowExecutor(defn, ctx, handlers={"set_variable": fake_set_var}).execute()
    assert isinstance(result, RunResult)
    assert result.reached_nodes == ["s", "a", "e"]
    assert result.terminal == "end"
    assert visited == ["a"]


# --------------------------------------------------------------------------- #
# Executor: branching (handle match selects route)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_branch_true_route(ctx: ChannelContext) -> None:
    async def cond(node, c):
        return "true"

    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "c", "type": "condition", "config": {"variable": "x", "operator": "eq", "value": "1"}},
            {"id": "t", "type": "end"},
            {"id": "f", "type": "hangup"},
        ],
        [
            edge("e1", "s", "out", "c"),
            edge("e2", "c", "true", "t"),
            edge("e3", "c", "false", "f"),
        ],
    )
    result = await WorkflowExecutor(defn, ctx, handlers={"condition": cond}).execute()
    assert result.reached_nodes == ["s", "c", "t"]
    assert result.terminal == "end"


@pytest.mark.asyncio
async def test_branch_false_route(ctx: ChannelContext) -> None:
    async def cond(node, c):
        return "false"

    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "c", "type": "condition", "config": {"variable": "x", "operator": "eq", "value": "1"}},
            {"id": "t", "type": "end"},
            {"id": "f", "type": "hangup"},
        ],
        [
            edge("e1", "s", "out", "c"),
            edge("e2", "c", "true", "t"),
            edge("e3", "c", "false", "f"),
        ],
    )
    result = await WorkflowExecutor(defn, ctx, handlers={"condition": cond}).execute()
    assert result.reached_nodes == ["s", "c", "f"]
    assert result.terminal == "hangup"


# --------------------------------------------------------------------------- #
# Executor: valid handle with no wired edge -> normal termination (H1 fix).
# A caller who reaches an unwired 'true'/'timeout'/'error'/fallback branch must
# NOT have the live call dropped; the run just ends at that node.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_valid_handle_no_wired_edge_terminates_normally(ctx: ChannelContext) -> None:
    async def cond(node, c):
        return "true"

    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "c", "type": "condition", "config": {"variable": "x", "operator": "eq", "value": "1"}},
            {"id": "f", "type": "end"},
        ],
        # only the 'false' edge is wired; handler returns the valid 'true' handle
        [edge("e1", "s", "out", "c"), edge("e2", "c", "false", "f")],
    )
    result = await WorkflowExecutor(defn, ctx, handlers={"condition": cond}).execute()
    # run ends at the condition node, no error, call not dropped
    assert result.reached_nodes == ["s", "c"]
    assert result.terminal == "condition"


@pytest.mark.asyncio
async def test_handler_returns_unknown_handle_is_error(ctx: ChannelContext) -> None:
    async def cond(node, c):
        return "maybe"  # not in ["true","false"]

    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "c", "type": "condition", "config": {"variable": "x", "operator": "eq", "value": "1"}},
            {"id": "t", "type": "end"},
        ],
        [edge("e1", "s", "out", "c"), edge("e2", "c", "true", "t")],
    )
    with pytest.raises(WorkflowExecutionError):
        await WorkflowExecutor(defn, ctx, handlers={"condition": cond}).execute()


# --------------------------------------------------------------------------- #
# Executor: unregistered handler -> explicit error (no warn-and-pass)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unregistered_handler_is_error(ctx: ChannelContext) -> None:
    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "p", "type": "play_audio", "config": {"tts_text": "hi"}},
            {"id": "e", "type": "end"},
        ],
        [edge("e1", "s", "out", "p"), edge("e2", "p", "out", "e")],
    )
    with pytest.raises(WorkflowExecutionError):
        await WorkflowExecutor(defn, ctx, handlers={}).execute()


# --------------------------------------------------------------------------- #
# Executor: step limit stops infinite loops
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_step_limit_stops_self_loop(ctx: ChannelContext) -> None:
    async def loop(node, c):
        return None

    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "a", "type": "set_variable", "config": {"variable": "x", "value": "1"}},
        ],
        [edge("e1", "s", "out", "a"), edge("e2", "a", "out", "a")],  # a -> a
    )
    with pytest.raises(WorkflowExecutionError):
        await WorkflowExecutor(
            defn, ctx, handlers={"set_variable": loop}, step_limit=10
        ).execute()


# --------------------------------------------------------------------------- #
# Executor: goto navigation + cycle guard
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_goto_navigates_to_target(ctx: ChannelContext) -> None:
    seen: list[str] = []

    async def mark(node, c):
        seen.append(node.id)
        return None

    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "g", "type": "goto", "config": {"target_node_id": "a"}},
            {"id": "a", "type": "set_variable", "config": {"variable": "x", "value": "1"}},
            {"id": "e", "type": "end"},
        ],
        [edge("e1", "s", "out", "g"), edge("e3", "a", "out", "e")],
    )
    result = await WorkflowExecutor(defn, ctx, handlers={"set_variable": mark}).execute()
    assert seen == ["a"]
    assert result.terminal == "end"
    assert "a" in result.reached_nodes


@pytest.mark.asyncio
async def test_goto_cycle_is_error(ctx: ChannelContext) -> None:
    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "g1", "type": "goto", "config": {"target_node_id": "g2"}},
            {"id": "g2", "type": "goto", "config": {"target_node_id": "g1"}},
        ],
        [edge("e1", "s", "out", "g1")],
    )
    with pytest.raises(WorkflowExecutionError):
        await WorkflowExecutor(defn, ctx).execute()


@pytest.mark.asyncio
async def test_goto_unknown_target_is_error(ctx: ChannelContext) -> None:
    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "g", "type": "goto", "config": {"target_node_id": "nope"}},
        ],
        [edge("e1", "s", "out", "g")],
    )
    with pytest.raises(WorkflowExecutionError):
        await WorkflowExecutor(defn, ctx).execute()


# --------------------------------------------------------------------------- #
# Executor: single-output result=None default transition + normal termination
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_single_output_no_out_edge_terminates(ctx: ChannelContext) -> None:
    async def noop(node, c):
        return None

    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "a", "type": "set_variable", "config": {"variable": "x", "value": "1"}},
        ],
        [edge("e1", "s", "out", "a")],  # a has no outgoing edge
    )
    result = await WorkflowExecutor(defn, ctx, handlers={"set_variable": noop}).execute()
    assert result.reached_nodes == ["s", "a"]


@pytest.mark.asyncio
async def test_terminal_node_terminates(ctx: ChannelContext) -> None:
    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "h", "type": "hangup"},
        ],
        [edge("e1", "s", "out", "h")],
    )
    result = await WorkflowExecutor(defn, ctx).execute()
    assert result.terminal == "hangup"
    assert ctx.hung_up is True


# --------------------------------------------------------------------------- #
# Executor: hangup mid-flow stops execution
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_hangup_flag_stops_execution(ctx: ChannelContext) -> None:
    async def hang(node, c):
        await c.hangup()
        return None

    reached_after = []

    async def should_not_run(node, c):
        reached_after.append(node.id)
        return None

    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "a", "type": "set_variable", "config": {"variable": "x", "value": "1"}},
            {"id": "b", "type": "play_audio", "config": {"tts_text": "hi"}},
        ],
        [edge("e1", "s", "out", "a"), edge("e2", "a", "out", "b")],
    )
    await WorkflowExecutor(
        defn, ctx, handlers={"set_variable": hang, "play_audio": should_not_run}
    ).execute()
    assert reached_after == []
    assert ctx.hung_up is True


# --------------------------------------------------------------------------- #
# Executor: missing start node -> error
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_missing_start_is_error(ctx: ChannelContext) -> None:
    defn = make_def([{"id": "e", "type": "end"}], [])
    with pytest.raises(WorkflowExecutionError):
        await WorkflowExecutor(defn, ctx).execute()


# --------------------------------------------------------------------------- #
# Registry: register_handler (function form + decorator) feeds default handlers
# --------------------------------------------------------------------------- #


def test_register_handler_function_form() -> None:
    async def h(node, c):
        return None

    register_handler("play_audio", h)
    assert get_handlers()["play_audio"] is h


def test_register_handler_decorator_form() -> None:
    @register_handler("api_call")
    async def h(node, c):
        return "success"

    assert get_handlers()["api_call"] is h


@pytest.mark.asyncio
async def test_executor_uses_global_registry_when_handlers_none(ctx: ChannelContext) -> None:
    calls: list[str] = []

    @register_handler("set_variable")
    async def h(node, c):
        calls.append(node.id)
        return None

    defn = make_def(
        [
            {"id": "s", "type": "start"},
            {"id": "a", "type": "set_variable", "config": {"variable": "x", "value": "1"}},
            {"id": "e", "type": "end"},
        ],
        [edge("e1", "s", "out", "a"), edge("e2", "a", "out", "e")],
    )
    result = await WorkflowExecutor(defn, ctx).execute()  # handlers=None -> global
    assert calls == ["a"]
    assert result.terminal == "end"
