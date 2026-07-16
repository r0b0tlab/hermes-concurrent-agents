"""Deterministic run-result manifest.

The result is aggregated purely from immutable run/task/question/event rows.
Optional prose must cite manifest entries; it can never introduce a success
claim the manifest does not support. In particular, ``completed`` is the only
outcome that means success, and it requires the run projection to actually be
in the ``completed`` terminal state — cancelled/blocked/failed runs can be
collected but are reported honestly, never as success.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from hca.run import RunProjection, RunSpec, RunState, RunStore


@dataclass
class Artifact:
    name: str
    kind: str  # "kanban" | "worktree" | "verification" | "comment"
    ref: str
    task_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunResult:
    run_id: str
    goal: str
    state: str
    outcome: str  # "success" | "partial" | "cancelled" | "blocked" | "failed"
    summary: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    unresolved_blockers: list[str] = field(default_factory=list)
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    cleanup: dict[str, Any] = field(default_factory=dict)
    manifest_sha256: str = ""
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_OUTCOME_BY_STATE = {
    RunState.COMPLETED: "success",
    RunState.CANCELLED: "cancelled",
    RunState.BLOCKED: "blocked",
    RunState.FAILED: "failed",
}


def _outcome_for(state: RunState) -> str:
    if state in _OUTCOME_BY_STATE:
        return _OUTCOME_BY_STATE[state]
    # non-terminal collected view
    return "partial"


def build_result(
    store: RunStore,
    run_id: str,
    *,
    artifacts: Optional[list[Artifact]] = None,
    cleanup: Optional[dict[str, Any]] = None,
) -> Optional[RunResult]:
    proj: Optional[RunProjection] = store.get_run(run_id)
    if proj is None:
        return None
    spec: Optional[RunSpec] = store.get_spec(run_id)
    events = store.list_events(run_id)
    open_qs = store.open_questions(run_id)

    outcome = _outcome_for(proj.state)
    blockers: list[str] = []
    if proj.reason and proj.state in (RunState.BLOCKED, RunState.FAILED, RunState.CANCELLED):
        blockers.append(proj.reason)
    for q in open_qs:
        blockers.append(f"awaiting input: {q.prompt} (question {q.question_id})")

    # Evidence is the immutable event trail (state transitions + lifecycle).
    evidence = [
        {"ts": e["ts"], "kind": e["kind"], "message": e["message"]}
        for e in events
        if e["kind"] in ("run.state", "run.created", "run.needs_input", "run.responded",
                         "run.review", "run.completed", "run.blocked", "task.complete")
    ]

    art_dicts = [a.to_dict() for a in (artifacts or [])]

    if outcome == "success":
        summary = f"completed: {proj.goal[:120]}"
    elif outcome == "cancelled":
        summary = f"cancelled by operator: {proj.goal[:100]} (partial work preserved)"
    elif outcome == "blocked":
        summary = f"blocked: {proj.reason or 'see unresolved_blockers'}"
    elif outcome == "failed":
        summary = f"failed: {proj.reason or 'internal error'}"
    else:
        summary = f"in progress ({proj.state.value}): {proj.goal[:100]}"

    manifest_body = {
        "run_id": run_id,
        "goal": proj.goal,
        "state": proj.state.value,
        "outcome": outcome,
        "spec": spec.to_dict() if spec else {},
        "evidence": evidence,
        "artifacts": art_dicts,
        "unresolved_blockers": blockers,
        "open_questions": [q.to_dict() for q in open_qs],
    }
    sha = hashlib.sha256(
        json.dumps(manifest_body, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return RunResult(
        run_id=run_id,
        goal=proj.goal,
        state=proj.state.value,
        outcome=outcome,
        summary=summary,
        evidence=evidence,
        artifacts=art_dicts,
        unresolved_blockers=blockers,
        open_questions=[q.to_dict() for q in open_qs],
        cleanup=cleanup or {},
        manifest_sha256=sha,
    )
