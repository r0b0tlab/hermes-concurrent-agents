"""Kanban integration: tmux-backed spawn_fn for Hermes dispatch_once."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from hca.config import FleetConfig
from hca.hermes_compat import (
    assert_dispatch_contract,
    import_kanban_db,
    worker_command,
)
from hca.logs import log_path
from hca.observe import slot_name
from hca.state import RunRecord, StateDB
from hca.tmux import TmuxManager, sanitize_session_name


def _open_kanban_conn(board: Optional[str] = None) -> sqlite3.Connection:
    kb = import_kanban_db()
    path = kb.kanban_db_path(board=board)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def pick_idle_slot(
    cfg: FleetConfig,
    state: StateDB,
    role_cursors: dict[str, int],
    assignee: str,
) -> Optional[str]:
    """Round-robin an idle slot for the assignee's role; None if all are busy.

    Never returns a slot with a live run: respawning a busy pane would kill the
    worker running in it.
    """
    # assignee may be profile name like coder-worker or hca-fleet-coder-01
    role = _role_from_assignee(assignee)
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


def make_tmux_spawn_fn(cfg: FleetConfig, state: StateDB, tmux: TmuxManager):
    """Return spawn_fn(task, workspace, board=None) -> pid for Hermes dispatcher."""

    role_cursors: dict[str, int] = {}

    def spawn_fn(task, workspace: str, board: Optional[str] = None) -> Optional[int]:
        board = board or cfg.board
        assignee = getattr(task, "assignee", None) or ""
        task_id = getattr(task, "id", "") or ""
        run_id = getattr(task, "run_id", None) or f"run-{task_id}-{int(time.time())}"
        # Hermes may set active_run_id on task after claim — try common attrs
        for attr in ("active_run_id", "run_id", "claim_run_id"):
            v = getattr(task, attr, None)
            if v:
                run_id = str(v)
                break

        slot = pick_idle_slot(cfg, state, role_cursors, assignee)
        if slot is None:
            state.set_activity(
                kind="admission.wait",
                message=f"no idle {_role_from_assignee(assignee)} slot for {task_id}; task stays queued",
                board=str(board),
                task_id=str(task_id),
            )
            return None
        # Defensive: a stale 'running' row on this slot (crash before reconcile)
        # would violate the live-slot unique index on insert.
        for stale in state.list_runs(status="running"):
            if stale.slot == slot:
                state.mark_run_status(
                    stale.board, stale.run_id, "superseded", error="slot reused by new spawn"
                )
        # Prefer concrete profile if assignee looks like a profile
        profile = assignee if assignee.startswith("hca-") else f"hca-{cfg.name}-{_role_from_assignee(assignee)}-01"
        # If assignee is already a full profile name from hermes, use it
        if assignee and not assignee.startswith("hca-") and Path(
            os.path.expanduser(f"~/.hermes/profiles/{assignee}")
        ).is_dir():
            profile = assignee

        kb = import_kanban_db()
        kanban_db = str(kb.kanban_db_path(board=board))
        workspaces_root = str(Path(workspace).expanduser().resolve().parent)

        # HERMES_HOME stays untouched: it is the ~/.hermes config root, and the
        # worker is addressed by profile via `hermes -p <profile>`.
        env = {
            "HERMES_PROFILE": profile,
            "HERMES_KANBAN_TASK": str(task_id),
            "HERMES_KANBAN_RUN_ID": str(run_id),
            "HERMES_KANBAN_CLAIM_LOCK": str(getattr(task, "claim_lock", "") or ""),
            "HERMES_KANBAN_BOARD": str(board),
            "HERMES_KANBAN_DB": kanban_db,
            "HERMES_KANBAN_WORKSPACE": str(workspace),
            "HERMES_KANBAN_WORKSPACES_ROOT": workspaces_root,
            "HCA_STATE_DB": str(Path(cfg.state_dir) / "hca.sqlite"),
            "HCA_MAX_SUBAGENT_CREDITS": str(cfg.delegation_max_children),
        }
        # claim lock may live on task object under different names
        for attr in ("claim_lock", "lock_token", "worker_lock"):
            v = getattr(task, attr, None)
            if v:
                env["HERMES_KANBAN_CLAIM_LOCK"] = str(v)

        cmd = worker_command(profile, str(task_id), yolo=cfg.approvals_yolo)
        pid = tmux.run_in_slot(
            slot,
            cmd,
            env=env,
            workdir=str(workspace) if workspace else None,
            log_path=str(log_path(cfg.state_dir, str(run_id))),
        )
        now = time.time()
        state.upsert_run(
            RunRecord(
                board=str(board),
                task_id=str(task_id),
                run_id=str(run_id),
                slot=slot,
                node="local",
                tmux_session=sanitize_session_name(slot),
                pid=pid,
                hermes_session_id=None,
                workspace=str(workspace) if workspace else None,
                status="running",
                started_at=now,
                updated_at=now,
                last_activity="spawned",
                error=None,
            )
        )
        state.set_activity(
            kind="run.start",
            message=f"spawned {task_id} on {slot} pid={pid}",
            board=str(board),
            task_id=str(task_id),
            run_id=str(run_id),
            slot=slot,
        )
        return pid

    return spawn_fn


def _role_from_assignee(assignee: str) -> str:
    a = (assignee or "").lower()
    for key in ("orchestrator", "coder", "research", "qa", "creative"):
        if key in a:
            return key
    return "coder"


def dispatch_tick(cfg: FleetConfig, state: StateDB, tmux: TmuxManager, **kwargs) -> dict[str, Any]:
    """One Hermes dispatcher tick with HCA tmux spawn_fn."""
    assert_dispatch_contract()
    kb = import_kanban_db()
    conn = _open_kanban_conn(cfg.board)
    try:
        spawn_fn = make_tmux_spawn_fn(cfg, state, tmux)
        result = kb.dispatch_once(
            conn,
            spawn_fn=spawn_fn,
            board=cfg.board,
            max_spawn=kwargs.get("max_spawn", cfg.capacity.max_wave_size),
            max_in_progress=kwargs.get(
                "max_in_progress", cfg.capacity.max_top_level_runs
            ),
            dry_run=kwargs.get("dry_run", False),
        )
        return {
            "reclaimed": result.reclaimed,
            "promoted": result.promoted,
            "spawned": list(result.spawned),
            "skipped_unassigned": list(result.skipped_unassigned),
            "skipped_nonspawnable": list(result.skipped_nonspawnable),
            "crashed": list(result.crashed),
            "skipped_locked": bool(result.skipped_locked),
        }
    finally:
        conn.close()
