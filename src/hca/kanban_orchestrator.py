"""Real goal-to-team orchestrator over upstream Hermes Kanban.

This is the product execution seam the controller required: ``hca run`` against
a configured Hermes installation submits *durable real work* to the upstream
Kanban board and derives its outcome from terminal upstream evidence. It does
not mutate HCA-only state or default-block when the prerequisites are valid.

Boundary (kept intact):
  * Upstream Kanban owns lifecycle truth — tasks, runs, claims, dependencies,
    results, attachments. We create/observe/close those through the current
    public functions (``create_task``, ``decompose_triage_task``,
    ``dispatch_once``, ``complete_task``) rather than duplicating Kanban SQL.
  * HCA state is projection/mapping/lease truth only.

Flow:
  1. ``plan`` validates the planner's bounded task graph (the decomposition
     barrier), creates a root *triage* container assigned to the planner slot,
     and fans the validated children out atomically with
     ``decompose_triage_task`` (so no child can dispatch before the whole graph
     is inserted and released).
  2. ``execute`` runs reservation-first HCA dispatch ticks (the real spawn
     seam) until every child is terminal or the budget is spent, then reads
     upstream task truth and returns :class:`ExecutionEvidence`.
  3. The service maps that evidence to a run state via
     :func:`hca.evidence.derive_final_state` — completion is *derived*, never
     asserted by this class.

The tmux slot manager is injectable so tests can bind a real fake-process PID
without a live model, exercising the same ``dispatch_once`` + HCA spawn seam.
"""

from __future__ import annotations

import sqlite3
import subprocess
import re
import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Optional

from hca.config import FleetConfig
from hca.decompose import TaskNode, validate_task_graph
from hca.evidence import (
    TERMINAL_TASK_STATUSES,
    ExecutionEvidence,
    TaskEvidence,
    review_required,
)
from hca.hermes_compat import (
    HermesCompatError,
    assert_sole_dispatcher,
    import_kanban_db,
)
from hca.process_identity import (
    proc_start_ticks,
    process_group_alive,
    process_identity_matches,
)
from hca.result import Artifact
from hca.routing import (
    planner_slots,
    resolve_role_hint,
    reviewer_slots,
    worker_slots,
)
from hca.run import RunState, RunStore
from hca.state import StateDB

PlannerFn = Callable[["FleetConfig", object, str, str], list[TaskNode]]

# Upstream review-column status → an independent reviewer ran.
_REVIEW_STATUSES = frozenset({"review"})


_RESULT_COMMIT_RE = re.compile(r"HCA_RESULT_COMMIT: ([0-9a-f]{40}|[0-9a-f]{64})")


def validate_git_result(task, result: str) -> tuple[bool, str, list[Artifact]]:
    """Bind a worker's claimed result to the exact recorded worktree HEAD."""
    first = next((line.strip() for line in (result or "").splitlines() if line.strip()), "")
    match = _RESULT_COMMIT_RE.fullmatch(first)
    if match is None:
        return (
            False,
            "project result first non-empty line must be exactly "
            "HCA_RESULT_COMMIT: <commit-oid>",
            [],
        )
    commit = match.group(1)
    workspace = getattr(task, "workspace_path", None)
    if not workspace:
        return False, "project result has no recorded workspace_path", []
    try:
        root = Path(str(workspace)).expanduser().resolve(strict=True)
    except OSError:
        return False, "recorded project workspace does not exist", []

    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.stdout.strip()

    try:
        git("cat-file", "-e", f"{commit}^{{commit}}")
    except (OSError, subprocess.SubprocessError):
        return False, f"claimed commit {commit} does not exist in recorded workspace", []
    try:
        head = git("rev-parse", "--verify", "HEAD")
        tree = git("rev-parse", "--verify", f"{commit}^{{tree}}")
        actual_root = Path(git("rev-parse", "--show-toplevel")).resolve(strict=True)
    except (OSError, subprocess.SubprocessError):
        return False, "could not resolve claimed commit tree from recorded workspace", []
    if head != commit:
        return False, f"claimed commit {commit} is not workspace HEAD {head}", []
    if actual_root != root:
        return False, "recorded workspace_path is not the Git worktree root", []
    return (
        True,
        "",
        [
            Artifact(name="workspace", kind="worktree", ref=str(root)),
            Artifact(name="result-commit", kind="git_commit", ref=commit),
            Artifact(name="result-tree", kind="git_tree", ref=tree),
        ],
    )


def default_planner(
    cfg: FleetConfig, spec, planner: str, worker: str
) -> list[TaskNode]:
    """Deterministic bounded fan-out from explicitly independent criteria.

    A one-step goal remains one execution task. Fan-out is created only when the
    operator supplies at least two acceptance criteria *and* explicitly declares
    them mutually independent. HCA never infers independence from opaque prose.
    The resulting DAG is independent of the requested execution concurrency:
    ``concurrency`` controls only the active dispatch wave, enabling fair c1/cN
    comparisons over the same tasks. Every slice converges through one
    integration node before optional review and final collection.
    """
    requested_acceptance = tuple(spec.acceptance_criteria)
    acceptance = requested_acceptance or ("goal addressed",)
    reviewed = review_required(spec)
    # Parallel graphs reserve one integration, one final, and (when enabled)
    # one review + one sticky review gate inside the run's task budget.
    graph_overhead = 2 + (2 if reviewed else 0)
    max_parallel_work = max(0, int(spec.budgets.max_tasks) - graph_overhead)
    fanout = min(
        len(requested_acceptance),
        max(1, int(spec.budgets.max_workers)),
        max_parallel_work,
    )
    parallel = bool(spec.independent_criteria and fanout >= 2)

    nodes: list[TaskNode] = []
    if parallel:
        criterion_groups: list[list[str]] = [[] for _ in range(fanout)]
        for index, criterion in enumerate(requested_acceptance):
            criterion_groups[index % fanout].append(criterion)
        for index, group in enumerate(criterion_groups, 1):
            nodes.append(
                TaskNode(
                    id=f"work-{index}",
                    title=f"Independent work slice {index}: {spec.goal[:64]}",
                    role_hint="worker",
                    scope=(
                        f"Overall goal: {spec.goal[:400]}\n\n"
                        "Work only on this independently verifiable slice. Do not assume "
                        "another worker's mutable checkout. Complete with a concrete result "
                        "or artifact and include every handoff detail needed by integration."
                    ),
                    acceptance_criteria=tuple(group),
                    expected_artifacts=(f"independent slice {index} result",),
                    kind="work",
                )
            )
        work_ids = tuple(node.id for node in nodes)
        nodes.append(
            TaskNode(
                id="integration",
                title="Integrate the independent work slices",
                role_hint="worker",
                depends_on=work_ids,
                scope=(
                    f"Overall goal: {spec.goal[:400]}\n\n"
                    "Read every parent result, reconcile conflicts, and produce one coherent "
                    "integrated result satisfying all acceptance criteria. Preserve source "
                    "provenance; never claim a parent artifact that was not actually produced."
                ),
                acceptance_criteria=acceptance,
                expected_artifacts=("integrated result with parent provenance",),
                kind="integration",
            )
        )
        implementation_parent = "integration"
    else:
        nodes.append(
            TaskNode(
                id="work",
                title=f"Implement: {spec.goal[:80]}",
                role_hint="worker",
                scope=(
                    f"{spec.goal[:400] or 'the run goal'}\n\n"
                    "Complete the Kanban task only after producing a concrete result "
                    "or attachment that addresses the acceptance criteria."
                ),
                acceptance_criteria=acceptance,
                expected_artifacts=("result summary",),
                kind="work",
            )
        )
        implementation_parent = "work"

    final_parent = implementation_parent
    if reviewed:
        nodes.append(
            TaskNode(
                id="review-1",
                title="Independently verify the implementation",
                role_hint="reviewer",
                depends_on=(implementation_parent,),
                scope=(
                    "Review the preceding implementation against the stated goal and "
                    "acceptance criteria. Do not modify the implementation. Start the "
                    "result with exactly `HCA_REVIEW: ACCEPT` when verified, or "
                    "`HCA_REVIEW: REJECT` followed by specific defects and evidence."
                ),
                acceptance_criteria=acceptance,
                expected_artifacts=("review verdict and verification evidence",),
                kind="review",
            )
        )
        nodes.append(
            TaskNode(
                id="review-gate-1",
                title="Confirm the accepted review gate",
                role_hint="planner",
                depends_on=("review-1",),
                scope=(
                    "Confirm that the latest independent review accepted the work. "
                    "Report `HCA_GATE: PASS` with the accepted review task and result "
                    "identifiers. Do not alter implementation artifacts."
                ),
                acceptance_criteria=("accepted independent review is cited",),
                expected_artifacts=("review gate evidence",),
                kind="gate",
            )
        )
        final_parent = "review-gate-1"
    nodes.append(
        TaskNode(
            id="final",
            title="Collect and finalize the run result",
            role_hint="planner",
            depends_on=(final_parent,),
            scope="aggregate accepted task outputs into the final run result",
            kind="final",
        )
    )
    return nodes


