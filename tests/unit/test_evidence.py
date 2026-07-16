"""Evidence-gated completion: a bare 'done' cannot forge success.

These tests exercise ``derive_final_state`` in isolation. They are *mechanics*
tests — the real goal-to-result acceptance lives in the c1 integration slice
against a real Kanban DB. Here we prove the gate rejects every shape of
under-evidenced completion.
"""

from __future__ import annotations

from hca.evidence import ExecutionEvidence, TaskEvidence, derive_final_state
from hca.result import Artifact
from hca.run import RunBudgets, RunSpec, RunState


def _spec(review_policy: str = "never", **kw) -> RunSpec:
    return RunSpec(
        run_id="run-x",
        goal="g",
        review_policy=review_policy,
        budgets=RunBudgets(),
        **kw,
    )


def _done_task(**kw) -> TaskEvidence:
    base = dict(
        task_id="t1",
        assignee="hca-f-coder-01",
        terminal_status="done",
        run_id=7,
        pid=4321,
        result="did the thing",
        artifacts=[Artifact(name="out.txt", kind="kanban", ref="t1")],
        is_root=True,
    )
    base.update(kw)
    return TaskEvidence(**base)


def test_no_tasks_is_blocked_never_completed():
    state, reason = derive_final_state(_spec(), ExecutionEvidence())
    assert state == RunState.BLOCKED
    assert "no upstream" in reason.lower()


def test_full_evidence_completes():
    ev = ExecutionEvidence(root_task_id="t1", tasks=[_done_task()])
    state, _ = derive_final_state(_spec(), ev)
    assert state == RunState.COMPLETED


def test_done_without_run_id_is_not_completion():
    ev = ExecutionEvidence(root_task_id="t1", tasks=[_done_task(run_id=None)])
    state, reason = derive_final_state(_spec(), ev)
    assert state == RunState.BLOCKED
    assert "run id" in reason.lower()


def test_done_without_pid_is_not_completion():
    ev = ExecutionEvidence(root_task_id="t1", tasks=[_done_task(pid=None)])
    state, reason = derive_final_state(_spec(), ev)
    assert state == RunState.BLOCKED
    assert "pid" in reason.lower()


def test_done_without_result_or_artifact_is_not_completion():
    ev = ExecutionEvidence(
        root_task_id="t1", tasks=[_done_task(result="", artifacts=[])]
    )
    state, reason = derive_final_state(_spec(), ev)
    assert state == RunState.BLOCKED
    assert "result or artifact" in reason.lower()


def test_non_terminal_task_holds_blocked():
    ev = ExecutionEvidence(
        root_task_id="t1", tasks=[_done_task(terminal_status="running")]
    )
    state, reason = derive_final_state(_spec(), ev)
    assert state == RunState.BLOCKED
    assert "in flight" in reason.lower()


def test_fatal_task_is_failed():
    ev = ExecutionEvidence(
        root_task_id="t1", tasks=[_done_task(terminal_status="crashed")]
    )
    state, _ = derive_final_state(_spec(), ev)
    assert state == RunState.FAILED


def test_blocked_task_is_blocked():
    ev = ExecutionEvidence(
        root_task_id="t1", tasks=[_done_task(terminal_status="blocked")]
    )
    state, _ = derive_final_state(_spec(), ev)
    assert state == RunState.BLOCKED


def test_review_required_but_absent_blocks():
    spec = _spec(review_policy="always")
    ev = ExecutionEvidence(root_task_id="t1", tasks=[_done_task()])
    state, reason = derive_final_state(spec, ev)
    assert state == RunState.BLOCKED
    assert "review required" in reason.lower()


def test_review_by_implementer_is_not_independent():
    spec = _spec(review_policy="always")
    work = _done_task(task_id="w1", assignee="hca-f-coder-01", is_root=False)
    root = _done_task(task_id="root", is_root=True)
    review = _done_task(
        task_id="rev",
        assignee="hca-f-qa-01",
        is_review=True,
        reviewed_by="hca-f-coder-01",  # the implementer reviewing itself
        is_root=False,
    )
    ev = ExecutionEvidence(root_task_id="root", tasks=[work, root, review])
    state, reason = derive_final_state(spec, ev)
    assert state == RunState.BLOCKED
    assert "independent" in reason.lower()


def test_independent_review_completes():
    spec = _spec(review_policy="always")
    work = _done_task(task_id="w1", assignee="hca-f-coder-01", is_root=False)
    root = _done_task(task_id="root", is_root=True)
    review = _done_task(
        task_id="rev",
        assignee="hca-f-qa-01",
        is_review=True,
        reviewed_by="hca-f-qa-01",
        is_root=False,
    )
    ev = ExecutionEvidence(root_task_id="root", tasks=[work, root, review])
    state, _ = derive_final_state(spec, ev)
    assert state == RunState.COMPLETED
