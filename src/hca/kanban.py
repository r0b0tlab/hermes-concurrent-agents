"""Kanban integration: reservation-first tmux spawn_fn for dispatch_once.

Dispatch order (Task 3): assert sole dispatcher → reconcile stale slots →
pre-reserve concrete slots for ready concretely-assigned tasks → call
upstream ``dispatch_once`` with ``max_in_progress_per_profile=1`` → the
spawn callback launches its *pre-reserved* slot or raises → release unused
reservations. The spawn callback makes no admission decision and never
returns ``None`` after a claim (which upstream would record as an invisible
stuck ``spawned`` row).
"""

from __future__ import annotations

import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from hca.config import FleetConfig
from hca.hermes_compat import (
    assert_dispatch_contract,
    assert_sole_dispatcher,
    import_kanban_db,
)
from hca.leases import acquire_worker_lease, release_worker_lease
from hca.logs import log_path, worker_log_id
from hca.observe import slot_name
from hca.process_identity import proc_start_ticks
from hca.resources import admit
from hca.routing import Reservations, concrete_slots
from hca.state import RunRecord, StateDB
from hca.tmux import TmuxManager, sanitize_session_name
from hca.worker_launch import WorkerLaunchError, build_worker_launch_spec, worker_unset_env


def _open_kanban_conn(board: Optional[str] = None) -> sqlite3.Connection:
    kb = import_kanban_db()
    path = kb.kanban_db_path(board=board)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def _concrete_profiles(cfg: FleetConfig) -> set[str]:
    return {s.profile for s in concrete_slots(cfg)}


def pick_idle_slot(
    cfg: FleetConfig,
    state: StateDB,
    role_cursors: dict[str, int],
    assignee: str,
) -> Optional[str]:
    """Round-robin an idle concrete slot matching the assignee's role.

    Kept for status/diagnostics and back-compat; the dispatch path now uses
    reservation-based routing. Never returns a slot with a live run.
    """
    role = _role_from_profile(assignee)
    n = int(cfg.profile_slots.get(role, 1) or 1)
    busy = {r.slot for r in state.list_runs(status="running")}
    cur = role_cursors.get(role, 0)
    for offset in range(n):
        idx = ((cur + offset) % n) + 1
        name = slot_name(cfg.name, role, idx)
        if name not in busy:
            role_cursors[role] = cur + offset + 1
            return name
    return None


def _role_from_profile(profile: str) -> str:
    """Extract the concrete slot role from a concrete profile name.

    Only used for slot bookkeeping of *concrete* ``hca-<fleet>-<role>-NN``
    profiles. It is NOT a logical-role fallback: unknown logical assignees
    are rejected by hca.routing, not silently mapped to coder.
    """
    a = (profile or "").lower()
    for key in ("orchestrator", "general", "coder", "research", "qa", "creative"):
        if f"-{key}-" in a or a.endswith(f"-{key}"):
            return key
    # For a concrete slot we could not classify, default the *slot count*
    # lookup to coder; this does not route work, only sizes the pool.
    return "coder"