class KanbanOrchestrator:
    """Drive one run through real Hermes Kanban and observe its evidence."""

    def __init__(
        self,
        cfg: FleetConfig,
        *,
        state: Optional[StateDB] = None,
        tmux=None,
        board: Optional[str] = None,
        planner_fn: PlannerFn = default_planner,
        max_ticks: int = 120,
        poll_interval: float = 0.1,
        max_wall_seconds: Optional[float] = None,
        enforce_sole_dispatcher: bool = True,
    ):
        self.cfg = cfg
        self.board = board or cfg.board
        state_dir = Path(cfg.state_dir or "~/.hca").expanduser()
        self.state = state or StateDB(state_dir / "hca.sqlite")
        self._tmux = tmux
        self.planner_fn = planner_fn
        # Retained as an API-compatibility diagnostic knob. Wall-clock budget is
        # authoritative: a fast poll interval must not silently shorten a run.
        self.max_ticks = max_ticks
        self.poll_interval = max(0.2, float(poll_interval))
        self.max_wall_seconds = (
            None
            if max_wall_seconds is None
            else max(0.1, float(max_wall_seconds))
        )
        self.enforce_sole_dispatcher = enforce_sole_dispatcher

    # -- kanban connection -------------------------------------------------

    def _kb(self):
        return import_kanban_db()

    def _conn(self) -> sqlite3.Connection:
        # Use upstream ``connect`` so a fresh board auto-initializes its schema
        # and gets WAL/pragmas exactly as the dispatcher expects.
        kb = self._kb()
        conn = kb.connect(board=self.board)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=60000")
        except sqlite3.Error:
            pass
        return conn

    # -- concrete profiles -------------------------------------------------

    def _planner_profile(self) -> str:
        slots = planner_slots(self.cfg)
        if slots:
            return slots[0].profile
        # fall back to any worker if no dedicated planner slot exists
        w = worker_slots(self.cfg)
        return w[0].profile if w else f"hca-{self.cfg.name}-orchestrator-01"

    def _worker_profile(self) -> str:
        slots = worker_slots(self.cfg)
        if slots:
            return slots[0].profile
        return f"hca-{self.cfg.name}-coder-01"

    def _reviewer_profile(self) -> str:
        slots = reviewer_slots(self.cfg)
        if slots:
            return slots[0].profile
        return self._planner_profile()

    @staticmethod
    def _root_body(spec) -> str:
        parts = [f"Goal:\n{spec.goal}"]
        if spec.constraints:
            parts.append("Constraints:\n- " + "\n- ".join(spec.constraints))
        if spec.acceptance_criteria:
            parts.append(
                "Acceptance criteria:\n- " + "\n- ".join(spec.acceptance_criteria)
            )
        return "\n\n".join(parts)

    @staticmethod
    def _workspace_for_spec(spec) -> tuple[str, Optional[str]]:
        if not spec.project_root:
            return "scratch", None
        requested = Path(spec.project_root).expanduser().resolve()
        if not requested.is_dir():
            raise ValueError(f"project root is not a directory: {requested}")
        proc = subprocess.run(
            ["git", "-C", str(requested), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            raise ValueError(
                f"project root {requested} is not a git repository; HCA refuses "
                "a shared mutable checkout because concurrent writers require "
                "isolated worktrees"
            )
        repo_root = Path(proc.stdout.strip()).resolve()
        return "worktree", str(repo_root)

    def _apply_child_metadata(self, conn, child_ids, nodes, spec) -> None:
        """Fill metadata the current upstream decomposition API cannot carry.

        ``decompose_triage_task(auto_promote=False)`` atomically inserts the
        complete graph but does not yet accept session/budget/goal fields for
        children. They remain non-dispatchable ``todo`` while this guarded shim
        fills those current public-schema columns, then the caller promotes the
        graph. Fail closed on drift rather than launching uncorrelated workers.
        """
        required = {
            "session_id",
            "max_runtime_seconds",
            "max_retries",
            "goal_mode",
            "goal_max_turns",
            "status",
            "block_kind",
        }
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        missing = sorted(required - columns)
        if missing:
            raise HermesCompatError(
                "upstream task metadata columns missing: " + ", ".join(missing)
            )
        gate_ids: list[str] = []
        with conn:
            for task_id, node in zip(child_ids, nodes):
                goal_mode = 1 if node.kind in {"work", "rework"} else 0
                conn.execute(
                    "UPDATE tasks SET session_id = ?, max_runtime_seconds = ?, "
                    "max_retries = ?, goal_mode = ?, goal_max_turns = ? WHERE id = ?",
                    (
                        spec.run_id,
                        int(spec.budgets.wall_seconds),
                        int(spec.budgets.max_retries),
                        goal_mode,
                        int(spec.budgets.max_turns_per_task) if goal_mode else None,
                        task_id,
                    ),
                )
                if node.kind == "gate":
                    gate_ids.append(task_id)
        kb = self._kb()
        for task_id in gate_ids:
            # Upstream sticky blocks are event-backed. Move the still-unclaimed
            # todo gate through ready solely so the public block API can record
            # the hold; claim_task would reject it while its review parent is open.
            with conn:
                conn.execute(
                    "UPDATE tasks SET status = 'ready' WHERE id = ? AND status = 'todo'",
                    (task_id,),
                )
            if not kb.block_task(
                conn,
                task_id,
                reason="awaiting an accepted independent review",
                kind="capability",
            ):
                raise HermesCompatError(f"could not create sticky review gate {task_id}")

    def _profile_for_hint(
        self,
        hint: str,
        planner: str,
        worker: str,
        *,
        worker_index: int = 0,
    ) -> str:
        normalized = (hint or "").lower()
        if normalized in ("planner", "orchestrator", "final"):
            return planner
        if normalized in ("reviewer", "review", "qa"):
            return self._reviewer_profile()

        concrete_role, error = resolve_role_hint(normalized or "worker")
        if error:
            raise HermesCompatError(error)
        pool = worker_slots(self.cfg)
        if concrete_role:
            pool = [slot for slot in pool if slot.role == concrete_role]
        if not pool:
            if concrete_role:
                raise HermesCompatError(
                    f"no concrete worker profile exists for role {concrete_role!r}"
                )
            return worker
        return pool[worker_index % len(pool)].profile

    # -- plan --------------------------------------------------------------

    def plan(self, spec, store: RunStore) -> RunState:
        # Ownership is a prerequisite to graph creation, not merely dispatch.
        # A live embedded gateway enumerates every board in its Hermes home and
        # could claim newly-ready children between decomposition and HCA's first
        # dispatch tick. Fail closed before opening/creating the board.
        if self.enforce_sole_dispatcher:
            try:
                assert_sole_dispatcher(self.board)
            except HermesCompatError as exc:
                reason = f"dispatcher ownership preflight failed: {exc}"
                store.append_event(spec.run_id, "run.preflight", reason)
                store.set_state(spec.run_id, RunState.BLOCKED, reason=reason)
                return RunState.BLOCKED

        planner = self._planner_profile()
        worker = self._worker_profile()

        nodes = list(self.planner_fn(self.cfg, spec, planner, worker))
        val = validate_task_graph(nodes, max_tasks=spec.budgets.max_tasks)
        if not val.valid:
            store.append_event(
                spec.run_id, "run.plan_rejected",
                "planner graph rejected: " + "; ".join(val.reasons),
                {"reasons": val.reasons},
            )
            return RunState.BLOCKED

        try:
            workspace_kind, workspace_path = self._workspace_for_spec(spec)
        except ValueError as exc:
            store.append_event(spec.run_id, "run.plan_rejected", str(exc))
            return RunState.BLOCKED

        kb = self._kb()
        conn = self._conn()
        try:
            root_id = kb.create_task(
                conn,
                title=f"[HCA run] {spec.goal[:120]}",
                body=self._root_body(spec),
                assignee=planner,
                created_by="hca",
                triage=True,
                board=self.board,
                session_id=spec.run_id,
                workspace_kind=workspace_kind,
                workspace_path=workspace_path,
                max_runtime_seconds=int(spec.budgets.wall_seconds),
                max_retries=int(spec.budgets.max_retries),
            )
            children = self._nodes_to_children(nodes, planner, worker)
            child_ids = kb.decompose_triage_task(
                conn,
                root_id,
                root_assignee=planner,
                children=children,
                author=planner,
                auto_promote=False,
            )
            if child_ids:
                self._apply_child_metadata(conn, child_ids, nodes, spec)
                # The synthetic root is an aggregation record, not worker work.
                # Hold it with an upstream event-backed sticky block so a
                # dispatcher can never claim it in the small window before HCA
                # records aggregate completion after all children finish.
                with conn:
                    conn.execute(
                        "UPDATE tasks SET status = 'ready' WHERE id = ?",
                        (root_id,),
                    )
                if not kb.block_task(
                    conn,
                    root_id,
                    reason="HCA owns root aggregation; do not dispatch",
                    kind="capability",
                ):
                    raise HermesCompatError(
                        f"could not create sticky root aggregation gate {root_id}"
                    )
                kb.recompute_ready(conn)
            conn.commit()
        finally:
            conn.close()

        if not child_ids:
            store.append_event(
                spec.run_id, "run.plan_failed",
                "decompose_triage_task created no children",
            )
            return RunState.BLOCKED

        # Projection-only mapping: run → root/child Kanban tasks. HCA never
        # becomes a second lifecycle authority over these ids.
        store.append_event(
            spec.run_id, "run.kanban_root",
            f"root={root_id} children={len(child_ids)}",
            {
                "root_task_id": root_id,
                "child_task_ids": list(child_ids),
                "board": self.board,
                "node_kinds": {c: n.kind for c, n in zip(child_ids, nodes)},
                "reviewer_profile": self._reviewer_profile(),
            },
        )
        return RunState.PLANNING

    def _nodes_to_children(
        self, nodes: list[TaskNode], planner: str, worker: str
    ) -> list[dict]:
        index = {n.id: i for i, n in enumerate(nodes)}
        children: list[dict] = []
        worker_index = 0
        for n in nodes:
            parents = [index[d] for d in n.depends_on if d in index]
            is_execution = (n.role_hint or "").lower() not in {
                "planner",
                "orchestrator",
                "final",
                "reviewer",
                "review",
                "qa",
            }
            assignee = self._profile_for_hint(
                n.role_hint,
                planner,
                worker,
                worker_index=worker_index,
            )
            if is_execution:
                worker_index += 1
            completion_protocol = ""
            if is_execution:
                completion_protocol = (
                    "\n\ncompletion protocol: If this task changes files in a Git "
                    "worktree, commit the accepted changes, verify the exact commit "
                    "with `git rev-parse HEAD`, and call `kanban_complete` with a "
                    "result whose first non-empty line is exactly "
                    "`HCA_RESULT_COMMIT: <40-hex-commit>`. Put verification details "
                    "on later lines. The conversational final response does not "
                    "replace the `kanban_complete` result."
                )
            children.append(
                {
                    "title": n.title,
                    "body": (
                        f"{n.scope}\n\nacceptance: "
                        f"{'; '.join(n.acceptance_criteria) or 'n/a'}"
                        f"{completion_protocol}"
                    ),
                    "assignee": assignee,
                    "parents": parents,
                }
            )
        return children

    # -- execute -----------------------------------------------------------

    def _observation_window_seconds(self, spec) -> float:
        """Resolve the authoritative run observation budget.

        Production uses the per-run wall budget. Tests/embedders may provide an
        explicit constructor cap, which can only shorten—not extend—the run's
        declared budget.
        """
        run_budget = max(0.1, float(spec.budgets.wall_seconds))
        if self.max_wall_seconds is None:
            return run_budget
        return min(run_budget, self.max_wall_seconds)

    def execute(self, spec, store: RunStore) -> ExecutionEvidence:
        mapping = self._mapping(store, spec.run_id)
        if not mapping:
            return ExecutionEvidence(
                reason="no Kanban root recorded for this run (planning did not "
                "produce a task graph)"
            )
        root_id = mapping["root_task_id"]
        child_ids = list(mapping.get("child_task_ids") or [])
        node_kinds = mapping.get("node_kinds") or {}
        reviewer_profile = mapping.get("reviewer_profile", "")

        observation_window = self._observation_window_seconds(spec)
        deadline = time.time() + observation_window
        wave = max(1, int(spec.concurrency or 1))

        ticks = 0
        terminal = False
        paused_reason = ""
        while time.time() < deadline:
            ticks += 1
            try:
                mapping = self.advance(spec, store) or mapping
                child_ids = list(mapping.get("child_task_ids") or [])
                node_kinds = mapping.get("node_kinds") or {}
                self._quarantine_worker_graph_expansion(spec, store, mapping)
                review_block = next(
                    (
                        event
                        for event in reversed(store.list_events(spec.run_id))
                        if event["kind"] == "run.review_blocked"
                    ),
                    None,
                )
                if review_block is not None:
                    paused_reason = review_block["message"]
                    break
                before_dispatch = self._statuses(child_ids)
                statuses = before_dispatch
                # Reap terminal-task ownership before deciding whether the
                # wave has a free slot. A terminal task with a still-running
                # projection remains active until this exact-identity sweep
                # proves its worker gone.
                self._reconcile_leases(child_ids, before_dispatch)
                if (
                    before_dispatch
                    and all(
                        status in TERMINAL_TASK_STATUSES
                        for status in before_dispatch.values()
                    )
                    and not self._live_owned_worker_tasks(child_ids)
                ):
                    terminal = True
                    break
                active_wave = self._active_wave_count(child_ids, before_dispatch)
                remaining = max(0, wave - active_wave)
                if remaining:
                    self._dispatch_tick(
                        remaining,
                        child_ids,
                        max_in_progress=wave,
                        owner_run_id=spec.run_id,
                        max_supervisor_replacements=spec.budgets.max_supervisor_replacements,
                        requested_disk_mb=spec.budgets.max_disk_mb,
                        remaining_wall_seconds=self._remaining_wall_seconds(spec),
                    )
                    statuses = self._statuses(child_ids)
            except HermesCompatError as exc:
                return ExecutionEvidence(
                    root_task_id=root_id,
                    reason=f"dispatch failed: {exc}",
                )
            # Release the durable lease of any task that is no longer running
            # (terminal, or reclaimed after a worker crash) so admission frees
            # its credit exactly once.
            self._reconcile_leases(child_ids, statuses)
            if (
                statuses
                and all(s in TERMINAL_TASK_STATUSES for s in statuses.values())
                and not self._live_owned_worker_tasks(child_ids)
            ):
                terminal = True
                break
            holding = [
                (tid, status)
                for tid, status in statuses.items()
                if status in {"blocked", "failed", "crashed", "timed_out"}
                and not (
                    status == "blocked" and node_kinds.get(tid) == "gate"
                )
            ]
            if holding and not any(s == "running" for s in statuses.values()):
                # Upstream block/failure truth can be committed before the
                # exact Hermes worker exits. Do not project a settled holding
                # state while HCA still owns that process and lease.
                if self._live_owned_worker_tasks(child_ids):
                    time.sleep(self.poll_interval)
                    continue
                tid, status = holding[0]
                paused_reason = f"execution paused: task {tid} is {status}"
                break
            time.sleep(self.poll_interval)

        self._maybe_close_root(root_id, child_ids, store, spec.run_id)
        # Final sweep: every non-running task releases its lease.
        self._reconcile_leases(child_ids, self._statuses(child_ids))
        evidence = self._build_evidence(
            spec, root_id, child_ids, node_kinds, reviewer_profile
        )
        if not evidence.live_worker_task_ids:
            # A process can reap between the prior sweep and evidence build.
            # Exact-dead identities cannot become live again; finish releasing
            # their leases before success is projected.
            self._reconcile_leases(child_ids, self._statuses(child_ids))
        self._sync_questions_from_evidence(spec, store, evidence)
        if not terminal:
            evidence.reason = paused_reason or (
                f"execution observation window exhausted after {ticks} tick(s); "
                "work remains non-terminal and requires supervisor/status reconciliation"
            )
        return evidence

    def start(self, spec, store: RunStore) -> dict:
        """Submit the first admitted dispatch wave and return without waiting.

        Used by ``hca run --detach``. Continued task dispatch is owned by the
        fleet supervisor (``hca up --daemon``); status/collect project live
        Kanban truth and can complete the durable run later.
        """
        mapping = self._mapping(store, spec.run_id)
        if not mapping:
            raise RuntimeError("planning produced no Kanban mapping")
        mapping = self.advance(spec, store) or mapping
        self._quarantine_worker_graph_expansion(spec, store, mapping)
        wave = max(1, int(spec.concurrency or 1))
        child_ids = list(mapping.get("child_task_ids") or [])
        result = self._dispatch_tick(
            wave,
            child_ids,
            max_in_progress=wave,
            owner_run_id=spec.run_id,
            max_supervisor_replacements=spec.budgets.max_supervisor_replacements,
            requested_disk_mb=spec.budgets.max_disk_mb,
            remaining_wall_seconds=self._remaining_wall_seconds(spec),
        )
        store.append_event(
            spec.run_id,
            "run.detached",
            "initial dispatch wave submitted; supervisor owns continued reconciliation",
            {"dispatch": result},
        )
        return result

    def tick(
        self, spec, store: RunStore, *, dispatch: bool = True
    ) -> ExecutionEvidence:
        """Advance one restart-safe controller iteration for ``spec``.

        This is the durable detached/supervisor seam: review mutations happen
        before dispatch, exact worker leases are reconciled from upstream truth,
        and at most the remaining useful wave capacity is admitted.
        """
        mapping = self.advance(spec, store) or self._mapping(store, spec.run_id)
        if not mapping:
            return ExecutionEvidence(reason="no Kanban mapping for controller tick")
        self._quarantine_worker_graph_expansion(spec, store, mapping)
        child_ids = list(mapping.get("child_task_ids") or [])
        node_kinds = dict(mapping.get("node_kinds") or {})
        statuses = self._statuses(child_ids)
        self._reconcile_leases(child_ids, statuses)

        terminal_review_block = any(
            event["kind"] == "run.review_blocked"
            for event in store.list_events(spec.run_id)
        )
        wave = max(1, int(spec.concurrency or 1))
        live_running = self._active_wave_count(child_ids, statuses)
        declared_terminal = bool(statuses) and all(
            status in TERMINAL_TASK_STATUSES for status in statuses.values()
        )
        if (
            dispatch
            and not terminal_review_block
            and not declared_terminal
            and live_running < wave
        ):
            remaining = max(0, wave - live_running)
            self._dispatch_tick(
                remaining,
                child_ids,
                max_in_progress=wave,
                owner_run_id=spec.run_id,
                max_supervisor_replacements=spec.budgets.max_supervisor_replacements,
                requested_disk_mb=spec.budgets.max_disk_mb,
                remaining_wall_seconds=self._remaining_wall_seconds(spec),
            )
            statuses = self._statuses(child_ids)
            self._reconcile_leases(child_ids, statuses)

        self._maybe_close_root(
            mapping["root_task_id"], child_ids, store, spec.run_id
        )
        evidence = self._build_evidence(
            spec,
            mapping["root_task_id"],
            child_ids,
            node_kinds,
            mapping.get("reviewer_profile", ""),
        )
        if not evidence.live_worker_task_ids:
            self._reconcile_leases(child_ids, self._statuses(child_ids))
        self._sync_questions_from_evidence(spec, store, evidence)
        return evidence

    def _quarantine_worker_graph_expansion(self, spec, store: RunStore, mapping: dict) -> list[str]:
        """Block ready tasks created by this fleet's workers outside HCA's DAG.

        Hermes workers retain upstream Kanban tools for task handoff, but the HCA
        controller is the sole owner of graph expansion. A model-created task is
        therefore preserved as audit evidence while being made non-dispatchable;
        it never receives an HCA reservation, lease, or process.
        """
        allowed = set(mapping.get("child_task_ids") or [])
        root_id = str(mapping.get("root_task_id") or "")
        if root_id:
            allowed.add(root_id)
        owned_profiles = {
            slot.profile
            for slot in (
                planner_slots(self.cfg)
                + worker_slots(self.cfg)
                + reviewer_slots(self.cfg)
            )
        }
        if not owned_profiles:
            return []

        kb = self._kb()
        conn = self._conn()
        denied: list[str] = []
        try:
            for task in kb.list_tasks(conn, status="ready"):
                task_id = str(getattr(task, "id", "") or "")
                if not task_id or task_id in allowed:
                    continue
                if str(getattr(task, "created_by", "") or "") not in owned_profiles:
                    continue
                if getattr(task, "current_run_id", None) is not None:
                    continue
                if kb.block_task(
                    conn,
                    task_id,
                    reason=(
                        f"HCA_OUT_OF_GRAPH: outside persisted graph for run {spec.run_id}; "
                        "worker graph expansion denied"
                    ),
                    kind="capability",
                ):
                    denied.append(task_id)
            conn.commit()
        finally:
            conn.close()

        if denied:
            store.append_event(
                spec.run_id,
                "run.graph_expansion_denied",
                f"quarantined {len(denied)} worker-created out-of-graph task(s)",
                {"task_ids": denied, "board": self.board},
            )
        return denied

    def advance(self, spec, store: RunStore) -> Optional[dict]:
        """Advance review/rework gates without claiming or spawning work.

        A rejected review adds exactly one sequential rework + re-review pair
        and links the new review in front of the final collector. Idempotency
        keys plus event checks make this safe across controller restarts. When
        the review budget is exhausted (or the verdict is malformed), the final
        task is visibly blocked rather than dispatched.
        """
        mapping = self._mapping(store, spec.run_id)
        if not mapping or not review_required(spec):
            return mapping
        events = store.list_events(spec.run_id)
        if any(
            event["kind"] in {"run.review_accepted", "run.review_blocked"}
            for event in events
        ):
            return mapping
        child_ids = list(mapping.get("child_task_ids") or [])
        node_kinds = dict(mapping.get("node_kinds") or {})
        evidence = self._build_evidence(
            spec,
            mapping["root_task_id"],
            child_ids,
            node_kinds,
            mapping.get("reviewer_profile", ""),
        )
        completed_reviews = [
            task
            for task in evidence.tasks
            if task.is_review and task.terminal_status == "done"
        ]
        if not completed_reviews:
            return mapping
        latest = completed_reviews[-1]
        handled = {
            str((event.get("data") or {}).get("review_task_id", ""))
            for event in events
            if event["kind"] in {
                "run.review_accepted",
                "run.review_rework",
                "run.review_blocked",
            }
        }
        if latest.task_id in handled:
            return mapping

        final_id = next(
            (task_id for task_id in child_ids if node_kinds.get(task_id) == "final"),
            "",
        )
        gate_id = next(
            (task_id for task_id in child_ids if node_kinds.get(task_id) == "gate"),
            "",
        )
        if not final_id or not gate_id:
            raise HermesCompatError(
                "reviewed run has no explicit review gate/final collection task"
            )

        if latest.review_verdict == "accept":
            self._open_review_gate(gate_id)
            store.append_event(
                spec.run_id,
                "run.review_accepted",
                f"review {latest.task_id} accepted by {latest.reviewed_by}",
                {"review_task_id": latest.task_id},
            )
            return mapping

        review_count = sum(1 for kind in node_kinds.values() if kind == "review")
        if (
            latest.review_verdict != "reject"
            or review_count >= int(spec.budgets.max_review_cycles)
        ):
            reason = (
                f"review {latest.task_id} returned no valid verdict"
                if latest.review_verdict != "reject"
                else (
                    f"review {latest.task_id} rejected the work and the bounded "
                    f"review budget ({spec.budgets.max_review_cycles}) is exhausted"
                )
            )
            self._block_review_gate(gate_id, reason)
            store.append_event(
                spec.run_id,
                "run.review_blocked",
                reason,
                {"review_task_id": latest.task_id, "cycles": review_count},
            )
            return mapping

        implementer_id = next(
            (
                task_id
                for task_id in reversed(child_ids)
                if node_kinds.get(task_id) in {"work", "rework"}
            ),
            "",
        )
        if not implementer_id:
            raise HermesCompatError("rejected review has no implementation task")

        kb = self._kb()
        conn = self._conn()
        try:
            implementation = kb.get_task(conn, implementer_id)
            if implementation is None:
                raise HermesCompatError(
                    f"implementation task {implementer_id} disappeared before rework"
                )
            reviewer = mapping.get("reviewer_profile", "") or self._reviewer_profile()
            implementer = getattr(implementation, "assignee", "") or self._worker_profile()
            if reviewer == implementer:
                reason = "reviewer is not independent of the implementation profile"
                self._block_review_gate(gate_id, reason, conn=conn, kb=kb)
                store.append_event(
                    spec.run_id,
                    "run.review_blocked",
                    reason,
                    {"review_task_id": latest.task_id, "cycles": review_count},
                )
                return mapping

            workspace_kind = getattr(implementation, "workspace_kind", "scratch") or "scratch"
            workspace_path = getattr(implementation, "workspace_path", None)
            branch_name = getattr(implementation, "branch_name", None)
            rejection = (latest.result or "review rejected without details")[:4000]
            cycle = review_count + 1
            rework_id = kb.create_task(
                conn,
                title=f"Rework after rejected review (cycle {cycle})",
                body=(
                    "Address every defect from the independent review below. Preserve "
                    "the existing implementation workspace and complete with concrete "
                    f"verification evidence.\n\n{rejection}"
                ),
                assignee=implementer,
                created_by="hca",
                workspace_kind=workspace_kind,
                workspace_path=workspace_path,
                branch_name=branch_name if workspace_kind == "worktree" else None,
                parents=[latest.task_id],
                idempotency_key=f"hca:{spec.run_id}:rework:{latest.task_id}",
                max_runtime_seconds=int(spec.budgets.wall_seconds),
                max_retries=int(spec.budgets.max_retries),
                goal_mode=True,
                goal_max_turns=int(spec.budgets.max_turns_per_task),
                session_id=spec.run_id,
                board=self.board,
            )
            review_id = kb.create_task(
                conn,
                title=f"Independently verify rework (cycle {cycle})",
                body=(
                    "Verify the rework against the goal, acceptance criteria, and prior "
                    "rejection. Do not modify it. Start the result with exactly "
                    "`HCA_REVIEW: ACCEPT` or `HCA_REVIEW: REJECT`, then cite evidence."
                ),
                assignee=reviewer,
                created_by="hca",
                workspace_kind=workspace_kind,
                workspace_path=workspace_path,
                branch_name=branch_name if workspace_kind == "worktree" else None,
                parents=[rework_id],
                idempotency_key=f"hca:{spec.run_id}:review:{latest.task_id}",
                max_runtime_seconds=int(spec.budgets.wall_seconds),
                max_retries=int(spec.budgets.max_retries),
                session_id=spec.run_id,
                board=self.board,
            )
            kb.link_tasks(conn, review_id, gate_id)
            kb.recompute_ready(conn)
            conn.commit()
        finally:
            conn.close()

        child_ids.extend([rework_id, review_id])
        node_kinds[rework_id] = "rework"
        node_kinds[review_id] = "review"
        updated = dict(mapping)
        updated["child_task_ids"] = child_ids
        updated["node_kinds"] = node_kinds
        store.append_event(
            spec.run_id,
            "run.kanban_root",
            f"root={mapping['root_task_id']} children={len(child_ids)}",
            updated,
        )
        store.append_event(
            spec.run_id,
            "run.review_rework",
            f"review {latest.task_id} rejected; staged {rework_id} then {review_id}",
            {
                "review_task_id": latest.task_id,
                "rework_task_id": rework_id,
                "next_review_task_id": review_id,
                "cycle": cycle,
            },
        )
        return updated

    def _open_review_gate(self, task_id: str) -> None:
        kb = self._kb()
        conn = self._conn()
        try:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise HermesCompatError(f"review gate {task_id} disappeared")
            status = getattr(task, "status", "")
            if status == "blocked":
                if not kb.unblock_task(conn, task_id):
                    raise HermesCompatError(f"could not open review gate {task_id}")
            elif status not in {"ready", "running", "done"}:
                raise HermesCompatError(
                    f"review gate {task_id} is in unexpected state {status!r}"
                )
            kb.recompute_ready(conn)
            conn.commit()
        finally:
            conn.close()

    def _block_review_gate(
        self, task_id: str, reason: str, *, conn=None, kb=None
    ) -> None:
        own_conn = conn is None
        kb = kb or self._kb()
        conn = conn or self._conn()
        try:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise HermesCompatError(f"review gate {task_id} disappeared")
            if getattr(task, "status", "") == "blocked":
                kb.unblock_task(conn, task_id)
            kb.recompute_ready(conn)
            task = kb.get_task(conn, task_id)
            if task is not None and getattr(task, "status", "") in {"ready", "running"}:
                kb.block_task(conn, task_id, reason=reason, kind=None)
            conn.commit()
        finally:
            if own_conn:
                conn.close()

    def _reconcile_leases(self, child_ids: list[str], statuses: dict[str, str]) -> None:
        """Release the durable lease of any task not currently ``running``.

        Covers terminal completion, a reclaimed crash (task bounced back to
        ready), block, and timeout — the lease is freed exactly once so a
        launched worker consumes a credit only while it is actually running.
        """
        from hca.leases import release_worker_lease

        for tid, status in statuses.items():
            if status != "running":
                rec = self.state.latest_run_for_task(self.board, tid)
                if rec is not None and rec.status == "running":
                    if self._run_record_is_live(rec):
                        # The completion tool commits Kanban truth before the
                        # Hermes process necessarily exits. Keep exact process
                        # ownership and its lease until the process is reaped.
                        continue
                    mapped = "completed" if status in {"done", "archived"} else status
                    self.state.mark_run_status(
                        self.board,
                        rec.run_id,
                        mapped,
                        error="" if mapped == "completed" else f"upstream task is {status}",
                    )
                    self._retire_terminal_slot(rec)
                release_worker_lease(self.state, board=self.board, task_id=tid)
                self.state.release_leases_by_owner(tid, kind="subagent")

    def _retire_terminal_slot(self, rec) -> None:
        """Remove an exact-dead worker's tmux session for a cold fleet.

        ``run_in_slot`` leaves a dead pane after the worker exits. A fleet with
        ``warm_slots=false`` promises no retained execution slots, so terminal
        reconciliation owns deleting that HCA-named session after process
        identity has already been proven gone.
        """
        if self.cfg.warm_slots:
            return
        from hca.tmux import TmuxManager

        tmux = self._tmux or TmuxManager(socket=self.cfg.tmux_socket)
        session = rec.tmux_session or rec.slot
        try:
            tmux.kill_session(session)
        except Exception as exc:
            self.state.set_activity(
                kind="slot.cleanup_failed",
                message=f"could not retire terminal slot {session}: {exc}",
                board=rec.board,
                task_id=rec.task_id,
                run_id=rec.run_id,
                slot=rec.slot,
            )

    @staticmethod
    def _run_record_is_live(rec) -> bool:
        """Conservatively determine whether an owned worker record is alive."""
        if not rec or not rec.pid:
            return False
        if rec.pid_start_ticks is not None:
            return process_identity_matches(rec.pid, rec.pid_start_ticks)
        # Legacy rows have no reusable identity token. If their PID is live,
        # quarantine rather than claiming cleanup we cannot prove.
        return proc_start_ticks(rec.pid) is not None

    def _live_owned_worker_tasks(self, child_ids: list[str]) -> list[str]:
        live: list[str] = []
        for task_id in child_ids:
            rec = self.state.latest_run_for_task(self.board, task_id)
            if rec is not None and rec.status == "running" and self._run_record_is_live(rec):
                live.append(task_id)
        return live

    def runtime_status(self, spec, store: RunStore) -> dict:
        """Structured live status for one persisted high-level run graph."""
        mapping = self._mapping(store, spec.run_id) or {}
        child_ids = list(mapping.get("child_task_ids") or [])
        statuses = self._statuses(child_ids) if child_ids else {}
        now = time.time()
        agents = []
        for task_id in child_ids:
            rec = self.state.latest_run_for_task(self.board, task_id)
            if rec is None or rec.status != "running" or not self._run_record_is_live(rec):
                continue
            agents.append(
                {
                    "task_id": task_id,
                    "attempt_run_id": rec.run_id,
                    "profile": rec.slot,
                    "pid": rec.pid,
                    "started_at": rec.started_at,
                    "elapsed_seconds": max(0.0, now - rec.started_at),
                    "workspace": rec.workspace or "",
                }
            )

        reason_counts: dict[str, int] = {}
        child_set = set(child_ids)
        for activity in self.state.recent_activity(1000):
            if activity.get("kind") != "admission.wait":
                continue
            if activity.get("task_id") not in child_set:
                continue
            message = str(activity.get("message") or "").lower()
            if "disk" in message:
                code = "disk"
            elif "memory" in message or "kv cache" in message:
                code = "memory"
            elif "sequence" in message:
                code = "sequence_credit"
            elif "role" in message:
                code = "role_cap"
            elif "backend" in message:
                code = "backend"
            else:
                code = "other"
            reason_counts[code] = reason_counts.get(code, 0) + 1

        status_counts: dict[str, int] = {}
        for status in statuses.values():
            status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "root_task_id": mapping.get("root_task_id", ""),
            "child_task_ids": child_ids,
            "active_agents": len(agents),
            "agents": agents,
            "task_status_counts": status_counts,
            "supervisor_replacements": {
                "used": int(
                    self.state.get_meta(
                        f"supervisor_replacements:{spec.run_id}", "0"
                    )
                    or "0"
                ),
                "limit": int(spec.budgets.max_supervisor_replacements),
            },
            "admission_wait_reasons": reason_counts,
        }

    def _active_wave_count(
        self, child_ids: list[str], statuses: dict[str, str]
    ) -> int:
        """Count every upstream or exact-owned worker consuming wave capacity.

        ``kanban_complete`` makes a task terminal before its Hermes process has
        necessarily exited.  Such a worker must still occupy its c1/cN slot;
        otherwise a nominal c1 run briefly overlaps successive workers and a cN
        run can exceed its declared wave. Unknown upstream running claims are
        counted conservatively as active.
        """
        active = set(self._live_owned_worker_tasks(child_ids))
        for task_id, status in statuses.items():
            if task_id in active:
                continue
            rec = self.state.latest_run_for_task(self.board, task_id)
            if status != "running":
                if rec is not None and rec.status == "running":
                    # Reconciliation—not a second racy liveness read—owns the
                    # transition from active terminal worker to reaped.
                    active.add(task_id)
                continue
            if rec is None or rec.status != "running":
                # An upstream claim without matching HCA ownership is unknown,
                # so it consumes capacity conservatively.
                active.add(task_id)
            elif self._run_record_is_live(rec):
                active.add(task_id)
            # Exact HCA identity is known dead: dispatch_tick owns crash
            # reconciliation and may reclaim/replace it on this tick.
        return len(active)

    def _dispatch_tick(
        self,
        max_spawn: int,
        allowed_task_ids: list[str],
        *,
        max_in_progress: int | None = None,
        owner_run_id: str = "",
        max_supervisor_replacements: int | None = None,
        requested_disk_mb: int = 0,
        remaining_wall_seconds: int | None = None,
    ) -> dict:
        from hca.kanban import dispatch_tick
        from hca.tmux import TmuxManager

        tmux = self._tmux or TmuxManager(socket=self.cfg.tmux_socket)
        if remaining_wall_seconds is not None:
            self._clamp_task_runtimes(allowed_task_ids, remaining_wall_seconds)
        return dispatch_tick(
            self.cfg,
            self.state,
            tmux,
            max_spawn=max(0, int(max_spawn)),
            max_in_progress=(
                max(1, int(max_in_progress))
                if max_in_progress is not None
                else max(1, int(max_spawn))
            ),
            allowed_task_ids=set(allowed_task_ids),
            owner_run_id=owner_run_id,
            max_supervisor_replacements=max_supervisor_replacements,
            requested_disk_mb=max(0, int(requested_disk_mb)),
            max_in_progress_per_profile=1,
            skip_sole_dispatcher_check=not self.enforce_sole_dispatcher,
        )

    @staticmethod
    def _remaining_wall_seconds(spec) -> int:
        deadline = float(spec.created_at) + max(1, int(spec.budgets.wall_seconds))
        return max(1, int(deadline - time.time()))

    def _clamp_task_runtimes(
        self, task_ids: list[str], remaining_wall_seconds: int
    ) -> None:
        """Clamp unstarted task watchdogs to the immutable high-level deadline."""
        if not task_ids:
            return
        remaining = max(1, int(remaining_wall_seconds))
        conn = self._conn()
        try:
            placeholders = ",".join("?" for _ in task_ids)
            conn.execute(
                "UPDATE tasks SET max_runtime_seconds = CASE "
                "WHEN max_runtime_seconds IS NULL OR max_runtime_seconds > ? THEN ? "
                "ELSE max_runtime_seconds END "
                f"WHERE id IN ({placeholders}) AND status IN ('todo', 'ready', 'blocked')",
                (remaining, remaining, *task_ids),
            )
            conn.commit()
        finally:
            conn.close()

    def _statuses(self, task_ids: list[str]) -> dict[str, str]:
        if not task_ids:
            return {}
        kb = self._kb()
        conn = self._conn()
        out: dict[str, str] = {}
        try:
            for tid in task_ids:
                t = kb.get_task(conn, tid)
                out[tid] = (getattr(t, "status", "") or "") if t else "missing"
        finally:
            conn.close()
        return out

    def _maybe_close_root(
        self, root_id: str, child_ids: list[str], store: RunStore, run_id: str
    ) -> None:
        """Close the planner container once all children are terminally done.

        HCA plays the orchestrator's collection role here: rather than
        re-spawning the root to "judge", we promote and complete it when the
        children are all ``done``. Best-effort — never fatal.
        """
        statuses = self._statuses(child_ids)
        if not statuses or any(s != "done" for s in statuses.values()):
            return
        kb = self._kb()
        conn = self._conn()
        try:
            recompute = getattr(kb, "recompute_ready", None)
            if callable(recompute):
                try:
                    recompute(conn)
                    conn.commit()
                except Exception:
                    pass
            root = kb.get_task(conn, root_id)
            status = getattr(root, "status", "") if root else ""
            if status in ("ready", "running", "blocked"):
                try:
                    kb.complete_task(
                        conn, root_id,
                        result="run collected by HCA orchestrator",
                        summary="all child tasks reached done",
                    )
                    conn.commit()
                    store.append_event(
                        run_id, "run.kanban_root_closed",
                        f"root {root_id} collected (all children done)",
                    )
                except Exception:
                    pass
        finally:
            conn.close()

    def _build_evidence(
        self,
        spec,
        root_id: str,
        child_ids: list[str],
        node_kinds: dict,
        reviewer_profile: str,
    ) -> ExecutionEvidence:
        kb = self._kb()
        conn = self._conn()
        tasks: list[TaskEvidence] = []
        try:
            for tid in child_ids:
                t = kb.get_task(conn, tid)
                if t is None:
                    tasks.append(
                        TaskEvidence(task_id=tid, terminal_status="missing")
                    )
                    continue
                rec = self.state.latest_run_for_task(self.board, tid)
                run_id = self._int_run_id(rec, t)
                pid = rec.pid if rec else None
                status = getattr(t, "status", "") or ""
                kind = node_kinds.get(tid, "")
                is_review = kind in ("review", "verification") or status in _REVIEW_STATUSES
                task_result = getattr(t, "result", "") or ""
                latest_upstream_run = None
                latest_run_fn = getattr(kb, "latest_run", None)
                if callable(latest_run_fn):
                    try:
                        latest_upstream_run = latest_run_fn(conn, tid)
                    except Exception:
                        latest_upstream_run = None
                run_summary = (
                    getattr(latest_upstream_run, "summary", "") or ""
                    if latest_upstream_run is not None
                    else ""
                )
                run_outcome = (
                    getattr(latest_upstream_run, "outcome", "") or ""
                    if latest_upstream_run is not None
                    else ""
                )
                # Upstream Hermes documents task_runs.summary as the structured
                # handoff consumed by downstream workers. A successful summary
                # is durable result evidence when the optional task.result field
                # is empty; failed/blocked attempt summaries never qualify.
                result = task_result or (
                    run_summary
                    if status == "done" and run_outcome == "completed"
                    else ""
                )
                evidence_status = status
                block_reason = run_summary if status == "blocked" else ""
                artifacts = self._artifacts_for(kb, conn, tid)
                if (
                    status == "done"
                    and kind in {"work", "rework"}
                    and getattr(t, "workspace_kind", "") == "worktree"
                ):
                    valid, git_reason, git_artifacts = validate_git_result(t, result)
                    if valid:
                        artifacts.extend(git_artifacts)
                    else:
                        evidence_status = "failed"
                        block_reason = git_reason
                        result = ""
                tasks.append(
                    TaskEvidence(
                        task_id=tid,
                        assignee=getattr(t, "assignee", "") or "",
                        terminal_status=evidence_status,
                        run_id=run_id,
                        pid=pid,
                        result=result,
                        artifacts=artifacts,
                        is_review=is_review,
                        reviewed_by=(getattr(t, "assignee", "") or "") if is_review else "",
                        review_verdict=self._review_verdict(result) if is_review else "",
                        block_kind=getattr(t, "block_kind", "") or "",
                        block_reason=block_reason,
                        kind=kind,
                        is_root=(kind == "final"),
                    )
                )
        finally:
            conn.close()
        completed_reviews = [
            task
            for task in tasks
            if task.is_review and task.terminal_status == "done"
        ]
        review_count = sum(1 for task in tasks if task.kind == "review")
        latest_verdict = completed_reviews[-1].review_verdict if completed_reviews else ""
        internal_gate_hold = (
            not completed_reviews
            or (
                latest_verdict == "reject"
                and review_count < int(spec.budgets.max_review_cycles)
            )
        )
        if internal_gate_hold:
            for task in tasks:
                if task.kind == "gate" and task.terminal_status == "blocked":
                    task.block_kind = "hca_review_gate"
                    task.block_reason = "awaiting an accepted independent review"
        return ExecutionEvidence(
            root_task_id=root_id,
            tasks=tasks,
            live_worker_task_ids=self._live_owned_worker_tasks(child_ids),
        )

    @staticmethod
    def _review_verdict(result: str) -> str:
        first = next((line.strip() for line in (result or "").splitlines() if line.strip()), "")
        normalized = first.upper().strip("`* ")
        if normalized == "HCA_REVIEW: ACCEPT":
            return "accept"
        if normalized == "HCA_REVIEW: REJECT":
            return "reject"
        return "malformed" if result else ""

    @staticmethod
    def _int_run_id(rec, task) -> Optional[int]:
        # Prefer the integer run id captured at spawn (HCA state), fall back to
        # the task's live current_run_id if the mapping is missing.
        if rec is not None and rec.run_id:
            try:
                return int(rec.run_id)
            except (TypeError, ValueError):
                pass
        crid = getattr(task, "current_run_id", None)
        if crid is not None:
            try:
                return int(crid)
            except (TypeError, ValueError):
                return None
        return None

    def _artifacts_for(
        self, kb, conn: sqlite3.Connection, task_id: str
    ) -> list[Artifact]:
        arts: list[Artifact] = []
        try:
            rows = kb.list_attachments(conn, task_id)
        except (AttributeError, sqlite3.Error):
            rows = []
        for row in rows:
            arts.append(
                Artifact(
                    name=getattr(row, "filename", "") or "",
                    kind="kanban",
                    ref=getattr(row, "stored_path", "") or "",
                    task_id=task_id,
                )
            )
        return arts

    # -- cancellation ------------------------------------------------------

    def recover(
        self,
        spec,
        store: RunStore,
        task_id: str,
        *,
        reassign_profile: str = "",
        idempotency_key: str,
    ) -> dict:
        """Reclaim one exact owned attempt without consuming task retries."""
        if not idempotency_key:
            raise ValueError("recovery idempotency_key is required")
        mapping = self._mapping(store, spec.run_id)
        child_ids = set((mapping or {}).get("child_task_ids") or [])
        if task_id not in child_ids:
            raise ValueError(f"task {task_id} is not owned by run {spec.run_id}")
        digest = hashlib.sha256(
            f"{spec.run_id}\0{task_id}\0{idempotency_key}".encode()
        ).hexdigest()
        replay_key = f"recovery_result:{digest}"
        cached = self.state.get_meta(replay_key, "")
        if cached:
            replay = json.loads(cached)
            replay["idempotent_replay"] = True
            return replay

        from hca.routing import concrete_slots

        valid_profiles = {slot.profile for slot in concrete_slots(self.cfg)}
        if reassign_profile and reassign_profile not in valid_profiles:
            raise ValueError(
                f"reassign profile {reassign_profile!r} is not an existing fleet slot"
            )
        budget_key = f"supervisor_replacements:{spec.run_id}"
        used = int(self.state.get_meta(budget_key, "0") or "0")
        limit = max(0, int(spec.budgets.max_supervisor_replacements))
        if used >= limit:
            raise RuntimeError(f"supervisor replacement budget exhausted ({used}/{limit})")

        rec = self.state.latest_run_for_task(self.board, task_id)
        if rec is None or rec.status != "running":
            raise RuntimeError(f"task {task_id} has no active HCA-owned attempt to recover")
        self.state.set_activity(
            kind="recovery.requested",
            message=f"exact supervisor replacement requested for {task_id}",
            board=self.board,
            task_id=task_id,
            run_id=rec.run_id,
            slot=rec.slot,
            data={
                "owner_run_id": spec.run_id,
                "termination_class": "supervisor_replace",
                "old_profile": rec.slot,
                "new_profile": reassign_profile or rec.slot,
            },
        )
        outcome = self._terminate_process_group(
            rec.pid,
            expected_start_ticks=rec.pid_start_ticks,
        )
        if outcome in {"no_pid", "identity_unverified", "survived_escalation"}:
            raise RuntimeError(f"exact recovery refused: {outcome}")

        def already_gone(_pid: int, _signal: int) -> None:
            raise ProcessLookupError

        kb = self._kb()
        conn = self._conn()
        try:
            task = kb.get_task(conn, task_id)
            if task is None or getattr(task, "session_id", None) != spec.run_id:
                raise ValueError(f"task {task_id} no longer belongs to run {spec.run_id}")
            if not kb.reclaim_task(
                conn,
                task_id,
                reason="HCA exact supervisor replacement",
                signal_fn=already_gone,
            ):
                raise RuntimeError(f"task {task_id} was not reclaimable")
            if reassign_profile and not kb.assign_task(conn, task_id, reassign_profile):
                raise RuntimeError(f"could not reassign {task_id} to {reassign_profile}")
            conn.commit()
        finally:
            conn.close()

        from hca.leases import release_worker_lease

        self.state.mark_run_status(
            self.board,
            rec.run_id,
            "supervisor_replaced",
            error="exact HCA supervisor replacement",
        )
        release_worker_lease(self.state, board=self.board, task_id=task_id)
        self.state.release_leases_by_owner(task_id, kind="subagent")
        used += 1
        self.state.set_meta(budget_key, str(used))
        result = {
            "task_id": task_id,
            "old_profile": rec.slot,
            "new_profile": reassign_profile or rec.slot,
            "old_attempt_run_id": rec.run_id,
            "termination": outcome,
            "replacement_number": used,
            "replacement_limit": limit,
            "workspace": rec.workspace or "",
            "idempotent_replay": False,
        }
        self.state.set_meta(replay_key, json.dumps(result, sort_keys=True))
        self.state.set_activity(
            kind="recovery.completed",
            message=f"exact supervisor replacement completed for {task_id}",
            board=self.board,
            task_id=task_id,
            run_id=rec.run_id,
            slot=reassign_profile or rec.slot,
            data={**result, "owner_run_id": spec.run_id},
        )
        store.append_event(
            spec.run_id,
            "run.recovery",
            f"recovered exact task attempt {task_id}",
            result,
        )
        return result

    def cancel(self, spec, store: RunStore) -> str:
        """Stop exact worker groups and block their upstream branches.

        PID/start-tick identity is mandatory before signalling. A live legacy
        row without that identity, or a process group that survives escalation,
        leaves the run visibly blocked instead of fabricating cancellation.
        """
        return self._stop_owned_workers(
            spec,
            store,
            task_reason="run cancelled by operator",
            run_error="stopped by operator",
            event_kind="run.cancel",
            action="cancelled",
        )

    def expire(self, spec, store: RunStore) -> str:
        """Stop exact worker groups because the immutable wall deadline elapsed."""
        return self._stop_owned_workers(
            spec,
            store,
            task_reason="run wall-time deadline exhausted",
            run_error="stopped at wall-time deadline",
            event_kind="run.deadline_cleanup",
            action="expired",
        )

    def _stop_owned_workers(
        self,
        spec,
        store: RunStore,
        *,
        task_reason: str,
        run_error: str,
        event_kind: str,
        action: str,
    ) -> str:
        mapping = self._mapping(store, spec.run_id)
        if not mapping:
            return f"no Kanban work to {action}"
        board = self.board
        child_ids = list(mapping.get("child_task_ids") or [])
        root_id = mapping.get("root_task_id", "")
        all_ids = child_ids + ([root_id] if root_id else [])

        stopped = 0
        outcomes: list[str] = []
        unsafe: dict[str, str] = {}
        for tid in all_ids:
            rec = self.state.latest_run_for_task(board, tid)
            if rec is None or rec.status != "running":
                continue
            out = self._terminate_process_group(
                rec.pid,
                expected_start_ticks=rec.pid_start_ticks,
            )
            outcomes.append(f"{tid}:{out}")
            if out in {"no_pid", "identity_unverified", "survived_escalation"}:
                unsafe[tid] = out
                self.state.set_activity(
                    kind="run.cancel_incomplete",
                    message=f"worker {tid} cancellation incomplete: {out}",
                    board=board,
                    task_id=tid,
                    run_id=rec.run_id,
                    slot=rec.slot,
                )
                continue
            stopped += 1
            self.state.mark_run_status(
                board, rec.run_id, "cancelled", error=run_error
            )

        kb = self._kb()
        conn = self._conn()
        blocked = 0
        try:
            for tid in child_ids:
                task = kb.get_task(conn, tid)
                status = getattr(task, "status", "") if task else ""
                # Ready work is always safe to block. A running task is blocked
                # only after its exact process reached an observed outcome.
                if status == "ready" or (status == "running" and tid not in unsafe):
                    if kb.block_task(conn, tid, reason=task_reason):
                        blocked += 1
            conn.commit()
        finally:
            conn.close()

        from hca.leases import release_worker_lease

        for tid in all_ids:
            if tid in unsafe:
                continue
            release_worker_lease(self.state, board=board, task_id=tid)
            self.state.release_leases_by_owner(tid, kind="subagent")

        store.append_event(
            spec.run_id,
            event_kind,
            f"observed {stopped} worker group(s), blocked {blocked} task(s)",
            {"outcomes": outcomes, "unsafe": unsafe},
        )
        if unsafe:
            raise RuntimeError(
                "owned workers did not reach an observed terminal outcome: "
                + ", ".join(f"{tid}:{reason}" for tid, reason in unsafe.items())
            )
        return (
            f"{action}: observed {stopped} worker process group(s), "
            f"released {blocked} Kanban claim(s); partial work preserved"
        )

    def _terminate_process_group(
        self,
        pid: Optional[int],
        *,
        expected_start_ticks: Optional[int],
        grace: float = 2.0,
    ) -> str:
        """TERM/KILL only a matching PID/start-tick process group."""
        import os
        import signal

        if not pid or pid <= 0:
            return "no_pid"
        current_ticks = proc_start_ticks(pid)
        if expected_start_ticks is None:
            return "already_gone" if current_ticks is None else "identity_unverified"
        if current_ticks != expected_start_ticks:
            return "identity_mismatch"
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return "already_gone"
        except PermissionError:
            return "identity_unverified"
        # Close the getpgid→kill race: never signal if the PID changed identity.
        if not process_identity_matches(pid, expected_start_ticks):
            return "identity_mismatch"
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return "already_gone"
        except PermissionError:
            return "identity_unverified"
        if self._wait_group_gone(pgid, grace):
            return "terminated"
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return "terminated"
        except PermissionError:
            return "survived_escalation"
        return "killed" if self._wait_group_gone(pgid, 1.0) else "survived_escalation"

    @staticmethod
    def _wait_pid_gone(
        pid: int, timeout: float, expected_start_ticks: int | None = None
    ) -> bool:
        """Compatibility helper: wait until this exact PID identity is gone."""
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            if expected_start_ticks is not None:
                if not process_identity_matches(pid, expected_start_ticks):
                    return True
            elif proc_start_ticks(pid) is None:
                return True
            time.sleep(0.05)
        if expected_start_ticks is not None:
            return not process_identity_matches(pid, expected_start_ticks)
        return proc_start_ticks(pid) is None

    @staticmethod
    def _wait_group_gone(pgid: int, timeout: float) -> bool:
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            if not process_group_alive(pgid):
                return True
            time.sleep(0.05)
        return not process_group_alive(pgid)

    # -- projection/input ---------------------------------------------------

    def _sync_questions_from_evidence(
        self, spec, store: RunStore, evidence: ExecutionEvidence
    ) -> int:
        if spec.input_policy == "fail_closed":
            return 0
        existing = {q.task_id for q in store.open_questions(spec.run_id) if q.task_id}
        added = 0
        for task in evidence.tasks:
            if (
                task.terminal_status == "blocked"
                and task.block_kind == "needs_input"
                and task.task_id not in existing
            ):
                store.add_question(
                    spec.run_id,
                    task.block_reason or f"task {task.task_id} requires operator input",
                    task_id=task.task_id,
                )
                existing.add(task.task_id)
                added += 1
        return added

    def sync_questions(self, spec, store: RunStore) -> int:
        """Mirror real ``needs_input`` Kanban blocks into durable HCA questions."""
        evidence = self.project(spec, store)
        if evidence is None:
            return 0
        return self._sync_questions_from_evidence(spec, store, evidence)

    def respond(self, spec, task_id: str, answer: str) -> bool:
        """Record an operator answer on the owning task and release that branch."""
        if not task_id:
            raise ValueError("question is not linked to an upstream task")
        kb = self._kb()
        conn = self._conn()
        try:
            task = kb.get_task(conn, task_id)
            if task is None or getattr(task, "session_id", None) != spec.run_id:
                raise ValueError(f"task {task_id} is not owned by run {spec.run_id}")
            kb.add_comment(
                conn,
                task_id,
                author="hca-operator",
                body=f"Operator response: {answer}",
            )
            released = bool(kb.unblock_task(conn, task_id))
            conn.commit()
        finally:
            conn.close()
        if released:
            # Resume only the matching branch. A durable fleet supervisor may
            # also pick it up; this immediate tick makes `respond` useful when
            # no daemon was already running. Dispatch remains reservation-first.
            try:
                self._dispatch_tick(
                    1,
                    [task_id],
                    max_in_progress=max(1, int(spec.concurrency or 1)),
                    owner_run_id=spec.run_id,
                    max_supervisor_replacements=spec.budgets.max_supervisor_replacements,
                    requested_disk_mb=spec.budgets.max_disk_mb,
                    remaining_wall_seconds=self._remaining_wall_seconds(spec),
                )
            except Exception:
                # The answer and unblock are durable. Admission/supervisor
                # reconciliation will retry without duplicating the question.
                pass
        return released

    def project(self, spec, store: RunStore) -> Optional[ExecutionEvidence]:
        """Rebuild evidence from current upstream truth without dispatching.

        Used by ``status``/``collect`` so the HCA run projection reflects the
        live Kanban board, not a stale HCA-only enum.
        """
        mapping = self._mapping(store, spec.run_id)
        if not mapping:
            return None
        return self._build_evidence(
            spec,
            mapping["root_task_id"],
            list(mapping.get("child_task_ids") or []),
            mapping.get("node_kinds") or {},
            mapping.get("reviewer_profile", ""),
        )

    @staticmethod
    def _mapping(store: RunStore, run_id: str) -> Optional[dict]:
        for e in reversed(store.list_events(run_id)):
            if e["kind"] == "run.kanban_root":
                return e["data"]
        return None
