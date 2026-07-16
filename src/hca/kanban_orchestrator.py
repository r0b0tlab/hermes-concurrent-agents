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
import time
from typing import Callable, Optional

from hca.config import FleetConfig
from hca.decompose import TaskNode, validate_task_graph
from hca.evidence import (
    TERMINAL_TASK_STATUSES,
    ExecutionEvidence,
    TaskEvidence,
)
from hca.hermes_compat import HermesCompatError, import_kanban_db
from hca.result import Artifact
from hca.routing import planner_slots, reviewer_slots, worker_slots
from hca.run import RunState, RunStore
from hca.state import StateDB

PlannerFn = Callable[["FleetConfig", object, str, str], list[TaskNode]]

# Upstream review-column status → an independent reviewer ran.
_REVIEW_STATUSES = frozenset({"review"})


def default_planner(
    cfg: FleetConfig, spec, planner: str, worker: str
) -> list[TaskNode]:
    """Minimal bounded decomposition: one work task + a final collection task.

    Deliberately conservative — a one-step goal uses exactly one *execution*
    worker after planning; the final node is planner-owned collection, not an
    execution worker. Richer team presets replace this without changing the
    barrier or the dispatch contract.
    """
    acceptance = tuple(spec.acceptance_criteria) or ("goal addressed",)
    return [
        TaskNode(
            id="work",
            title=f"Implement: {spec.goal[:80]}",
            role_hint="worker",
            scope=spec.goal[:400] or "the run goal",
            acceptance_criteria=acceptance,
            expected_artifacts=("result summary",),
            kind="work",
        ),
        TaskNode(
            id="final",
            title="Collect and finalize the run result",
            role_hint="planner",
            depends_on=("work",),
            scope="aggregate task outputs into the final run result",
            kind="final",
        ),
    ]


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
        max_wall_seconds: float = 90.0,
        enforce_sole_dispatcher: bool = True,
    ):
        self.cfg = cfg
        self.board = board or cfg.board
        from pathlib import Path

        state_dir = Path(cfg.state_dir or "~/.hca").expanduser()
        self.state = state or StateDB(state_dir / "hca.sqlite")
        self._tmux = tmux
        self.planner_fn = planner_fn
        self.max_ticks = max_ticks
        self.poll_interval = poll_interval
        self.max_wall_seconds = max_wall_seconds
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

    def _profile_for_hint(self, hint: str, planner: str, worker: str) -> str:
        hint = (hint or "").lower()
        if hint in ("planner", "orchestrator", "final"):
            return planner
        if hint in ("reviewer", "review", "qa"):
            return self._reviewer_profile()
        return worker

    # -- plan --------------------------------------------------------------

    def plan(self, spec, store: RunStore) -> RunState:
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

        kb = self._kb()
        conn = self._conn()
        try:
            root_id = kb.create_task(
                conn,
                title=f"[HCA run] {spec.goal[:120]}",
                body=spec.goal,
                assignee=planner,
                created_by="hca",
                triage=True,
                board=self.board,
                session_id=spec.run_id,
            )
            children = self._nodes_to_children(nodes, planner, worker)
            child_ids = kb.decompose_triage_task(
                conn,
                root_id,
                root_assignee=planner,
                children=children,
                author=planner,
            )
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
        for n in nodes:
            parents = [index[d] for d in n.depends_on if d in index]
            children.append(
                {
                    "title": n.title,
                    "body": (
                        f"{n.scope}\n\nacceptance: "
                        f"{'; '.join(n.acceptance_criteria) or 'n/a'}"
                    ),
                    "assignee": self._profile_for_hint(n.role_hint, planner, worker),
                    "parents": parents,
                }
            )
        return children

    # -- execute -----------------------------------------------------------

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

        deadline = time.time() + min(
            float(spec.budgets.wall_seconds or self.max_wall_seconds),
            self.max_wall_seconds,
        )
        wave = max(1, int(spec.concurrency or 1))

        ticks = 0
        while ticks < self.max_ticks and time.time() < deadline:
            ticks += 1
            try:
                self._dispatch_tick(wave)
            except HermesCompatError as exc:
                return ExecutionEvidence(
                    root_task_id=root_id,
                    reason=f"dispatch failed: {exc}",
                )
            statuses = self._statuses(child_ids)
            if statuses and all(
                s in TERMINAL_TASK_STATUSES for s in statuses.values()
            ):
                break
            time.sleep(self.poll_interval)

        self._maybe_close_root(root_id, child_ids, store, spec.run_id)
        return self._build_evidence(
            spec, root_id, child_ids, node_kinds, reviewer_profile
        )

    def _dispatch_tick(self, wave: int) -> None:
        from hca.kanban import dispatch_tick
        from hca.tmux import TmuxManager

        tmux = self._tmux or TmuxManager(socket=f"hca-{self.cfg.name}")
        dispatch_tick(
            self.cfg,
            self.state,
            tmux,
            max_spawn=wave,
            max_in_progress_per_profile=1,
            skip_sole_dispatcher_check=not self.enforce_sole_dispatcher,
        )

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
                tasks.append(
                    TaskEvidence(
                        task_id=tid,
                        assignee=getattr(t, "assignee", "") or "",
                        terminal_status=status,
                        run_id=run_id,
                        pid=pid,
                        result=getattr(t, "result", "") or "",
                        artifacts=self._artifacts_for(conn, tid),
                        is_review=is_review,
                        reviewed_by=(getattr(t, "assignee", "") or "") if is_review else "",
                        is_root=(kind == "final"),
                    )
                )
        finally:
            conn.close()
        return ExecutionEvidence(root_task_id=root_id, tasks=tasks)

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

    def _artifacts_for(self, conn: sqlite3.Connection, task_id: str) -> list[Artifact]:
        arts: list[Artifact] = []
        try:
            rows = conn.execute(
                "SELECT filename, stored_path FROM task_attachments WHERE task_id=?",
                (task_id,),
            ).fetchall()
        except sqlite3.Error:
            rows = []
        for r in rows:
            arts.append(
                Artifact(
                    name=r["filename"],
                    kind="kanban",
                    ref=r["stored_path"],
                    task_id=task_id,
                )
            )
        return arts

    # -- projection (status/collect reconciliation) ------------------------

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