def pre_reserve_ready(
    cfg: FleetConfig,
    state: StateDB,
    conn: sqlite3.Connection,
    reservations: Reservations,
    *,
    limit: int,
    allowed_task_ids: Optional[set[str]] = None,
    requested_disk_mb: int = 0,
) -> list[str]:
    """Reserve concrete slots for ready, concretely-assigned free tasks.

    Returns the reserved profile names. Only tasks already assigned to an
    existing HCA concrete profile are reserved here; logical-role tasks are
    resolved to concrete profiles at creation time by the service layer.
    """
    concrete = _concrete_profiles(cfg)
    reserved: list[str] = []
    try:
        rows = conn.execute(
            "SELECT id, assignee FROM tasks "
            "WHERE status = 'ready' AND claim_lock IS NULL "
            "ORDER BY priority DESC, created_at ASC"
        ).fetchall()
    except sqlite3.Error:
        return reserved
    busy = reservations.busy(state)
    active_runs = state.list_runs(status="running")
    role_counts = Counter(_role_from_profile(run.slot) for run in active_runs)
    reserved_roles: Counter[str] = Counter()
    for row in rows:
        if len(reserved) >= limit:
            break
        task_id = str(row["id"])
        if allowed_task_ids is not None and task_id not in allowed_task_ids:
            continue
        assignee = (row["assignee"] or "") if isinstance(row, sqlite3.Row) else ""
        if assignee not in concrete:
            continue
        if assignee in busy:
            state.set_activity(
                kind="admission.wait",
                message=f"waiting: concrete profile {assignee} already has an active worker",
                board=cfg.board,
                task_id=str(row["id"]),
                slot=assignee,
            )
            continue
        role = _role_from_profile(assignee)
        role_cap = cfg.capacity.per_role_caps.get(role)
        if role_cap is not None and role_counts[role] + reserved_roles[role] >= role_cap:
            state.set_activity(
                kind="admission.wait",
                message=f"waiting: role {role} cap {role_cap} reached",
                board=cfg.board,
                task_id=str(row["id"]),
                slot=assignee,
            )
            continue
        decision = admit(
            cfg,
            state,
            credits=1.0 + len(reserved),
            enforce_top_level_cap=False,
            requested_disk_mb=requested_disk_mb,
        )
        if not decision.allowed:
            state.set_activity(
                kind="admission.wait",
                message=decision.reason,
                board=cfg.board,
                task_id=str(row["id"]),
                slot=assignee,
            )
            break
        reservations.reserve(assignee)
        busy.add(assignee)
        reserved.append(assignee)
        reserved_roles[role] += 1
    return reserved


