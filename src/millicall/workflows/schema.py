"""Workflow definition container + save-time graph validation (Task 1).

``WorkflowDefinition`` is the strict, typed graph ({nodes, edges}). Node typing /
required-config enforcement happens at parse time via the discriminated union;
``validate_graph`` covers the structural rules that Pydantic cannot express on
its own (start uniqueness, edge referential integrity, handle-vocabulary
membership, goto/call_workflow cycles, reachability warnings).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from millicall.workflows.handles import output_handles
from millicall.workflows.nodes import WorkflowNode  # noqa: TC001  (runtime: Pydantic field)


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    target: str = Field(..., min_length=1)
    sourceHandle: str | None = None  # noqa: N815 - xyflow wire format
    targetHandle: str | None = None  # noqa: N815 - xyflow wire format
    label: str | None = None


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


@dataclass
class ValidationResult:
    """Outcome of :func:`validate_graph`.

    ``errors`` are hard violations (API rejects with 422). ``warnings`` are
    advisory (e.g. unreachable nodes) and returned alongside a 200 save.
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_graph(
    definition: WorkflowDefinition, workflow_id: int | None = None
) -> ValidationResult:
    """Validate a parsed workflow graph.

    Checks: (a) exactly one start node, (b) every edge source/target references
    an existing node, (c) every edge sourceHandle is in the source node's handle
    vocabulary, (e) goto target existence + goto/call_workflow cycle detection,
    (f) unreachable-node warnings. Required-config enforcement (d) already
    happened at parse time.
    """
    result = ValidationResult()
    node_map: dict[str, Any] = {node.id: node for node in definition.nodes}

    # (a) exactly one start
    starts = [n for n in definition.nodes if n.type == "start"]
    if len(starts) == 0:
        result.errors.append("workflow must have exactly one start node (found 0)")
    elif len(starts) > 1:
        ids = ", ".join(n.id for n in starts)
        result.errors.append(
            f"workflow must have exactly one start node (found {len(starts)}: {ids})"
        )

    # (b) + (c) edges
    for edge in definition.edges:
        if edge.source not in node_map:
            result.errors.append(f"edge '{edge.id}' references unknown source node '{edge.source}'")
        if edge.target not in node_map:
            result.errors.append(f"edge '{edge.id}' references unknown target node '{edge.target}'")
        if edge.source in node_map:
            source_node = node_map[edge.source]
            allowed = output_handles(source_node)
            handle = edge.sourceHandle
            if not allowed:
                result.errors.append(
                    f"edge '{edge.id}' leaves node '{edge.source}' "
                    f"({source_node.type}) which has no output handles"
                )
            elif handle is None or handle not in allowed:
                result.errors.append(
                    f"edge '{edge.id}' uses sourceHandle '{handle}' not in "
                    f"vocabulary for node '{edge.source}' ({source_node.type}): {allowed}"
                )

    # (e) goto target existence + goto cycle detection
    for node in definition.nodes:
        if node.type == "goto":
            target = node.config.target_node_id
            if target not in node_map:
                result.errors.append(f"goto node '{node.id}' targets unknown node '{target}'")

    _detect_goto_cycles(definition, node_map, result)

    # (e) call_workflow self-recursion (cross-workflow cycles are DB-level / Task 2)
    if workflow_id is not None:
        for node in definition.nodes:
            if node.type == "call_workflow" and node.config.workflow_id == workflow_id:
                result.errors.append(
                    f"call_workflow node '{node.id}' creates a cycle "
                    f"(references its own workflow id {workflow_id})"
                )

    # (f) reachability warnings (only meaningful with a single start)
    if len(starts) == 1:
        _reachability_warnings(definition, node_map, starts[0].id, result)

    return result


def _detect_goto_cycles(
    definition: WorkflowDefinition, node_map: dict[str, Any], result: ValidationResult
) -> None:
    reported: set[frozenset[str]] = set()
    for node in definition.nodes:
        if node.type != "goto":
            continue
        seen: list[str] = []
        cur = node
        while cur is not None and cur.type == "goto":
            if cur.id in seen:
                cycle = frozenset(seen[seen.index(cur.id) :])
                if cycle not in reported:
                    reported.add(cycle)
                    chain = " -> ".join(seen[seen.index(cur.id) :] + [cur.id])
                    result.errors.append(f"goto cycle detected: {chain}")
                break
            seen.append(cur.id)
            nxt_id = cur.config.target_node_id
            cur = node_map.get(nxt_id)


def _reachability_warnings(
    definition: WorkflowDefinition,
    node_map: dict[str, Any],
    start_id: str,
    result: ValidationResult,
) -> None:
    # adjacency: edges (source -> target) + implicit goto (node -> target_node_id)
    adjacency: dict[str, set[str]] = {node.id: set() for node in definition.nodes}
    for edge in definition.edges:
        if edge.source in adjacency and edge.target in node_map:
            adjacency[edge.source].add(edge.target)
    for node in definition.nodes:
        if node.type == "goto":
            target = node.config.target_node_id
            if target in node_map:
                adjacency[node.id].add(target)

    reachable: set[str] = set()
    stack = [start_id]
    while stack:
        cur = stack.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        stack.extend(adjacency.get(cur, ()))

    for node in definition.nodes:
        if node.id not in reachable:
            result.warnings.append(f"node '{node.id}' ({node.type}) is unreachable from start")
