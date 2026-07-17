"""FleetService lifecycle: run/status/respond/collect/stop + exit codes.

The execution seam is a deterministic double that returns *observed evidence*,
not a bare enum — the service derives the terminal state from that evidence via
``derive_final_state``. These are **mechanics** tests: they prove the state
machine, exit codes, idempotency, and the anti-forgery gate at the service
boundary. The real goal-to-result acceptance lives in
``tests/integration/test_vertical_slice_c1.py`` against a real Kanban DB.
"""

from __future__ import annotations

from pathlib import Path

from hca.config import load_fleet_config
from hca.evidence import ExecutionEvidence, TaskEvidence
from hca.run import RunState, RunStore
from hca.service import (
    EXIT_BLOCKED,
    EXIT_INVALID,
    EXIT_OK,
    FleetService,
    PreflightOrchestrator,
)


def _cfg(tmp_path: Path):
    return load_fleet_config(model="m", state_dir=str(tmp_path))


def _done_evidence(review: bool = False) -> ExecutionEvidence:
    """Full evidence: a done task with a run id, a bound pid, and a result."""
    tasks = [
        TaskEvidence(
            task_id="t-work",
            assignee="hca-f-coder-01",
            terminal_status="done",
            run_id=11,
            pid=4242,
            result="implemented the thing",
            is_root=not review,
        )
    ]
    if review:
        tasks.append(
            TaskEvidence(
                task_id="t-rev",
                assignee="hca-f-qa-01",
                terminal_status="done",
                run_id=12,
                pid=4243,
                result="verified",
                is_review=True,
                reviewed_by="hca-f-qa-01",
                is_root=True,
            )
        )
    return ExecutionEvidence(root_task_id="t-work", tasks=tasks)


class CompletingOrchestrator:
    """Mechanics double: emits real evidence of a done task (no review)."""

    def plan(self, spec, store):
        store.append_event(spec.run_id, "run.plan", "1 task")
        return RunState.PLANNING

    def execute(self, spec, store):
        return _done_evidence(review=False)


class ReviewedOrchestrator:
    """Mechanics double: emits evidence including an independent reviewer."""

    def plan(self, spec, store):
        return RunState.PLANNING

    def execute(self, spec, store):
        return _done_evidence(review=True)


class ForgeryOrchestrator:
    """A double that *tries* to claim done without run id / pid / result."""

    def plan(self, spec, store):
        return RunState.PLANNING

    def execute(self, spec, store):
        return ExecutionEvidence(
            root_task_id="t1",
            tasks=[TaskEvidence(task_id="t1", terminal_status="done")],
        )


class NeedsInputOrchestrator:
    def plan(self, spec, store):
        store.add_question(spec.run_id, "which database?")
        return RunState.NEEDS_INPUT

    def execute(self, spec, store):  # pragma: no cover - not reached
        return ExecutionEvidence()


def test_default_orchestrator_is_honest_blocked(tmp_path, monkeypatch):
    # Simulate Hermes being unavailable so the default falls back to the
    # honest preflight block (never a fabricated completion).
    import hca.hermes_compat as hc

    def _raise(*a, **k):
        raise hc.HermesCompatError("hermes not installed")

    monkeypatch.setattr(hc, "import_kanban_db", _raise)
    svc = FleetService(_cfg(tmp_path))
    assert isinstance(svc.orchestrator, PreflightOrchestrator)
    res = svc.run("do something")
    assert res.state == "blocked"  # NOT completed — no backend admitted
    assert res.code == EXIT_BLOCKED
    assert res.remediation


def test_completing_orchestrator_reaches_completed(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=CompletingOrchestrator())
    res = svc.run("build x")
    assert res.state == "completed"
    assert res.code == EXIT_OK
    # completion must be recorded as evidence-derived, not a bare enum
    kinds = [e["kind"] for e in svc.store.list_events(res.run_id)]
    assert "run.evidence" in kinds


def test_reviewed_run_passes_through_review(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=ReviewedOrchestrator())
    res = svc.run("build x")
    assert res.state == "completed"
    msgs = [e["message"] for e in svc.store.list_events(res.run_id)]
    assert any("verifying" in m for m in msgs)


def test_forged_completion_is_rejected(tmp_path):
    # A 'done' task with no run id, pid, or result must NOT complete the run.
    svc = FleetService(_cfg(tmp_path), orchestrator=ForgeryOrchestrator())
    res = svc.run("build x")
    assert res.state == "blocked"
    assert res.state != "completed"


def test_empty_goal_is_invalid(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=PreflightOrchestrator())
    res = svc.run("   ")
    assert res.code == EXIT_INVALID
    assert not res.ok


def test_independence_declaration_requires_multiple_criteria(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=PreflightOrchestrator())
    res = svc.run(
        "build x",
        acceptance_criteria=["only one"],
        independent_criteria=True,
    )
    assert res.code == EXIT_INVALID
    assert "at least two" in res.message


def test_unknown_budget_and_overlarge_concurrency_are_rejected(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=PreflightOrchestrator())
    unknown = svc.run("build x", budgets={"mystery": 3})
    assert unknown.code == EXIT_INVALID
    assert "unknown budget" in unknown.message
    # small is explicitly one worker; the request must not be silently ignored.
    too_wide = svc.run("build x", team="small", concurrency=2)
    assert too_wide.code == EXIT_INVALID
    assert "exceeds" in too_wide.message


def test_idempotency_key_dedups(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=CompletingOrchestrator())
    a = svc.run("build x", idempotency_key="k1")
    b = svc.run("build x", idempotency_key="k1")
    assert a.run_id == b.run_id  # same run, not a new one
    c = svc.run("build x", idempotency_key="k2")
    assert c.run_id != a.run_id


