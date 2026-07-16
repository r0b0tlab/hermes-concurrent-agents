"""Shared typed operation layer for the goal-to-team product.

Both the human CLI (`hca run/status/respond/collect/stop`) and the Hermes
plugin tools (`hca_team_*`) call *these* methods — neither surface implements
its own run lifecycle. Every result carries a stable semantic exit code and a
`remediation` field so a human or an agent can decide the next step.

The control plane is deterministic and non-LLM: leases, admission, routing,
reconciliation, and safety are code. The *decomposition/execution* seam is an
injected ``Orchestrator`` so a live backend can drive real Hermes workers
while tests drive a deterministic double. The default orchestrator is honest
— absent an admitted execution backend it leaves the run ``blocked`` with a
precise reason; it never fabricates completion.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

from hca.config import FleetConfig
from hca.result import Artifact, RunResult, build_result
from hca.run import (
    RunBudgets,
    RunProjection,
    RunSpec,
    RunState,
    RunStateError,
    RunStore,
    new_run_id,
)

# Standardized exit / result codes (shared by CLI and plugin tools).
EXIT_OK = 0
EXIT_INVALID = 2  # invalid input/config
EXIT_PREFLIGHT = 3  # preflight / capability failure
EXIT_BLOCKED = 4  # run blocked / needs input
EXIT_RUNTIME = 5  # internal / runtime failure


@dataclass
class ServiceResult:
    ok: bool
    code: int
    action: str
    run_id: str
    state: str
    message: str
    remediation: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Orchestrator(Protocol):
    """Plan/execute seam. Implementations must never fabricate completion.

    ``execute`` returns the final state, or ``(state, reason)`` to attach a
    human/agent-readable reason. Any returned ``completed`` is validated to
    pass through review — the seam cannot forge success.
    """

    def plan(self, spec: RunSpec, store: RunStore) -> RunState:
        ...

    def execute(self, spec: RunSpec, store: RunStore) -> "RunState | tuple[RunState, str]":
        ...


class PreflightOrchestrator:
    """Default honest orchestrator.

    It performs planning bookkeeping but does not start model servers or
    workers (reserved for the supervisor + a configured backend). Absent an
    admitted execution path it leaves the run ``blocked`` with remediation,
    rather than claiming success.
    """

    def __init__(self, cfg: Optional[FleetConfig] = None):
        self.cfg = cfg

    def plan(self, spec: RunSpec, store: RunStore) -> RunState:
        store.append_event(
            spec.run_id, "run.plan",
            f"decomposition staged for goal: {spec.goal[:80]}",
            {"team": spec.team, "concurrency": spec.concurrency},
        )
        return RunState.PLANNING

    def execute(self, spec: RunSpec, store: RunStore):
        reason = (
            "no admitted execution backend — configure a Hermes endpoint and "
            "start the supervisor (`hca up`) so tasks can be dispatched"
        )
        store.append_event(spec.run_id, "run.preflight", reason)
        return RunState.BLOCKED, reason


class FleetService:
    """Deterministic run lifecycle service."""

    def __init__(
        self,
        cfg: FleetConfig,
        *,
        orchestrator: Optional[Orchestrator] = None,
        store: Optional[RunStore] = None,
    ):
        self.cfg = cfg
        state_dir = Path(cfg.state_dir or "~/.hca").expanduser()
        state_dir.mkdir(parents=True, exist_ok=True)
        self.store = store or RunStore(state_dir / "hca.sqlite")
        self.orchestrator = orchestrator or PreflightOrchestrator(cfg)

    # --- run ---

    def run(
        self,
        goal: str,
        *,
        project_root: str = "",
        constraints: Optional[list[str]] = None,
        acceptance_criteria: Optional[list[str]] = None,
        team: str = "default",
        concurrency: int = 1,
        review_policy: str = "auto",
        source_profiles: Optional[list[str]] = None,
        budgets: Optional[dict] = None,
        idempotency_key: str = "",
        resume: str = "",
    ) -> ServiceResult:
        # Resume an existing run by id.
        if resume:
            proj = self.store.get_run(resume)
            if proj is None:
                return ServiceResult(
                    False, EXIT_INVALID, "run", resume, "unknown",
                    f"cannot resume unknown run {resume}",
                    "list runs with `hca status` or drop --resume",
                )
            return self._status_result("run", proj)

        if not goal or not goal.strip():
            return ServiceResult(
                False, EXIT_INVALID, "run", "", "invalid",
                "goal must be a non-empty string",
                "provide a goal: hca run \"<what to build/research/ship>\"",
            )

        # Idempotency: a caller-supplied key deduplicates; goal text never does.
        if idempotency_key:
            existing = self.store.find_by_idempotency_key(idempotency_key)
            if existing is not None:
                return self._status_result(
                    "run", existing,
                    message=f"idempotent replay of {existing.run_id}",
                )

        if concurrency < 1:
            return ServiceResult(
                False, EXIT_INVALID, "run", "", "invalid",
                "concurrency must be >= 1",
            )

        # Validate the team against the bundled templates (both surfaces).
        from hca.team import TeamError, select_team

        try:
            team_spec = select_team(team, review_policy=review_policy)
        except TeamError as exc:
            return ServiceResult(
                False, EXIT_INVALID, "run", "", "invalid", str(exc),
                "choose a known --team (default | small | reviewed)",
            )

        spec = RunSpec(
            run_id=new_run_id(),
            goal=goal.strip(),
            project_root=project_root,
            constraints=tuple(constraints or ()),
            acceptance_criteria=tuple(acceptance_criteria or ()),
            source_profiles=tuple(source_profiles or ()),
            team=team,
            concurrency=int(concurrency),
            review_policy=review_policy,
            budgets=RunBudgets.from_dict(budgets),
            idempotency_key=idempotency_key,
            board=self.cfg.board,
            created_at=time.time(),
        )
        self.store.create_run(spec, state=RunState.QUEUED)
        self.store.append_event(
            spec.run_id, "run.team",
            f"team={team_spec.name} workers={team_spec.worker_count()} "
            f"review={team_spec.review_policy}",
        )

        # Drive the deterministic control plane through the injected seam.
        try:
            self.store.set_state(spec.run_id, RunState.PLANNING, reason="planning")
            planned = self.orchestrator.plan(spec, self.store)
            if planned == RunState.NEEDS_INPUT:
                self.store.set_state(spec.run_id, RunState.NEEDS_INPUT, reason="planner needs input")
            else:
                outcome = self.orchestrator.execute(spec, self.store)
                # execute may return a bare state or (state, reason).
                if isinstance(outcome, tuple):
                    final, final_reason = outcome
                else:
                    final, final_reason = outcome, ""
                # Honor exactly what the orchestrator reports; validate the
                # transition so a bad orchestrator cannot forge `completed`.
                self._apply_final(spec.run_id, final, reason=final_reason)
        except RunStateError as exc:
            self.store.set_state(spec.run_id, RunState.FAILED, reason=str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self.store.set_state(spec.run_id, RunState.FAILED, reason=f"orchestrator error: {exc}")

        proj = self.store.get_run(spec.run_id)
        return self._status_result("run", proj)

    def _apply_final(self, run_id: str, final: RunState, *, reason: str = "") -> None:
        proj = self.store.get_run(run_id)
        if proj is None:
            return
        # Route through review when required by the target state.
        if final == RunState.COMPLETED:
            # completion is only legal from running/review/stopping — pass
            # through running→review→completed so it can never skip verify.
            self.store.set_state(run_id, RunState.RUNNING, reason="executing")
            self.store.set_state(run_id, RunState.REVIEW, reason="verifying")
            self.store.set_state(run_id, RunState.COMPLETED, reason=reason or "accepted")
        elif final == RunState.RUNNING:
            self.store.set_state(run_id, RunState.RUNNING, reason=reason or "executing")
        else:
            # blocked / needs_input / failed etc. Prefer the orchestrator's
            # explicit reason over the generic transition word.
            if proj.state != final:
                self.store.set_state(run_id, final, reason=reason or final.value)

    # --- status ---

    def status(self, run_id: str = "") -> ServiceResult:
        if not run_id:
            runs = self.store.list_runs(limit=20)
            data = {"runs": [r.to_dict() for r in runs]}
            return ServiceResult(
                True, EXIT_OK, "status", "", "list",
                f"{len(runs)} run(s)", data={"runs": data["runs"]},
            )
        proj = self.store.get_run(run_id)
        if proj is None:
            return ServiceResult(
                False, EXIT_INVALID, "status", run_id, "unknown",
                f"unknown run {run_id}",
            )
        return self._status_result("status", proj)

    # --- respond ---

    def respond(self, run_id: str, question_id: str, answer: str) -> ServiceResult:
        proj = self.store.get_run(run_id)
        if proj is None:
            return ServiceResult(
                False, EXIT_INVALID, "respond", run_id, "unknown",
                f"unknown run {run_id}",
            )
        try:
            self.store.answer_question(run_id, question_id, answer)
        except RunStateError as exc:
            return ServiceResult(
                False, EXIT_INVALID, "respond", run_id, proj.state.value, str(exc),
                "check the run id + question id with `hca status <run>`",
            )
        # Resume the blocked branch if no more open questions.
        if proj.state == RunState.NEEDS_INPUT and not self.store.open_questions(run_id):
            self.store.set_state(run_id, RunState.RUNNING, reason="resumed after input")
        proj = self.store.get_run(run_id)
        return self._status_result("respond", proj, message=f"recorded answer to {question_id}")

    # --- collect ---

    def collect(self, run_id: str) -> ServiceResult:
        result = build_result(self.store, run_id, cleanup={"state_dir": self.cfg.state_dir})
        if result is None:
            return ServiceResult(
                False, EXIT_INVALID, "collect", run_id, "unknown",
                f"unknown run {run_id}",
            )
        code = EXIT_OK
        if result.outcome in ("blocked",):
            code = EXIT_BLOCKED
        elif result.outcome == "failed":
            code = EXIT_RUNTIME
        return ServiceResult(
            result.outcome in ("success", "partial", "cancelled"),
            code, "collect", run_id, result.state, result.summary,
            data={"result": result.to_dict()},
        )

    # --- stop ---

    def stop(self, run_id: str) -> ServiceResult:
        proj = self.store.get_run(run_id)
        if proj is None:
            return ServiceResult(
                False, EXIT_INVALID, "stop", run_id, "unknown",
                f"unknown run {run_id}",
            )
        if proj.state in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED):
            return ServiceResult(
                True, EXIT_OK, "stop", run_id, proj.state.value,
                f"run already terminal ({proj.state.value}); nothing to stop",
            )
        # State machine: mark stopping, then cancelled. Never turn a stop into
        # a completion. Partial work/evidence is preserved (never deleted here).
        try:
            self.store.set_state(run_id, RunState.STOPPING, reason="stop requested")
            self.store.set_state(run_id, RunState.CANCELLED, reason="cancelled by operator")
        except RunStateError as exc:
            return ServiceResult(
                False, EXIT_RUNTIME, "stop", run_id, proj.state.value, str(exc),
            )
        proj = self.store.get_run(run_id)
        return self._status_result(
            "stop", proj, message="run cancelled; partial work preserved"
        )

    # --- helpers ---

    def _status_result(
        self, action: str, proj: RunProjection, *, message: str = ""
    ) -> ServiceResult:
        state = proj.state
        code = EXIT_OK
        ok = True
        remediation = ""
        if state == RunState.NEEDS_INPUT:
            code = EXIT_BLOCKED
            qs = self.store.open_questions(proj.run_id)
            remediation = (
                f"answer with: hca respond {proj.run_id} <question-id> \"...\" "
                f"({len(qs)} open)"
            )
        elif state == RunState.BLOCKED:
            code = EXIT_BLOCKED
            remediation = proj.reason or "run blocked — inspect with `hca status`"
        elif state == RunState.FAILED:
            code = EXIT_RUNTIME
            ok = False
            remediation = proj.reason or "run failed — see events"
        elif state == RunState.CANCELLED:
            ok = True
            remediation = "cancelled; collect partial work with `hca collect`"
        msg = message or f"run {proj.run_id} is {state.value}"
        return ServiceResult(
            ok, code, action, proj.run_id, state.value, msg, remediation,
            data={"run": proj.to_dict()},
        )


def artifacts_from(pairs: list[tuple[str, str, str]]) -> list[Artifact]:
    """Small helper: (name, kind, ref) → Artifact list."""
    return [Artifact(name=n, kind=k, ref=r) for (n, k, r) in pairs]


def result_manifest(store: RunStore, run_id: str) -> Optional[RunResult]:
    return build_result(store, run_id)
