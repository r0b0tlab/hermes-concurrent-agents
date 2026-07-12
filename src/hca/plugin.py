"""Hermes plugin: subagent budget gate + activity telemetry (no-op without HCA_STATE_DB)."""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Optional


def _state_db():
    path = os.environ.get("HCA_STATE_DB")
    if not path:
        return None
    try:
        from hca.state import StateDB

        return StateDB(path)
    except Exception:
        return None


def _max_children() -> float:
    try:
        return float(os.environ.get("HCA_MAX_SUBAGENT_CREDITS", "2"))
    except ValueError:
        return 2.0


def register(ctx: Any = None) -> None:
    """
    Hermes plugin entrypoint.
    Supports both context-API plugins and no-arg discovery.
    """
    # Prefer hooks if ctx provides them; otherwise define module-level functions
    # that hermes may import by convention.
    if ctx is not None and hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_tool_call", on_pre_tool_call)
        ctx.register_hook("subagent_start", on_subagent_start)
        ctx.register_hook("subagent_stop", on_subagent_stop)
        ctx.register_hook("on_session_end", on_session_end)


def on_pre_tool_call(tool_name: str, args: dict, **kwargs) -> Optional[dict]:
    if tool_name != "delegate_task":
        return None
    db = _state_db()
    if db is None:
        return None
    tasks = args.get("tasks")
    n = len(tasks) if isinstance(tasks, list) and tasks else 1
    credits = float(n)
    used = db.active_lease_credits(kind="subagent")
    limit = _max_children()
    if used + credits > limit:
        msg = (
            f"HCA subagent budget exceeded ({used + credits:.0f}/{limit:.0f}). "
            "Create durable Kanban child tasks or continue sequentially."
        )
        db.set_activity(kind="admission.wait", message=msg)
        return {"block": True, "message": msg}
    # reserve provisional leases
    for i in range(n):
        db.acquire_lease(
            lease_id=f"subagent-reserve-{uuid.uuid4().hex[:12]}",
            kind="subagent",
            owner=os.environ.get("HERMES_KANBAN_TASK", "session"),
            credits=1.0,
            ttl_seconds=600,
            meta={"phase": "reserve"},
        )
    return None


def on_subagent_start(subagent_id: str = "", **kwargs) -> None:
    db = _state_db()
    if db is None:
        return
    db.set_activity(
        kind="subagent.start",
        message=f"subagent {subagent_id} start",
        task_id=os.environ.get("HERMES_KANBAN_TASK", ""),
        run_id=os.environ.get("HERMES_KANBAN_RUN_ID", ""),
        data={"subagent_id": subagent_id},
    )


def on_subagent_stop(subagent_id: str = "", **kwargs) -> None:
    db = _state_db()
    if db is None:
        return
    # release one subagent credit (best-effort: drop oldest reserve/active)
    try:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT lease_id FROM leases WHERE kind='subagent' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row:
                conn.execute("DELETE FROM leases WHERE lease_id=?", (row["lease_id"],))
    except Exception:
        pass
    db.set_activity(
        kind="subagent.stop",
        message=f"subagent {subagent_id} stop",
        task_id=os.environ.get("HERMES_KANBAN_TASK", ""),
        run_id=os.environ.get("HERMES_KANBAN_RUN_ID", ""),
        data={"subagent_id": subagent_id},
    )


def on_session_end(**kwargs) -> None:
    db = _state_db()
    if db is None:
        return
    owner = os.environ.get("HERMES_KANBAN_TASK", "")
    try:
        with db.connection() as conn:
            if owner:
                conn.execute("DELETE FROM leases WHERE owner=?", (owner,))
            # also expire any timed-out leases
            conn.execute(
                "DELETE FROM leases WHERE expires_at IS NOT NULL AND expires_at < ?",
                (time.time(),),
            )
    except Exception:
        pass
