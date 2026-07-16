"""Evidence-backed run completion.

The single rule this module enforces: a run is ``completed`` **only** when
terminal upstream Kanban evidence proves it. Completion is *derived* from
observed facts — task terminal status, the integer ``current_run_id`` the
task carried while running, the worker PID that was bound, and the
result/artifact the worker produced — never from a bare enum an orchestrator
(or a test double) hands back. This is the anti-forgery gate the controller
required: an injected object cannot manufacture success without first
creating a real done task, binding a real/fake-process PID, and producing a
real result/artifact in the Kanban DB.

``ExecutionEvidence`` is assembled by the orchestrator *across* the run
lifecycle: ``run_id``/``pid`` are captured at claim/spawn (when the task is
running and carries them), while ``terminal_status``/``result``/``artifacts``
are read from the terminal task. That ordering is deliberate — upstream nulls
``current_run_id`` and ``worker_pid`` on completion, so the proof-of-execution
fields must be captured while the worker is live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from hca.result import Artifact
from hca.run import RunSpec, RunState

# Upstream Kanban task statuses that mean "no more work will happen here".
TERMINAL_TASK_STATUSES: frozenset[str] = frozenset(
    {"done", "blocked", "failed", "crashed", "timed_out", "archived"}
)
# Terminal statuses that indicate the task did *not* succeed.
FATAL_TASK_STATUSES: frozenset[str] = frozenset(
    {"failed", "crashed", "timed_out"}
)


@dataclass
class TaskEvidence:
    """Observed facts about one upstream Kanban task in a run.

    ``run_id`` and ``pid`` are the proof-of-execution fields captured while
    the worker was live; ``terminal_status``/``result``/``artifacts`` are the
    terminal outcome. A ``done`` task that never carried an integer run id or
    bound a pid is *not* accepted as real completion.
    """

    task_id: str
    assignee: str = ""
    terminal_status: str = ""
    run_id: Optional[int] = None
    pid: Optional[int] = None
    result: str = ""
    artifacts: list[Artifact] = field(default_factory=list)
    is_review: bool = False
    reviewed_by: str = ""
    review_verdict: str = ""  # accept | reject | malformed | ""
    block_kind: str = ""
    block_reason: str = ""
    kind: str = ""  # work | rework | review | final | verification
    is_root: bool = False

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "assignee": self.assignee,
            "terminal_status": self.terminal_status,
            "run_id": self.run_id,
            "pid": self.pid,
            "result": self.result,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "is_review": self.is_review,
            "reviewed_by": self.reviewed_by,
            "review_verdict": self.review_verdict,
            "block_kind": self.block_kind,
            "block_reason": self.block_reason,
            "kind": self.kind,
            "is_root": self.is_root,
        }


@dataclass
class ExecutionEvidence:
    """The complete observed outcome of a run's upstream tasks."""

    root_task_id: str = ""
    tasks: list[TaskEvidence] = field(default_factory=list)
    reason: str = ""

    def root(self) -> Optional[TaskEvidence]:
        for t in self.tasks:
            if t.is_root or t.task_id == self.root_task_id:
                return t
        return None

    def all_artifacts(self) -> list[Artifact]:
        out: list[Artifact] = []
        for t in self.tasks:
            out.extend(t.artifacts)
        return out

    def to_dict(self) -> dict:
        return {
            "root_task_id": self.root_task_id,
            "tasks": [t.to_dict() for t in self.tasks],
            "reason": self.reason,
        }


def review_required(spec: RunSpec) -> bool:
    """Whether an independent reviewer must accept before completion.

    ``always`` always requires review; ``never`` never does; ``auto`` requires
    it when the run modifies or publishes work — proxied here by a configured
    project root or explicit acceptance criteria. A pure research/read goal
    with neither can complete without a reviewer.
    """
    policy = (spec.review_policy or "auto").lower()
    if policy == "always":
        return True
    if policy == "never":
        return False
    return bool(spec.project_root) or bool(spec.acceptance_criteria)


