"""Kanban integration: tmux-backed spawn_fn for Hermes dispatch_once."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from hca.config import FleetConfig
from hca.hermes_compat import (
    HermesCompatError,
    assert_dispatch_contract,
    import_kanban_db,
    worker_command,
)
from hca.observe import slot_name
from hca.state import RunRecord, StateDB
from hca.tmux import TmuxManager, sanitize_session_name


def _open_kanban_conn(board: Optional[str] = None) -> sqlite3.Connection:
    kb = import_kanban_db()
    path = kb.kanban_db_path(board=board)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def make_tmux_spawn_fn(cfg: FleetConfig, state: StateDB, tmux: TmuxManager):
    """Return spawn_fn(task, workspace, board=None) -> pid for Hermes dispatcher."""

    # round-robin empty slots by role
    role_cursors: dict[str, int] = {}

    def _pick_slot(assignee: str) -> str:
        # assignee may be profile name like coder-worker or hca-fleet-coder-01
        role = "coder"
        a = (assignee or "").lower()
        for key in ("orchestrator", "coder", "research", "qa", "creative"):
            if key in a:
                role = key
                break
        n = int(cfg.profile_slots.get(role, 1) or 1)
        cur = role_cursors.get(role, 0)
        idx = (cur % n) + 1
        role_cursors[role] = cur + 1
        return slot_name(cfg.name, role, idx)

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

        slot = _pick_slot(assignee)
        # Prefer concrete profile if assignee looks like a profile
        profile = assignee if assignee.startswith("hca-") else f"hca-{cfg.name}-{_role_from_assignee(assignee)}-01"
        # If assignee is already a full profile name from hermes, use it
        if assignee and not assignee.startswith("hca-") and Path(
            os.path.expanduser(f"~/.hermes/profiles/{assignee}")
        ).is_dir():
            profile = assignee

        hermes_home = os.path.expanduser(f"~/.hermes/profiles/{profile}")
        if not Path(hermes_home).is_dir():
            # fall back to default hermes home with -p profile
            hermes_home = os.path.expanduser("~/.hermes")

        kb = import_kanban_db()
        kanban_db = str(kb.kanban_db_path(board=board))
        workspaces_root = str(Path(workspace).expanduser().resolve().parent)

        env = {
            "HERMES_HOME": hermes_home,
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
        pid = tmux.run_in_slot(slot, cmd, env=env, workdir=str(workspace) if workspace else None)
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