def _reconcile_owned_worker_identities(
    kb: Any,
    conn: sqlite3.Connection,
    cfg: FleetConfig,
    state: StateDB,
    *,
    owner_run_id: str = "",
    max_supervisor_replacements: Optional[int] = None,
    allowed_task_ids: Optional[set[str]] = None,
) -> list[str]:
    """Reclaim dead or PID-reused exact workers before admission and claim."""

    reclaimed: list[str] = []

    def already_gone(_pid: int, _signal: int) -> None:
        # Upstream's reclaim API needs a signal seam. Identity mismatch proves
        # the recorded worker is gone, so never signal the process now at PID.
        raise ProcessLookupError

    for rec in state.list_runs(status="running"):
        if rec.board != cfg.board:
            continue
        if owner_run_id and allowed_task_ids is not None and rec.task_id not in allowed_task_ids:
            continue
        current_ticks = proc_start_ticks(rec.pid) if rec.pid else None
        if rec.pid_start_ticks is None and current_ticks is not None:
            state.set_activity(
                kind="worker.quarantined",
                message=(
                    f"worker {rec.run_id} pid={rec.pid} has no recorded start "
                    "identity; refusing to signal or replace it"
                ),
                board=rec.board,
                task_id=rec.task_id,
                run_id=rec.run_id,
                slot=rec.slot,
            )
            continue
        if rec.pid_start_ticks is not None and current_ticks == rec.pid_start_ticks:
            continue
        # Close the race where a worker commits terminal Kanban truth and exits
        # after the controller's status snapshot but before this lower dispatch
        # reconciliation. Reclaiming that already-completed task would reset it
        # to ready and create a duplicate replacement attempt.
        get_task = getattr(kb, "get_task", None)
        task = get_task(conn, rec.task_id) if callable(get_task) else None
        upstream_status = str(getattr(task, "status", "") or "")
        if upstream_status in {
            "done",
            "archived",
            "blocked",
            "failed",
            "crashed",
            "timed_out",
            "cancelled",
        }:
            mapped = (
                "completed"
                if upstream_status in {"done", "archived"}
                else upstream_status
            )
            state.mark_run_status(
                rec.board,
                rec.run_id,
                mapped,
                error=(
                    ""
                    if mapped == "completed"
                    else f"upstream task is {upstream_status}"
                ),
            )
            release_worker_lease(state, board=rec.board, task_id=rec.task_id)
            state.release_leases_by_owner(rec.task_id, kind="subagent")
            state.set_activity(
                kind="attempt.settled",
                message=(
                    f"settled dead worker {rec.run_id} from terminal upstream "
                    f"status {upstream_status}"
                ),
                board=rec.board,
                task_id=rec.task_id,
                run_id=rec.run_id,
                slot=rec.slot,
                data={
                    "owner_run_id": owner_run_id,
                    "termination_class": "task_terminal",
                    "upstream_status": upstream_status,
                },
            )
            continue
        did_reclaim = kb.reclaim_task(
            conn,
            rec.task_id,
            reason="HCA owned worker process identity no longer exists",
            signal_fn=already_gone,
        )
        if not did_reclaim:
            continue
        replacement_key = f"supervisor_replacements:{owner_run_id}"
        replacements = (
            int(state.get_meta(replacement_key, "0") or "0")
            if owner_run_id
            else 0
        )
        replacement_allowed = (
            not owner_run_id
            or max_supervisor_replacements is None
            or replacements < max(0, int(max_supervisor_replacements))
        )
        if replacement_allowed and owner_run_id:
            replacements += 1
            state.set_meta(replacement_key, str(replacements))
            state.set_activity(
                kind="recovery.supervisor_replace",
                message=(
                    f"reserved supervisor replacement {replacements}/"
                    f"{max_supervisor_replacements} for {rec.task_id}"
                ),
                board=rec.board,
                task_id=rec.task_id,
                run_id=rec.run_id,
                slot=rec.slot,
                data={
                    "owner_run_id": owner_run_id,
                    "termination_class": "supervisor_replace",
                    "replacement_number": replacements,
                },
            )
        elif owner_run_id:
            kb.block_task(
                conn,
                rec.task_id,
                reason=(
                    "HCA supervisor replacement budget exhausted "
                    f"({replacements}/{max_supervisor_replacements})"
                ),
            )
            conn.commit()
            state.set_activity(
                kind="recovery.budget_exhausted",
                message=f"replacement budget exhausted for {rec.task_id}",
                board=rec.board,
                task_id=rec.task_id,
                run_id=rec.run_id,
                slot=rec.slot,
                data={
                    "owner_run_id": owner_run_id,
                    "termination_class": "worker_crash",
                    "replacements_used": replacements,
                },
            )
        state.mark_run_status(
            rec.board,
            rec.run_id,
            "crashed",
            error="owned worker process identity no longer exists",
        )
        release_worker_lease(state, board=rec.board, task_id=rec.task_id)
        state.release_leases_by_owner(rec.task_id, kind="subagent")
        state.set_activity(
            kind="attempt.terminated",
            message=f"reclaimed dead worker {rec.run_id} pid={rec.pid}",
            board=rec.board,
            task_id=rec.task_id,
            run_id=rec.run_id,
            slot=rec.slot,
            data={
                "owner_run_id": owner_run_id,
                "termination_class": "worker_crash",
                "replacement_allowed": replacement_allowed,
            },
        )
        reclaimed.append(rec.task_id)
    return reclaimed


