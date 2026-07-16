"""FleetService lifecycle: run/status/respond/collect/stop + exit codes.

The execution seam is a deterministic test double — it drives the state
machine without an LLM. The default PreflightOrchestrator is tested to leave
the run honestly ``blocked`` (never a fabricated ``completed``).
"""

from __future__ import annotations

from pathlib import Path

from hca.config import load_fleet_config
from hca.run import RunState, RunStore
from hca.service import (
    EXIT_BLOCKED,
    EXIT_INVALID,
    EXIT_OK,
    FleetService,
)


def _cfg(tmp_path: Path):
    return load_fleet_config(model="m", state_dir=str(tmp_path))


class CompletingOrchestrator:
    """Deterministic double: plans then reports completion."""

    def plan(self, spec, store):
        store.append_event(spec.run_id, "run.plan", "1 task")
        return RunState.PLANNING

    def execute(self, spec, store):
        return RunState.COMPLETED


class NeedsInputOrchestrator:
    def plan(self, spec, store):
        store.add_question(spec.run_id, "which database?")
        return RunState.NEEDS_INPUT

    def execute(self, spec, store):  # pragma: no cover - not reached
        return RunState.COMPLETED


def test_default_orchestrator_is_honest_blocked(tmp_path):
    svc = FleetService(_cfg(tmp_path))
    res = svc.run("do something")
    assert res.state == "blocked"  # NOT completed — no backend admitted
    assert res.code == EXIT_BLOCKED
    assert res.remediation


def test_completing_orchestrator_reaches_completed_via_review(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=CompletingOrchestrator())
    res = svc.run("build x")
    assert res.state == "completed"
    assert res.code == EXIT_OK
    # the event trail must show it passed through review before completion
    events = [e["message"] for e in svc.store.list_events(res.run_id)]
    assert any("REVIEW" in m or "verifying" in m for m in events)


def test_empty_goal_is_invalid(tmp_path):
    svc = FleetService(_cfg(tmp_path))
    res = svc.run("   ")
    assert res.code == EXIT_INVALID
    assert not res.ok


def test_idempotency_key_dedups(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=CompletingOrchestrator())
    a = svc.run("build x", idempotency_key="k1")
    b = svc.run("build x", idempotency_key="k1")
    assert a.run_id == b.run_id  # same run, not a new one
    # a different key → different run
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
    # wrong question id fails
    bad = svc.respond(res.run_id, "q-nope", "answer")
    assert not bad.ok
    # correct answer resumes to running
    ok = svc.respond(res.run_id, qs[0].question_id, "postgres")
    assert ok.state == "running"


def test_stop_never_becomes_completion(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=NeedsInputOrchestrator())
    res = svc.run("goal")
    stopped = svc.stop(res.run_id)
    assert stopped.state == "cancelled"
    # collect reports cancelled, never success
    col = svc.collect(res.run_id)
    assert col.data["result"]["outcome"] == "cancelled"


def test_collect_manifest_has_sha_and_honest_outcome(tmp_path):
    svc = FleetService(_cfg(tmp_path), orchestrator=CompletingOrchestrator())
    res = svc.run("build x")
    col = svc.collect(res.run_id)
    manifest = col.data["result"]
    assert manifest["outcome"] == "success"
    assert len(manifest["manifest_sha256"]) == 64
    assert manifest["state"] == "completed"


def test_blocked_run_collect_reports_blockers(tmp_path):
    svc = FleetService(_cfg(tmp_path))  # default → blocked
    res = svc.run("do")
    col = svc.collect(res.run_id)
    assert col.data["result"]["outcome"] == "blocked"
    assert col.code == EXIT_BLOCKED


def test_status_unknown_run(tmp_path):
    svc = FleetService(_cfg(tmp_path))
    res = svc.status("run-does-not-exist")
    assert res.code == EXIT_INVALID


def test_resume_returns_existing_state(tmp_path):
    store = RunStore(Path(tmp_path) / "hca.sqlite")
    svc = FleetService(_cfg(tmp_path), orchestrator=CompletingOrchestrator(), store=store)
    a = svc.run("build x")
    resumed = svc.run("", resume=a.run_id)
    assert resumed.run_id == a.run_id
    assert resumed.state == "completed"
