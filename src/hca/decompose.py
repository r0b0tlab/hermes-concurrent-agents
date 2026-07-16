"""Decomposition barrier: validate a planner's task graph before release.

Children created by the planner are staged (blocked / dependent on the planner
task) and cannot dispatch until HCA validates the complete graph as an
acyclic, bounded DAG with well-formed dependencies, requirements, acceptance
criteria, expected artifacts, and scope. Invalid or oversized graphs return to
the planner once within budget, then block visibly — they never race
execution.

The canonical fan-in is enforced structurally:

    plan → independent work → integration → verification → rework/no-op gate
         → final result

so final collection depends on verification *and* the rework/no-op gate and
cannot race rejected work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TaskNode:
    id: str
    title: str = ""
    role_hint: str = ""
    depends_on: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()  # required tools/capabilities
    acceptance_criteria: tuple[str, ...] = ()
    expected_artifacts: tuple[str, ...] = ()
    scope: str = ""
    kind: str = "work"  # work | integration | verification | rework | final


@dataclass
class ValidationResult:
    valid: bool
    reasons: list[str] = field(default_factory=list)
    order: list[str] = field(default_factory=list)  # topological order when valid

    def to_dict(self) -> dict:
        return {"valid": self.valid, "reasons": self.reasons, "order": self.order}


def _topo_order(nodes: dict[str, TaskNode]) -> Optional[list[str]]:
    """Kahn's algorithm; returns None if a cycle exists."""
    indeg = {nid: 0 for nid in nodes}
    for n in nodes.values():
        for dep in n.depends_on:
            if dep in nodes:
                indeg[n.id] += 1
    # deterministic order: process ids sorted
    ready = sorted([nid for nid, d in indeg.items() if d == 0])
    order: list[str] = []
    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for m in nodes.values():
            if nid in m.depends_on and m.id in indeg:
                indeg[m.id] -= 1
                if indeg[m.id] == 0:
                    ready.append(m.id)
        ready.sort()
    if len(order) != len(nodes):
        return None
    return order


def validate_task_graph(
    nodes: list[TaskNode],
    *,
    max_tasks: int = 20,
    require_acceptance: bool = True,
) -> ValidationResult:
    reasons: list[str] = []

    if not nodes:
        return ValidationResult(False, ["empty task graph — planner produced no tasks"])

    node_map: dict[str, TaskNode] = {}
    for n in nodes:
        if n.id in node_map:
            reasons.append(f"duplicate task id {n.id!r}")
        node_map[n.id] = n

    if len(node_map) > max_tasks:
        reasons.append(
            f"task count {len(node_map)} exceeds budget max_tasks={max_tasks}"
        )

    # dependency references must exist (no dangling edges)
    for n in nodes:
        for dep in n.depends_on:
            if dep not in node_map:
                reasons.append(f"task {n.id!r} depends on unknown task {dep!r}")
            if dep == n.id:
                reasons.append(f"task {n.id!r} depends on itself")

    # per-node well-formedness (skip the planner-owned final node's artifacts)
    for n in nodes:
        if require_acceptance and n.kind in ("work", "integration") and not n.acceptance_criteria:
            reasons.append(f"task {n.id!r} has no acceptance criteria")
        if not n.scope:
            reasons.append(f"task {n.id!r} has no scope boundary")

    # acyclicity
    order = _topo_order(node_map)
    if order is None:
        reasons.append("task graph is cyclic — dependencies must form a DAG")

    # canonical fan-in: exactly one final node, and it must (transitively)
    # depend on a verification and a rework/no-op gate when such work exists.
    finals = [n for n in nodes if n.kind == "final"]
    if len(finals) != 1:
        reasons.append(f"expected exactly one final-result task, found {len(finals)}")
    else:
        final = finals[0]
        kinds_present = {n.kind for n in nodes}
        if "verification" in kinds_present or "rework" in kinds_present:
            gate_kinds = {"verification", "rework"}
            reachable = _ancestors(node_map, final.id)
            if not any(node_map[a].kind in gate_kinds for a in reachable):
                reasons.append(
                    "final-result task must depend on the verification + "
                    "rework/no-op gate (it must not race rejected work)"
                )

    valid = not reasons
    return ValidationResult(valid=valid, reasons=reasons, order=order or [])


def _ancestors(nodes: dict[str, TaskNode], start: str) -> set[str]:
    """All transitive dependencies of ``start``."""
    seen: set[str] = set()
    stack = list(nodes[start].depends_on) if start in nodes else []
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in nodes:
            continue
        seen.add(cur)
        stack.extend(nodes[cur].depends_on)
    return seen