def make_tmux_spawn_fn(
    cfg: FleetConfig,
    state: StateDB,
    tmux: TmuxManager,
    reservations: Reservations,
    *,
    allowed_task_ids: Optional[set[str]] = None,
):
    """Return spawn_fn(task, workspace, board=None) -> int (never None).

    The callback has no admission decision left: it confirms the task's
    concrete profile was pre-reserved this tick, builds the exact worker
    launch spec, launches the reserved slot, and returns a real PID. If it
    cannot proceed it *raises* (upstream then auto-blocks the task) — it
    must never return None, which upstream records as a stuck spawned row.
    """

    def spawn_fn(task, workspace: str, board: Optional[str] = None) -> int:
        board = board or cfg.board
        assignee = getattr(task, "assignee", None) or ""
        task_id = getattr(task, "id", "") or ""

        if allowed_task_ids is not None and task_id not in allowed_task_ids:
            raise WorkerLaunchError(
                f"task {task_id} is outside the persisted HCA graph — refusing "
                "to claim or spawn it"
            )
        if not assignee:
            raise WorkerLaunchError(f"task {task_id} has no concrete assignee")
        if assignee not in reservations.reserved:
            # No admission decision here: a task reaching spawn without a
            # reservation is a routing/reservation bug. Raise so upstream
            # blocks it visibly rather than leaving an invisible claim.
            raise WorkerLaunchError(
                f"task {task_id} assignee {assignee!r} was not pre-reserved "
                "this tick — refusing to spawn (would leak an unadmitted worker)"
            )

        slot = sanitize_session_name(assignee)

        # Defensive: a stale 'running' row on this slot (crash before
        # reconcile) violates the live-slot unique index on insert.
        for stale in state.list_runs(status="running"):
            if stale.slot == slot:
                state.mark_run_status(
                    stale.board, stale.run_id, "superseded", error="slot reused by new spawn"
                )

        extra_env = {
            "HCA_STATE_DB": str(Path(cfg.state_dir) / "hca.sqlite"),
            "HCA_MAX_SUBAGENT_CREDITS": str(cfg.delegation_max_children),
        }
        # build_worker_launch_spec fails closed unless current_run_id is an
        # integer — so a missing run id raises here, before any tmux launch.
        spec = build_worker_launch_spec(
            task, str(workspace or ""), board=board, profile=assignee,
            hca_extra_env=extra_env,
        )
        run_id = spec.run_id

        pid = tmux.run_in_slot(
            slot,
            spec.command(),
            env=spec.env(),
            unset_env=worker_unset_env(),
            workdir=str(workspace) if workspace else None,
            log_path=str(
                log_path(
                    cfg.state_dir,
                    worker_log_id(str(board), str(task_id), run_id),
                )
            ),
        )
        if not pid:
            raise WorkerLaunchError(
                f"tmux returned no pid for task {task_id} on slot {slot}"
            )
        pid_start_ticks = None
        deadline = time.monotonic() + 0.5
        while pid_start_ticks is None and time.monotonic() < deadline:
            pid_start_ticks = proc_start_ticks(pid)
            if pid_start_ticks is None:
                time.sleep(0.01)
        if pid_start_ticks is None:
            # The named tmux slot is HCA-owned; terminate it rather than leave
            # an untracked process after the upstream claim has been created.
            try:
                tmux.kill_session(slot)
            except Exception:
                pass
            raise WorkerLaunchError(
                f"could not capture exact process identity for pid {pid} "
                f"task {task_id} on slot {slot}"
            )

        now = time.time()
        session_id = getattr(task, "session_id", None)
        state.upsert_run(
            RunRecord(
                board=str(board),
                task_id=str(task_id),
                run_id=str(run_id),
                slot=slot,
                node="local",
                tmux_session=slot,
                pid=pid,
                hermes_session_id=str(session_id) if session_id else None,
                workspace=str(workspace) if workspace else None,
                status="running",
                started_at=now,
                updated_at=now,
                last_activity="spawned",
                error=None,
                pid_start_ticks=pid_start_ticks,
            )
        )
        # Acquire the durable top-level lease bound to this exact worker so
        # admission (hca.resources.admit) actually counts every launched worker
        # against the sequence-credit ceiling — not just an in-memory
        # reservation. Released on terminal/crash/stop by the orchestrator.
        acquire_worker_lease(
            state,
            board=str(board),
            task_id=str(task_id),
            run_id=run_id,
            slot=slot,
            pid=pid,
            pid_start_ticks=pid_start_ticks,
            node="local",
        )
        state.set_activity(
            kind="run.start",
            message=f"spawned {task_id} on {slot} pid={pid} run={run_id}",
            board=str(board),
            task_id=str(task_id),
            run_id=str(run_id),
            slot=slot,
        )
        return pid

    return spawn_fn


