"""Decomposition barrier: DAG validation + canonical fan-in enforcement."""

from __future__ import annotations

from hca.decompose import TaskNode, validate_task_graph


def _work(id, deps=(), **kw):
    return TaskNode(
        id=id, title=id, depends_on=tuple(deps),
        acceptance_criteria=("done",), scope="module", **kw
    )


def _valid_graph():
    return [
        _work("plan", kind="work"),
        _work("a", deps=("plan",)),
        _work("b", deps=("plan",)),
        _work("integrate", deps=("a", "b"), kind="integration"),
        TaskNode(id="verify", title="verify", kind="verification",
                 depends_on=("integrate",), scope="all"),
        TaskNode(id="rework", title="rework", kind="rework",
                 depends_on=("verify",), scope="all"),
        TaskNode(id="final", title="final", kind="final",
                 depends_on=("rework",), scope="all"),
    ]


def test_valid_graph_passes_and_is_ordered():
    res = validate_task_graph(_valid_graph(), max_tasks=20)
    assert res.valid, res.reasons
    # plan comes before its dependents; final comes last
    assert res.order.index("plan") < res.order.index("a")
    assert res.order[-1] == "final"


def test_cycle_is_rejected():
    nodes = [
        _work("a", deps=("b",)),
        _work("b", deps=("a",)),
        TaskNode(id="final", kind="final", scope="x"),
    ]
    res = validate_task_graph(nodes)
    assert not res.valid
    assert any("cyclic" in r for r in res.reasons)


def test_dangling_dependency_rejected():
    nodes = [_work("a", deps=("ghost",)), TaskNode(id="final", kind="final", scope="x")]
    res = validate_task_graph(nodes)
    assert not res.valid
    assert any("unknown task" in r for r in res.reasons)


def test_oversized_graph_rejected():
    nodes = [_work(f"t{i}") for i in range(5)] + [TaskNode(id="final", kind="final", scope="x")]
    res = validate_task_graph(nodes, max_tasks=3)
    assert not res.valid
    assert any("exceeds budget" in r for r in res.reasons)


def test_missing_acceptance_criteria_rejected():
    nodes = [
        TaskNode(id="a", kind="work", scope="x"),  # no acceptance criteria
        TaskNode(id="final", kind="final", scope="x"),
    ]
    res = validate_task_graph(nodes)
    assert not res.valid
    assert any("acceptance criteria" in r for r in res.reasons)


def test_final_must_depend_on_gate():
    # verification/rework exist but final does not depend on them → rejected
    nodes = [
        _work("a"),
        TaskNode(id="verify", kind="verification", depends_on=("a",), scope="x"),
        TaskNode(id="final", kind="final", depends_on=("a",), scope="x"),  # skips gate
    ]
    res = validate_task_graph(nodes)
    assert not res.valid
    assert any("rework/no-op gate" in r for r in res.reasons)


def test_two_finals_rejected():
    nodes = [
        _work("a"),
        TaskNode(id="f1", kind="final", depends_on=("a",), scope="x"),
        TaskNode(id="f2", kind="final", depends_on=("a",), scope="x"),
    ]
    res = validate_task_graph(nodes)
    assert not res.valid
    assert any("exactly one final" in r for r in res.reasons)


def test_empty_graph_rejected():
    res = validate_task_graph([])
    assert not res.valid
