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
from hca.evidence import ExecutionEvidence, derive_final_state
from hca.result import Artifact, RunResult, build_result
from hca.run import (
    RunBudgets,
    RunProjection,
    RunSpec,
    RunState,
    RunStateError,
    RunStore,
    can_transition,
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

    ``plan`` returns the post-planning run state (``PLANNING`` to proceed,
    ``NEEDS_INPUT`` to gate on a question, ``BLOCKED`` when the planner graph
    is invalid). ``execute`` returns :class:`ExecutionEvidence` describing the
    *observed* upstream tasks — the terminal state is then *derived* from that
    evidence by :func:`hca.evidence.derive_final_state`, so an implementation
    (or a test double) cannot hand back a bare ``completed`` enum. To report
    success it must produce real terminal tasks carrying a run id, a bound pid,
    and a result/artifact.
    """

    def plan(self, spec: RunSpec, store: RunStore) -> RunState:
        ...

    def execute(self, spec: RunSpec, store: RunStore) -> ExecutionEvidence:
        ...


class PreflightOrchestrator:
    """Default honest orchestrator when no real Kanban backend is available.

    It performs planning bookkeeping but does not start model servers or
    workers. Absent an admitted execution path it returns *empty* evidence,
    which derives to ``blocked`` with remediation — it never claims success.
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

    def execute(self, spec: RunSpec, store: RunStore) -> ExecutionEvidence:
        reason = (
            "no admitted execution backend — a configured Hermes installation "
            "with an importable kanban_db and a board is required so `hca run` "
            "can submit durable work; start the supervisor (`hca up`)"
        )
        store.append_event(spec.run_id, "run.preflight", reason)
        return ExecutionEvidence(reason=reason)


def _default_orchestrator(cfg: FleetConfig, store: RunStore):
    """Pick the real Kanban orchestrator when the prerequisites are valid.

    The controller's rule: `hca run` must submit durable real work when a
    valid configured Hermes install is present, and must *not* default-block
    in that case. We only fall back to the honest preflight block when Hermes
    is not importable or no board is configured.
    """
    from hca.hermes_compat import HermesCompatError, import_kanban_db

    if not cfg.board:
        return PreflightOrchestrator(cfg)
    try:
        import_kanban_db()
    except HermesCompatError:
        return PreflightOrchestrator(cfg)
    from hca.kanban_orchestrator import KanbanOrchestrator

    return KanbanOrchestrator(cfg, board=cfg.board)


class FleetService:
    """Deterministic run lifecycle service."""

    def __init__(
        self,
        cfg: FleetConfig,
        *,
        orchestrator: Optional[Orchestrator] = None,
        store: Optional[RunStore] = None,
        launch_controller: bool = True,
    ):
        self.cfg = cfg
        self._controller_enabled = bool(launch_controller and orchestrator is None)
        state_dir = Path(cfg.state_dir or "~/.hca").expanduser()
        state_dir.mkdir(parents=True, exist_ok=True)
        self.store = store or RunStore(state_dir / "hca.sqlite")
        self.orchestrator = orchestrator or _default_orchestrator(cfg, self.store)

    # --- run ---

    def run(
        self,
        goal: str,
        *,
        project_root: str = "",
        constraints: Optional[list[str]] = None,
        acceptance_criteria: Optional[list[str]] = None,
        independent_criteria: bool = False,
        team: str = "default",
        concurrency: int = 1,
        review_policy: str = "auto",
        source_profiles: Optional[list[str]] = None,
        budgets: Optional[dict] = None,
        idempotency_key: str = "",
        resume: str = "",
        detach: bool = False,
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

        if independent_criteria and len(acceptance_criteria or ()) < 2:
            return ServiceResult(
                False,
                EXIT_INVALID,
                "run",
                "",
                "invalid",
                "independent_criteria requires at least two acceptance criteria",
                "provide two or more --acceptance values, or omit --independent-criteria",
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

        raw_budgets = dict(budgets or {})
        known_budgets = set(RunBudgets().to_dict())
        unknown_budgets = sorted(set(raw_budgets) - known_budgets)
        if unknown_budgets:
            return ServiceResult(
                False,
                EXIT_INVALID,
                "run",
                "",
                "invalid",
                f"unknown budget key(s): {', '.join(unknown_budgets)}",
                f"known budgets: {', '.join(sorted(known_budgets))}",
            )
        try:
            parsed_budgets = RunBudgets.from_dict(raw_budgets)
            if any(int(v) < 0 for v in parsed_budgets.to_dict().values()):
                raise ValueError("budgets must be non-negative")
        except (TypeError, ValueError) as exc:
            return ServiceResult(
                False, EXIT_INVALID, "run", "", "invalid", f"invalid budgets: {exc}"
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
        concurrency_limit = max(
            1,
            min(
                int(team_spec.max_workers),
                int(parsed_budgets.max_workers),
                int(self.cfg.capacity.max_wave_size),
            ),
        )
        if concurrency > concurrency_limit:
            return ServiceResult(
                False,
                EXIT_INVALID,
                "run",
                "",
                "invalid",
                f"concurrency {concurrency} exceeds admitted team/run limit {concurrency_limit}",
                "lower --concurrency or select/configure a larger bounded team",
            )

        spec = RunSpec(
            run_id=new_run_id(),
            goal=goal.strip(),
            project_root=project_root,
            constraints=tuple(constraints or ()),
            acceptance_criteria=tuple(acceptance_criteria or ()),
            independent_criteria=bool(independent_criteria),
            source_profiles=tuple(source_profiles or ()),
            team=team,
            concurrency=int(concurrency),
            review_policy=review_policy,
            budgets=parsed_budgets,
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
                self.store.set_state(
                    spec.run_id, RunState.NEEDS_INPUT, reason="planner needs input"
                )
            elif planned == RunState.BLOCKED:
                proj_now = self.store.get_run(spec.run_id)
                if proj_now is not None and proj_now.state != RunState.BLOCKED:
                    self.store.set_state(
                        spec.run_id,
                        RunState.BLOCKED,
                        reason=proj_now.reason or "planner produced no dispatchable graph",
                    )
            else:
                if detach:
                    start = getattr(self.orchestrator, "start", None)
                    if not callable(start):
                        self.store.set_state(
                            spec.run_id,
                            RunState.BLOCKED,
                            reason=(
                                "selected execution backend cannot detach safely; "
                                "run without --detach or configure the Kanban backend"
                            ),
                        )
                    else:
                        self.store.set_state(
                            spec.run_id,
                            RunState.RUNNING,
                            reason="detached after initial admitted dispatch wave",
                        )
                        start(spec, self.store)
                        self.store.append_event(
                            spec.run_id,
                            "run.detached",
                            "detached controller requested",
                        )
                        if self._controller_enabled:
                            self._ensure_controller(spec.run_id, fail_closed=True)
                else:
                    evidence = self.orchestrator.execute(spec, self.store)
                    self._reconcile_from_evidence(spec, evidence)
        except RunStateError as exc:
            self.store.set_state(spec.run_id, RunState.FAILED, reason=str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self.store.set_state(
                spec.run_id, RunState.FAILED, reason=f"orchestrator error: {exc}"
            )

        proj = self.store.get_run(spec.run_id)
        return self._status_result("run", proj)

    def _reconcile_from_evidence(
        self, spec: RunSpec, evidence: ExecutionEvidence
    ) -> None:
        """Derive the terminal run state from *observed upstream evidence*.

        Completion is never taken on the orchestrator's word: it is derived by
        :func:`derive_final_state`, which requires real terminal tasks with a
        run id, a bound pid, and a result/artifact. The transitions here only
        reflect what the evidence proves.
        """
        final, reason = derive_final_state(spec, evidence)
        self.store.append_event(
            spec.run_id, "run.evidence", reason,
            {"final": final.value, "evidence": evidence.to_dict()},
        )
        # Reflect that execution was attempted before recording the outcome.
        proj = self.store.get_run(spec.run_id)
        if proj is not None and proj.state == RunState.PLANNING:
            self.store.set_state(spec.run_id, RunState.RUNNING, reason="executing on kanban")

        if final == RunState.COMPLETED:
            # If an independent reviewer participated, pass through review so
            # the trail shows verification; otherwise complete directly. Either
            # way `final` was already gated on evidence.
            if any(t.is_review for t in evidence.tasks):
                self._safe_set(spec.run_id, RunState.REVIEW, "verifying")
            self._safe_set(spec.run_id, RunState.COMPLETED, reason)
        elif final == RunState.FAILED:
            self._safe_set(spec.run_id, RunState.FAILED, reason)
        elif final == RunState.RUNNING:
            self._safe_set(spec.run_id, RunState.RUNNING, reason)
        elif final == RunState.NEEDS_INPUT:
            self._safe_set(spec.run_id, RunState.NEEDS_INPUT, reason)
        else:
            self._safe_set(spec.run_id, RunState.BLOCKED, reason)

    def _safe_set(self, run_id: str, target: RunState, reason: str) -> None:
        """Apply a transition only if the state machine permits it."""
        proj = self.store.get_run(run_id)
        if proj is None or proj.state == target:
            return
        if can_transition(proj.state, target):
            self.store.set_state(run_id, target, reason=reason)

    def reconcile(self, run_id: str, *, dispatch: bool = True) -> ServiceResult:
        """Run one durable controller iteration for a non-terminal run."""
        proj = self.store.get_run(run_id)
        if proj is None:
            return ServiceResult(
                False, EXIT_INVALID, "reconcile", run_id, "unknown",
                f"unknown run {run_id}",
            )
        if proj.state in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED):
            return self._status_result("reconcile", proj)
        spec = self.store.get_spec(run_id)
        tick = getattr(self.orchestrator, "tick", None)
        if spec is None or not callable(tick):
            proj = self._reconcile_projection(run_id) or proj
            return self._status_result("reconcile", proj)
        try:
            evidence = tick(spec, self.store, dispatch=dispatch)
            if not isinstance(evidence, ExecutionEvidence):
                raise TypeError("controller tick returned invalid evidence")
            self._reconcile_from_evidence(spec, evidence)
        except Exception as exc:
            self.store.append_event(
                run_id,
                "run.controller_error",
                "controller iteration failed",
                {"error_type": type(exc).__name__},
            )
            proj = self.store.get_run(run_id) or proj
            return ServiceResult(
                False,
                EXIT_RUNTIME,
                "reconcile",
                run_id,
                proj.state.value,
                f"controller iteration failed: {type(exc).__name__}",
                "inspect controller logs and retry status/reconcile",
            )
        refreshed = self.store.get_run(run_id)
        return self._status_result("reconcile", refreshed or proj)

    def _reconcile_projection(self, run_id: str) -> Optional[RunProjection]:
        """Refresh a non-terminal run from live upstream Kanban truth.

        Status/collect call this so the HCA projection reflects the board
        rather than a stale enum. Terminal success/failure/cancel are never
        reopened; a ``blocked`` run may advance if the board later satisfies
        the evidence gate.
        """
        proj = self.store.get_run(run_id)
        if proj is None or proj.state in (
            RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED
        ):
            return proj
        project = getattr(self.orchestrator, "project", None)
        tick = getattr(self.orchestrator, "tick", None)
        spec = self.store.get_spec(run_id)
        if spec is None or (not callable(project) and not callable(tick)):
            return proj
        try:
            if callable(tick):
                evidence = tick(spec, self.store, dispatch=False)
            else:
                sync_questions = getattr(self.orchestrator, "sync_questions", None)
                if callable(sync_questions):
                    sync_questions(spec, self.store)
                if not callable(project):
                    return proj
                evidence = project(spec, self.store)
        except Exception:  # pragma: no cover - defensive; never fail status
            return proj
        if not isinstance(evidence, ExecutionEvidence):
            return proj
        self._reconcile_from_evidence(spec, evidence)
        return self.store.get_run(run_id)

    def _collected_artifacts(self, run_id: str) -> list[Artifact]:
        """Artifacts observed on the run's terminal Kanban tasks.

        Read from the most recent evidence event so ``collect`` links every
        claimed output to a real Kanban task/attachment rather than prose.
        """
        latest: dict[str, Any] = {}
        for e in self.store.list_events(run_id):
            if e["kind"] == "run.evidence":
                latest = e.get("data", {}) or {}
        ev = latest.get("evidence", {}) if isinstance(latest, dict) else {}
        out: list[Artifact] = []
        for t in ev.get("tasks", []) if isinstance(ev, dict) else []:
            for a in t.get("artifacts", []) or []:
                out.append(
                    Artifact(
                        name=a.get("name", ""),
                        kind=a.get("kind", "kanban"),
                        ref=a.get("ref", ""),
                        task_id=a.get("task_id", t.get("task_id", "")),
                    )
                )
            # A non-empty upstream result is itself evidence of a real output.
            if t.get("result"):
                out.append(
                    Artifact(
                        name=f"result:{t.get('task_id','')}",
                        kind="result",
                        ref=t.get("result", "")[:200],
                        task_id=t.get("task_id", ""),
                    )
                )
        return out

    def _detached_requested(self, run_id: str) -> bool:
        return any(
            event["kind"] == "run.detached"
            for event in self.store.list_events(run_id)
        )

    def _ensure_controller(self, run_id: str, *, fail_closed: bool = False) -> bool:
        if not self._controller_enabled or not self._detached_requested(run_id):
            return False
        proj = self.store.get_run(run_id)
        if proj is None or proj.state in (
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
            RunState.NEEDS_INPUT,
            RunState.STOPPING,
        ):
            return False
        try:
            from hca.controller import controller_alive, launch_controller

            was_alive = controller_alive(self.cfg.state_dir, run_id)
            pid = launch_controller(self.cfg, run_id)
            if not was_alive:
                self.store.append_event(
                    run_id,
                    "run.controller_ready",
                    "detached controller is live",
                    {"pid": pid},
                )
            return True
        except Exception as exc:
            self.store.append_event(
                run_id,
                "run.controller_launch_failed",
                "detached controller failed to start",
                {"error_type": type(exc).__name__},
            )
            if fail_closed:
                spec = self.store.get_spec(run_id)
                cancel = getattr(self.orchestrator, "cancel", None)
                if spec is not None and callable(cancel):
                    try:
                        cancel(spec, self.store)
                    except Exception:
                        pass
                self._safe_set(
                    run_id,
                    RunState.BLOCKED,
                    "detached controller failed to start; admitted workers were stopped",
                )
            return False

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
        # Reconcile the projection from live upstream Kanban truth.
        proj = self._reconcile_projection(run_id) or proj
        self._ensure_controller(run_id)
        proj = self.store.get_run(run_id) or proj
        return self._status_result("status", proj)

    # --- respond ---

    def respond(self, run_id: str, question_id: str, answer: str) -> ServiceResult:
        proj = self.store.get_run(run_id)
        if proj is None:
            return ServiceResult(
                False, EXIT_INVALID, "respond", run_id, "unknown",
                f"unknown run {run_id}",
            )
        if not answer or not answer.strip():
            return ServiceResult(
                False,
                EXIT_INVALID,
                "respond",
                run_id,
                proj.state.value,
                "answer must be non-empty",
            )
        question = self.store.get_question(question_id)
        if question is None or question.run_id != run_id or question.status != "open":
            detail = (
                f"unknown question {question_id}"
                if question is None
                else (
                    f"question {question_id} belongs to run {question.run_id}, not {run_id}"
                    if question.run_id != run_id
                    else f"question {question_id} already answered"
                )
            )
            return ServiceResult(
                False, EXIT_INVALID, "respond", run_id, proj.state.value, detail,
                "check the run id + question id with `hca status <run>`",
            )

        spec = self.store.get_spec(run_id)
        backend_respond = getattr(self.orchestrator, "respond", None)
        if question.task_id and callable(backend_respond) and spec is not None:
            try:
                released = backend_respond(
                    spec, question.task_id, answer.strip()
                )
                if not released:
                    return ServiceResult(
                        False,
                        EXIT_BLOCKED,
                        "respond",
                        run_id,
                        proj.state.value,
                        f"task gate for {question_id} is no longer unblockable",
                        "refresh status; do not replay a stale answer",
                    )
            except (ValueError, RuntimeError) as exc:
                return ServiceResult(
                    False,
                    EXIT_BLOCKED,
                    "respond",
                    run_id,
                    proj.state.value,
                    f"could not release matching task gate: {exc}",
                    "refresh status and verify the task still belongs to this run",
                )

        try:
            self.store.answer_question(run_id, question_id, answer.strip())
        except RunStateError as exc:
            return ServiceResult(
                False, EXIT_INVALID, "respond", run_id, proj.state.value, str(exc),
                "check the run id + question id with `hca status <run>`",
            )
        # Resume the blocked branch if no more open questions.
        if proj.state == RunState.NEEDS_INPUT and not self.store.open_questions(run_id):
            self.store.set_state(run_id, RunState.RUNNING, reason="resumed after input")
        reconciled = self.reconcile(run_id, dispatch=True)
        if reconciled.code == EXIT_RUNTIME:
            return reconciled
        self._ensure_controller(run_id, fail_closed=True)
        proj = self.store.get_run(run_id)
        return self._status_result("respond", proj, message=f"recorded answer to {question_id}")

    # --- collect ---

    def collect(self, run_id: str) -> ServiceResult:
        # Reconcile from upstream so a run that finished on the board is
        # collected as such, then aggregate the immutable manifest.
        if self.store.get_run(run_id) is not None:
            self._reconcile_projection(run_id)
        artifacts = self._collected_artifacts(run_id)
        result = build_result(
            self.store, run_id, artifacts=artifacts,
            cleanup={"state_dir": self.cfg.state_dir},
        )
        if result is None:
            return ServiceResult(
                False, EXIT_INVALID, "collect", run_id, "unknown",
                f"unknown run {run_id}",
            )
        code = EXIT_OK
        if result.outcome in ("blocked", "cancelled"):
            code = EXIT_BLOCKED
        elif result.outcome == "failed":
            code = EXIT_RUNTIME
        return ServiceResult(
            result.outcome in ("success", "partial"),
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
        # State machine: mark stopping, run the real cancellation seam
        # (process-group TERM/KILL + Kanban reconcile), then cancelled. Never
        # turn a stop into a completion. Partial work/evidence is preserved.
        reason = "cancelled by operator"
        try:
            # Persist STOPPING first. Concurrent status/respond calls now see a
            # dispatch barrier and cannot relaunch a controller during stop.
            self.store.set_state(run_id, RunState.STOPPING, reason="stop requested")
            if self._controller_enabled and self._detached_requested(run_id):
                try:
                    from hca.controller import stop_controller

                    stop_controller(self.cfg.state_dir, run_id)
                except Exception as exc:
                    self.store.append_event(
                        run_id,
                        "controller.stop_error",
                        "controller stop signal failed; persisted STOPPING remains authoritative",
                        {"error": str(exc)[:500]},
                    )
            cancel = getattr(self.orchestrator, "cancel", None)
            spec = self.store.get_spec(run_id)
            if callable(cancel) and spec is not None:
                try:
                    cancel_reason = cancel(spec, self.store)
                    if isinstance(cancel_reason, str) and cancel_reason:
                        reason = cancel_reason
                except Exception as exc:
                    blocked_reason = f"cancellation incomplete: {exc}"
                    self.store.append_event(
                        run_id,
                        "run.cancel_incomplete",
                        blocked_reason,
                    )
                    self.store.set_state(
                        run_id, RunState.BLOCKED, reason=blocked_reason
                    )
                    blocked = self.store.get_run(run_id)
                    if blocked is None:  # pragma: no cover - invariant guard
                        raise RunStateError(f"run {run_id} disappeared during stop")
                    return self._status_result(
                        "stop",
                        blocked,
                        message="run blocked because exact cancellation did not finish",
                    )
            self.store.set_state(run_id, RunState.CANCELLED, reason=reason)
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
            # Stopping a run successfully is a successful *stop operation*.
            # Observing that same cancellation through run/status must not emit
            # exit 0, which automation would mistake for a completed run.
            ok = action == "stop"
            code = EXIT_OK if ok else EXIT_BLOCKED
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