def _result_to_dict(result: Any) -> dict[str, Any]:
    """Surface the full upstream DispatchResult, tolerating field drift."""

    def g(name, default):
        v = getattr(result, name, default)
        return list(v) if isinstance(v, (list, tuple)) else v

    return {
        "reclaimed": g("reclaimed", 0),
        "promoted": g("promoted", 0),
        "spawned": g("spawned", []),
        "skipped_unassigned": g("skipped_unassigned", []),
        "auto_assigned_default": g("auto_assigned_default", []),
        "skipped_nonspawnable": g("skipped_nonspawnable", []),
        "skipped_per_profile_capped": g("skipped_per_profile_capped", []),
        "crashed": g("crashed", []),
        "auto_blocked": g("auto_blocked", []),
        "timed_out": g("timed_out", []),
        "stale": g("stale", []),
        "respawn_guarded": g("respawn_guarded", []),
        "rate_limited": g("rate_limited", []),
        "skipped_locked": bool(getattr(result, "skipped_locked", False)),
    }


def dispatch_tick(
    cfg: FleetConfig, state: StateDB, tmux: TmuxManager, **kwargs
) -> dict[str, Any]:
    """One reservation-first Hermes dispatcher tick with the HCA spawn_fn."""
    assert_dispatch_contract()
    # Fail closed before any claim/spawn if a foreign gateway can own the board.
    if not kwargs.get("skip_sole_dispatcher_check"):
        assert_sole_dispatcher(cfg.board)
    kb = import_kanban_db()
    conn = _open_kanban_conn(cfg.board)
    try:
        wave = int(kwargs.get("max_spawn", cfg.capacity.max_wave_size))
        raw_allowed = kwargs.get("allowed_task_ids")
        allowed_task_ids = (
            {str(task_id) for task_id in raw_allowed}
            if raw_allowed is not None
            else None
        )
        pre_crashed = _reconcile_owned_worker_identities(
            kb,
            conn,
            cfg,
            state,
            owner_run_id=str(kwargs.get("owner_run_id") or ""),
            max_supervisor_replacements=kwargs.get("max_supervisor_replacements"),
            allowed_task_ids=allowed_task_ids,
        )
        reservations = Reservations()
        # dispatch_once promotes todo/blocked → ready *internally*; run the
        # same idempotent promotion first so HCA can see and pre-reserve the
        # tasks the tick is about to spawn.
        recompute = getattr(kb, "recompute_ready", None)
        if callable(recompute):
            try:
                recompute(conn)
            except Exception:
                pass
        pre_reserve_ready(
            cfg,
            state,
            conn,
            reservations,
            limit=wave,
            allowed_task_ids=allowed_task_ids,
            requested_disk_mb=max(0, int(kwargs.get("requested_disk_mb") or 0)),
        )
        spawn_fn = make_tmux_spawn_fn(
            cfg,
            state,
            tmux,
            reservations,
            allowed_task_ids=allowed_task_ids,
        )
        configured_cap = int(
            kwargs.get("max_in_progress", cfg.capacity.max_top_level_runs)
        )
        live_running = int(
            conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'running'").fetchone()[0]
        )
        # Never let upstream claim more tasks than HCA pre-reserved. This keeps
        # admission deferrals as ready work instead of turning them into visible
        # spawn failures after claim.
        admitted_cap = min(configured_cap, live_running + len(reservations.reserved))
        result = kb.dispatch_once(
            conn,
            spawn_fn=spawn_fn,
            board=cfg.board,
            max_spawn=len(reservations.reserved),
            max_in_progress=admitted_cap,
            max_in_progress_per_profile=kwargs.get("max_in_progress_per_profile", 1),
            dry_run=kwargs.get("dry_run", False),
        )
        # Release reservations that did not bind to a spawned run.
        spawned_profiles = {
            entry[1] for entry in getattr(result, "spawned", []) if len(entry) > 1
        }
        for profile in list(reservations.reserved):
            if profile not in spawned_profiles:
                reservations.release(profile)
        projected = _result_to_dict(result)
        projected["crashed"] = list(
            dict.fromkeys([*pre_crashed, *projected.get("crashed", [])])
        )
        projected["reclaimed"] = int(projected.get("reclaimed", 0)) + len(
            pre_crashed
        )
        return projected
    finally:
        conn.close()