def test_goal_text_never_dedups(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=CompletingOrchestrator())
    a = svc.run("same goal text")
    b = svc.run("same goal text")
    assert a.run_id != b.run_id  # no idempotency key → distinct runs


def test_needs_input_then_respond_resumes(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=NeedsInputOrchestrator())
    res = svc.run("ambiguous goal")
    assert res.state == "needs_input"
    assert res.code == EXIT_BLOCKED
    qs = svc.store.open_questions(res.run_id)
    assert len(qs) == 1
    bad = svc.respond(res.run_id, "q-nope", "answer")
    assert not bad.ok
    ok = svc.respond(res.run_id, qs[0].question_id, "postgres")
    assert ok.state == "running"


def test_stop_never_becomes_completion(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=NeedsInputOrchestrator())
    res = svc.run("goal")
    stopped = svc.stop(res.run_id)
    assert stopped.state == "cancelled"
    assert stopped.ok and stopped.code == EXIT_OK
    status = svc.status(res.run_id)
    assert not status.ok and status.code == EXIT_BLOCKED
    col = svc.collect(res.run_id)
    assert col.data["result"]["outcome"] == "cancelled"
    assert not col.ok and col.code == EXIT_BLOCKED


def test_stop_persists_stopping_before_signalling_detached_controller(
    tmp_path, monkeypatch
):
    svc = FleetService(_cfg(tmp_path), orchestrator=NeedsInputOrchestrator())
    res = svc.run("goal")
    svc._controller_enabled = True
    svc.store.append_event(res.run_id, "run.detached", "detached")
    observed = []

    def fake_stop(_state_dir, run_id):
        observed.append(svc.store.get_run(run_id).state)
        return True

    monkeypatch.setattr("hca.controller.stop_controller", fake_stop)
    stopped = svc.stop(res.run_id)
    assert stopped.state == "cancelled"
    assert observed == [RunState.STOPPING]


class CancellationFailureOrchestrator(NeedsInputOrchestrator):
    def cancel(self, spec, store):
        raise RuntimeError("owned worker survived escalation")


def test_stop_failure_blocks_instead_of_claiming_cancellation(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=CancellationFailureOrchestrator())
    res = svc.run("goal")
    stopped = svc.stop(res.run_id)
    assert stopped.state == "blocked"
    assert "cancellation incomplete" in stopped.data["run"]["reason"]


class DeadlineOrchestrator(NeedsInputOrchestrator):
    def __init__(self):
        self.cancelled = False

    def cancel(self, spec, store):
        self.cancelled = True
        store.append_event(
            spec.run_id,
            "task.partial_evidence",
            "partial worker output preserved before deadline cleanup",
        )
        return "deadline workers terminated"


def test_deadline_expiry_cancels_exact_workers_before_failed_state(tmp_path):
    orchestrator = DeadlineOrchestrator()
    svc = FleetService(_cfg(tmp_path), orchestrator=orchestrator)
    started = svc.run("bounded goal")
    assert started.state == "needs_input"

    expired = svc.expire(started.run_id)

    assert orchestrator.cancelled is True
    assert expired.state == "failed"
    assert "wall-time deadline exhausted" in expired.data["run"]["reason"]
    events = svc.store.list_events(started.run_id)
    assert any(event["kind"] == "run.controller_budget_exhausted" for event in events)
    assert any(event["kind"] == "task.partial_evidence" for event in events)


def test_deadline_expiry_blocks_when_exact_cancellation_is_incomplete(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=CancellationFailureOrchestrator())
    started = svc.run("bounded goal")

    expired = svc.expire(started.run_id)

    assert expired.state == "blocked"
    assert "deadline cancellation incomplete" in expired.data["run"]["reason"]


def test_terminal_detached_status_never_restarts_controller(tmp_path, monkeypatch):
    svc = FleetService(_cfg(tmp_path), orchestrator=CompletingOrchestrator())
    res = svc.run("goal")
    svc._controller_enabled = True
    svc.store.append_event(res.run_id, "run.detached", "detached")

    def forbidden_launch(*args, **kwargs):
        raise AssertionError("terminal runs must not restart controllers")

    monkeypatch.setattr("hca.controller.launch_controller", forbidden_launch)
    status = svc.status(res.run_id)
    assert status.state == "completed"


def test_collect_manifest_has_sha_and_honest_outcome(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=CompletingOrchestrator())
    res = svc.run("build x")
    col = svc.collect(res.run_id)
    manifest = col.data["result"]
    assert manifest["outcome"] == "success"
    assert len(manifest["manifest_sha256"]) == 64
    assert manifest["state"] == "completed"
    assert manifest["cleanup"] == {"hca_state_preserved": True}
    assert str(tmp_path) not in str(manifest)
    # the manifest must link a real artifact/result, not only prose
    assert manifest["artifacts"]


def test_blocked_run_collect_reports_blockers(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=PreflightOrchestrator())
    res = svc.run("do")
    col = svc.collect(res.run_id)
    assert col.data["result"]["outcome"] == "blocked"
    assert col.code == EXIT_BLOCKED


def test_status_unknown_run(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=PreflightOrchestrator())
    res = svc.status("run-does-not-exist")
    assert res.code == EXIT_INVALID


def test_resume_returns_existing_state(tmp_path):
    store = RunStore(Path(tmp_path) / "hca.sqlite")
    svc = FleetService(
        _cfg(tmp_path), orchestrator=CompletingOrchestrator(), store=store
    )
    a = svc.run("build x")
    resumed = svc.run("", resume=a.run_id)
    assert resumed.run_id == a.run_id
    assert resumed.state == "completed"