def derive_final_state(
    spec: RunSpec, ev: ExecutionEvidence
) -> tuple[RunState, str]:
    """Map observed upstream evidence to a terminal/holding run state.

    Never returns ``COMPLETED`` unless every required task is terminally
    ``done``, each done work task carried an integer run id *and* bound a pid,
    a result or artifact was produced, and (when required) an independent
    reviewer accepted the work. Anything short of that holds the run in
    ``blocked``/``failed`` with a precise reason.
    """
    if not ev.tasks:
        return RunState.BLOCKED, (
            ev.reason or "no upstream Kanban tasks were created for this run"
        )

    fatal = [t for t in ev.tasks if t.terminal_status in FATAL_TASK_STATUSES]
    if fatal:
        t = fatal[0]
        return RunState.FAILED, (
            f"{len(fatal)} task(s) did not succeed; {t.task_id} ended "
            f"{t.terminal_status}"
        )

    needs_input = [
        t for t in ev.tasks
        if t.terminal_status == "blocked" and t.block_kind == "needs_input"
    ]
    if needs_input:
        t = needs_input[0]
        return RunState.NEEDS_INPUT, (
            t.block_reason
            or f"task {t.task_id} needs operator input before it can continue"
        )

    blocked = [
        t
        for t in ev.tasks
        if t.terminal_status == "blocked"
        and not (t.kind == "gate" and t.block_kind == "hca_review_gate")
    ]
    if blocked:
        t = blocked[0]
        return RunState.BLOCKED, (
            ev.reason or f"{len(blocked)} task(s) blocked; {t.task_id} needs attention"
        )

    non_terminal = [
        t for t in ev.tasks if t.terminal_status not in TERMINAL_TASK_STATUSES
    ]
    if non_terminal:
        ids = ", ".join(t.task_id for t in non_terminal[:5])
        if ev.reason:
            # A bounded synchronous execution attempt exhausted its observation
            # window. This requires attention/supervision rather than a false
            # success, so hold it blocked with the explicit budget reason.
            return RunState.BLOCKED, ev.reason
        # A status/collect projection can observe healthy work in flight. That
        # is RUNNING, not BLOCKED; otherwise merely checking a detached run would
        # corrupt its projection into a terminal-looking failure state.
        return RunState.RUNNING, (
            f"{len(non_terminal)} task(s) still in flight ({ids}) — not terminal"
        )

    # Everything terminal and nothing fatal/blocked ⇒ all done/archived.
    work = [t for t in ev.tasks if not t.is_review]
    done_work = [t for t in work if t.terminal_status == "done"]
    if not done_work:
        return RunState.BLOCKED, "no work task reached 'done' — nothing to report"

    # Proof-of-execution: a done work task must have carried an integer run id
    # and bound a pid while running. A bare 'done' with neither is exactly the
    # forgery this gate rejects.
    unproven = [t for t in done_work if t.run_id is None or t.pid is None]
    if unproven:
        t = unproven[0]
        return RunState.BLOCKED, (
            f"task {t.task_id} is 'done' but no run id + worker pid were "
            "observed for it — refusing to report unverified completion"
        )

    # A success must point at something: a result string or a surviving artifact.
    if not any(t.result or t.artifacts for t in done_work):
        return RunState.BLOCKED, (
            "no result or artifact was produced by any task — refusing to "
            "report empty success"
        )

    if review_required(spec):
        reviews_done = [
            t for t in ev.tasks if t.is_review and t.terminal_status == "done"
        ]
        if not reviews_done:
            return RunState.BLOCKED, (
                "review required for this run but no independent reviewer "
                "accepted the work"
            )
        latest_review = reviews_done[-1]
        if latest_review.run_id is None or latest_review.pid is None:
            return RunState.BLOCKED, (
                f"review {latest_review.task_id} is 'done' but no run id + worker "
                "pid were observed — refusing unexecuted review acceptance"
            )
        if not (latest_review.result or latest_review.artifacts):
            return RunState.BLOCKED, (
                f"review {latest_review.task_id} produced no verdict artifact/result"
            )
        if latest_review.review_verdict == "reject":
            return RunState.BLOCKED, (
                f"review {latest_review.task_id} rejected the work; bounded "
                "rework is required before completion"
            )
        if latest_review.review_verdict != "accept":
            return RunState.BLOCKED, (
                "review task completed without a structured HCA_REVIEW: ACCEPT "
                "verdict — refusing ambiguous acceptance"
            )
        implementers = {
            t.assignee
            for t in work
            if t.assignee and (t.kind in {"work", "rework"} or (not t.kind and not t.is_root))
        }
        if latest_review.reviewed_by in implementers or not latest_review.reviewed_by:
            return RunState.BLOCKED, (
                "reviewer is not independent of the implementer — self-review "
                "cannot accept a run"
            )

    return RunState.COMPLETED, (
        "all required tasks done with observed run id + worker pid and "
        "result/artifact evidence"
    )
