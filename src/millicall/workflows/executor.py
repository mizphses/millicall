"""Stackless workflow execution engine core (Phase 4b Task 3).

``WorkflowExecutor`` runs a :class:`WorkflowDefinition` as an explicit
state machine: a ``current`` pointer advanced by a bounded ``while`` loop (never
recursion). Each step dispatches the current node to a registered handler,
then resolves the next node by matching the handler's returned *output-handle
name* against the node's ``output_handles`` and following the corresponding
edge's ``sourceHandle``.

Design rules (v2 fixes the old engine's silent behaviour):
  * **Unregistered node type -> explicit error** (old: warn-and-pass).
  * **No edge matches the handler result -> explicit error** (old: fall through
    to the first edge). Single-output nodes may return ``None`` and, with no
    ``out`` edge wired, terminate normally.
  * **Terminal nodes** (no output handles) end the run.
  * **Step limit + goto cycle guard** guarantee termination even for cyclic
    graphs that slipped past save-time validation.

Node handlers are supplied by Tasks 4–7 through :func:`register_handler`
(``async def handler(node, ctx) -> str | None``). The core registers only the
minimal built-ins (start / end / hangup) plus built-in *navigation* for goto.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from millicall.workflows.context import ChannelContext
from millicall.workflows.errors import WorkflowExecutionError
from millicall.workflows.handles import output_handles

if TYPE_CHECKING:
    from millicall.workflows.schema import WorkflowDefinition, WorkflowEdge

# A node handler: given the typed node and the channel context, perform the
# node's side effects and return the output-handle name to follow (or None for
# single-output nodes, which default to the "out" handle).
Handler = Callable[[Any, ChannelContext], Awaitable["str | None"]]

DEFAULT_STEP_LIMIT = 500

# --------------------------------------------------------------------------- #
# Handler registry (Tasks 4–7 populate this at import time)
# --------------------------------------------------------------------------- #

HANDLERS: dict[str, Handler] = {}


def register_handler(node_type: str, fn: Handler | None = None) -> Any:
    """Register a node handler. Usable as a call or a decorator.

    ``register_handler("api_call", fn)`` or::

        @register_handler("api_call")
        async def handle(node, ctx): ...
    """
    if fn is None:

        def _decorator(func: Handler) -> Handler:
            HANDLERS[node_type] = func
            return func

        return _decorator

    HANDLERS[node_type] = fn
    return fn


def get_handlers() -> dict[str, Handler]:
    """Return a copy of the current global handler registry."""
    return dict(HANDLERS)


# --------------------------------------------------------------------------- #
# Core built-in handlers
# --------------------------------------------------------------------------- #


async def _core_start(node: Any, ctx: ChannelContext) -> str | None:
    # start is a pass-through; the dialplan owns ring_count. Follow "out".
    return None


async def _core_end(node: Any, ctx: ChannelContext) -> str | None:
    # terminal: no output handles -> the loop ends after this handler.
    return None


async def _core_hangup(node: Any, ctx: ChannelContext) -> str | None:
    await ctx.hangup()
    return None


_CORE_HANDLERS: dict[str, Handler] = {
    "start": _core_start,
    "end": _core_end,
    "hangup": _core_hangup,
}


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #


@dataclass
class RunResult:
    """Outcome of a workflow run."""

    reached_nodes: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    terminal: str | None = None  # type of the node where the run ended
    steps: int = 0


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #


class WorkflowExecutor:
    """Stackless state-machine executor for a single workflow run."""

    def __init__(
        self,
        definition: WorkflowDefinition,
        ctx: ChannelContext,
        *,
        handlers: dict[str, Handler] | None = None,
        step_limit: int = DEFAULT_STEP_LIMIT,
    ) -> None:
        self._defn = definition
        self._ctx = ctx
        self._step_limit = step_limit
        # Core built-ins first, then the caller's / global registry on top so
        # Task 4–7 handlers can override the minimal defaults.
        merged: dict[str, Handler] = dict(_CORE_HANDLERS)
        merged.update(get_handlers() if handlers is None else handlers)
        self._handlers = merged

        self._node_map: dict[str, Any] = {n.id: n for n in definition.nodes}
        self._edges_by_source: dict[str, list[WorkflowEdge]] = {}
        for e in definition.edges:
            self._edges_by_source.setdefault(e.source, []).append(e)

    async def execute(self) -> RunResult:
        current = self._find_start()
        reached: list[str] = []
        steps = 0

        while current is not None:
            steps += 1
            if steps > self._step_limit:
                raise WorkflowExecutionError(
                    f"workflow exceeded step limit ({self._step_limit}); "
                    f"aborting to prevent an infinite loop (last node {current.id!r})"
                )
            reached.append(current.id)

            # Built-in navigation node: goto jumps to its config target and is
            # not dispatched to a handler. Pure goto chains are cycle-guarded.
            if current.type == "goto":
                current = self._follow_goto(current)
                continue

            handler = self._handlers.get(current.type)
            if handler is None:
                raise WorkflowExecutionError(
                    f"no handler registered for node type {current.type!r} (node {current.id!r})"
                )

            result = await handler(current, self._ctx)

            if self._ctx.hung_up:
                return self._done(reached, current.type, steps)

            handles = output_handles(current)
            if not handles:
                # terminal node -> normal termination
                return self._done(reached, current.type, steps)

            nxt = self._resolve_next(current, result)
            if nxt is None:
                # single-output node with no wired "out" edge -> normal end
                return self._done(reached, current.type, steps)
            current = nxt

        return self._done(reached, None, steps)

    # ------------------------------------------------------------------ #

    def _find_start(self) -> Any:
        starts = [n for n in self._defn.nodes if n.type == "start"]
        if len(starts) != 1:
            raise WorkflowExecutionError(
                f"workflow must have exactly one start node (found {len(starts)})"
            )
        return starts[0]

    def _follow_goto(self, node: Any) -> Any:
        """Resolve a goto (or a chain of gotos) to the first non-goto node.

        A local ``seen`` set catches pure goto cycles immediately; broader
        cross-node cycles are caught by the outer step limit.
        """
        seen: set[str] = set()
        cur = node
        while cur is not None and cur.type == "goto":
            if cur.id in seen:
                raise WorkflowExecutionError(f"goto cycle detected at node {cur.id!r}")
            seen.add(cur.id)
            target_id = cur.config.target_node_id
            nxt = self._node_map.get(target_id)
            if nxt is None:
                raise WorkflowExecutionError(
                    f"goto node {cur.id!r} targets unknown node {target_id!r}"
                )
            cur = nxt
        return cur

    def _resolve_next(self, node: Any, result: str | None) -> Any:
        """Follow the edge whose sourceHandle matches the handler result.

        ``result=None`` defaults to the ``out`` handle. A non-None result that
        is not a valid output handle (i.e. not in the node's vocabulary) is an
        explicit :class:`WorkflowExecutionError` — that signals a handler bug,
        not an authoring gap. A *valid* handle with no wired edge terminates the
        run normally: a caller who times out a ``menu``, or reaches an unwired
        ``false``/``error``/``timeout``/fallback branch, must NOT have the live
        call dropped (save-time warnings flag such unwired branches instead).
        """
        handles = output_handles(node)
        handle = result if result is not None else "out"

        if result is not None and result not in handles:
            raise WorkflowExecutionError(
                f"node {node.id!r} ({node.type}) handler returned handle "
                f"{result!r} which is not in its output handles {handles}"
            )

        for edge in self._edges_by_source.get(node.id, []):
            if edge.sourceHandle == handle:
                target = self._node_map.get(edge.target)
                if target is None:
                    raise WorkflowExecutionError(
                        f"edge {edge.id!r} targets unknown node {edge.target!r}"
                    )
                return target

        # No edge wired for this (valid) handle -> terminate the run normally
        # rather than dropping the live call.
        return None

    def _done(self, reached: list[str], terminal: str | None, steps: int) -> RunResult:
        return RunResult(
            reached_nodes=reached,
            variables=dict(self._ctx.variables),
            terminal=terminal,
            steps=steps,
        )
