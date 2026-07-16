"""Run state machine + immutable spec + durable store."""

from __future__ import annotations

import pytest

from hca.run import (
    RunBudgets,
    RunSpec,
    RunState,
    RunStateError,
    RunStore,
    TERMINAL_STATES,
    can_transition,
    new_run_id,
)


def test_terminal_states_have_no_outgoing_success():
    assert RunState.COMPLETED in TERMINAL_STATES
    assert RunState.CANCELLED in TERMINAL_STATES
    # completed/failed/cancelled cannot transition anywhere
    assert not can_transition(RunState.COMPLETED, RunState.RUNNING)
    assert not can_transition(RunState.FAILED, RunState.RUNNING)
    assert not can_transition(RunState.CANCELLED, RunState.RUNNING)


def test_completion_requires_review_path():
    # running can reach completed (via the service it goes through review)
    assert can_transition(RunState.RUNNING, RunState.REVIEW)
    assert can_transition(RunState.REVIEW, RunState.COMPLETED)
    # planning cannot jump straight to completed
    assert not can_transition(RunState.PLANNING, RunState.COMPLETED)


def test_stop_only_reaches_cancelled_via_stopping():
    assert can_transition(RunState.RUNNING, RunState.STOPPING)
    assert can_transition(RunState.STOPPING, RunState.CANCELLED)
    # running cannot go straight to cancelled
    assert not can_transition(RunState.RUNNING, RunState.CANCELLED)


def test_runspec_roundtrip():
    spec = RunSpec(
        run_id=new_run_id(),
        goal="build a thing",
        constraints=("no network",),
        acceptance_criteria=("tests pass", "docs complete"),
        independent_criteria=True,
        budgets=RunBudgets(max_workers=2),
        idempotency_key="k1",
    )
    d = spec.to_dict()
    back = RunSpec.from_dict(d)
    assert back == spec
    assert back.budgets.max_workers == 2
    assert back.independent_criteria is True


def test_runspec_v1_defaults_to_no_inferred_independence():
    legacy = RunSpec.from_dict(
        {"run_id": "legacy", "goal": "g", "schema_version": 1}
    )
    assert legacy.independent_criteria is False
    assert legacy.schema_version == 1


def test_store_create_and_transition(tmp_path):
    store = RunStore(tmp_path / "hca.sqlite")
    spec = RunSpec(run_id=new_run_id(), goal="g")
    proj = store.create_run(spec)
    assert proj.state == RunState.QUEUED
    store.set_state(spec.run_id, RunState.PLANNING)
    store.set_state(spec.run_id, RunState.RUNNING)
    got = store.get_run(spec.run_id)
    assert got.state == RunState.RUNNING


def test_store_rejects_illegal_transition(tmp_path):
    store = RunStore(tmp_path / "hca.sqlite")
    spec = RunSpec(run_id=new_run_id(), goal="g")
    store.create_run(spec)
    with pytest.raises(RunStateError):
        store.set_state(spec.run_id, RunState.COMPLETED)  # queued -> completed illegal


def test_idempotency_lookup(tmp_path):
    store = RunStore(tmp_path / "hca.sqlite")
    spec = RunSpec(run_id=new_run_id(), goal="g", idempotency_key="abc")
    store.create_run(spec)
    found = store.find_by_idempotency_key("abc")
    assert found and found.run_id == spec.run_id
    assert store.find_by_idempotency_key("missing") is None


def test_questions_lifecycle(tmp_path):
    store = RunStore(tmp_path / "hca.sqlite")
    spec = RunSpec(run_id=new_run_id(), goal="g")
    store.create_run(spec)
    q = store.add_question(spec.run_id, "which framework?")
    assert store.open_questions(spec.run_id)
    store.answer_question(spec.run_id, q.question_id, "pytest")
    assert not store.open_questions(spec.run_id)
    # cannot answer twice
    with pytest.raises(RunStateError):
        store.answer_question(spec.run_id, q.question_id, "again")


def test_answer_wrong_run_rejected(tmp_path):
    store = RunStore(tmp_path / "hca.sqlite")
    s1 = RunSpec(run_id=new_run_id(), goal="g1")
    s2 = RunSpec(run_id=new_run_id(), goal="g2")
    store.create_run(s1)
    store.create_run(s2)
    q = store.add_question(s1.run_id, "?")
    with pytest.raises(RunStateError):
        store.answer_question(s2.run_id, q.question_id, "x")
