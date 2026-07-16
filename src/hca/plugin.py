"""Hermes plugin: subagent budget gate + activity telemetry (no-op without HCA_STATE_DB).

Subagents are disabled by default (``HCA_MAX_SUBAGENT_CREDITS=0``); durable
parallel work belongs in Kanban. When explicitly opted in, leases correlate by
``child_session_id`` — the only id present in *both* ``subagent_start`` and
``subagent_stop`` (stop does not carry ``child_subagent_id``). There is no
fixed 600s child expiry: a long-running child stays counted until its stop or
session cleanup, with conservative orphan reconciliation on session end.
"""

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
        return float(os.environ.get("HCA_MAX_SUBAGENT_CREDITS", "0"))
    except ValueError:
        return 0.0


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
    # Register the five scoped team tools (no unrestricted passthrough).
    # Registration failures are fatal: a loaded plugin that silently exposes
    # zero tools is more dangerous than an explicit startup/doctor failure.
    if ctx is not None:
        from hca.plugin_tools import register_tools

        register_tools(ctx)


def _parent_owner() -> str:
    return os.environ.get("HERMES_KANBAN_TASK", "") or os.environ.get(
        "HERMES_KANBAN_RUN_ID", "session"
    )


def on_pre_tool_call(tool_name: str, args: dict, **kwargs) -> Optional[dict]:
    if tool_name == "hca_team_stop":
        run_id = str((args or {}).get("run_id", "")).strip()
        if not run_id:
            return {"action": "block", "message": "run_id is required to stop a run"}
        # Stable Hermes v2026.7.7.2 recognizes this directive and escalates to
        # its real CLI/gateway human approval flow. The handler additionally
        # requires authorization=run_id as defense against a mis-targeted retry.
        return {
            "action": "approve",
            "message": f"Cancel HCA run {run_id} and terminate its owned workers?",
            "rule_key": f"hca_team_stop:{run_id}",
        }
    if tool_name != "delegate_task":
        return None
    db = _state_db()
    if db is None:
        return None
    limit = _max_children()
    tasks = args.get("tasks")
    n = len(tasks) if isinstance(tasks, list) and tasks else 1
    credits = float(n)
    used = db.active_lease_credits(kind="subagent")
    if used + credits > limit:
        # Default limit is 0 → delegation is blocked; durable fan-out uses Kanban.
        msg = (
            f"HCA subagent budget exceeded ({used + credits:.0f}/{limit:.0f}). "
            "Create durable Kanban child tasks (visible + recoverable) instead "
            "of subagents, or continue sequentially."
        )
        db.set_activity(kind="admission.wait", message=msg)
        return {"action": "block", "message": msg}
    # Reserve provisional leases WITHOUT a fixed expiry (a long child must stay
    # counted). Each reservation is tagged with the parent so session-end can
    # reconcile orphans that never produced a subagent_start.
    owner = _parent_owner()
    for _ in range(n):
        db.acquire_lease(
            lease_id=f"subagent-reserve-{uuid.uuid4().hex[:12]}",
            kind="subagent",
            owner=owner,
            credits=1.0,
            ttl_seconds=None,
            meta={"phase": "reserve", "parent": owner},
        )
    return None


def on_subagent_start(
    child_subagent_id: str = "",
    child_session_id: str = "",
    parent_session_id: str = "",
    parent_turn_id: str = "",
    **kwargs,
) -> None:
    db = _state_db()
    if db is None:
        return
    owner = _parent_owner()
    # Convert one provisional reservation into an exact child lease keyed by
    # child_session_id (the durable correlation key present in stop too).
    try:
        with db.connection() as conn:
            conn.execute(
                "DELETE FROM leases WHERE lease_id IN ("
                "  SELECT lease_id FROM leases WHERE kind='subagent' "
                "  AND meta_json LIKE '%\"phase\": \"reserve\"%' AND owner=? "
                "  ORDER BY created_at ASC LIMIT 1)",
                (owner,),
            )
    except Exception:
        pass
    if child_session_id:
        db.acquire_lease(
            lease_id=f"subagent-{child_session_id}",
            kind="subagent",
            owner=owner,
            credits=1.0,
            ttl_seconds=None,  # no fixed expiry — long children stay counted
            meta={
                "phase": "active",
                "child_subagent_id": child_subagent_id,
                "child_session_id": child_session_id,
                "parent_session_id": parent_session_id,
                "parent_turn_id": parent_turn_id,
                "parent": owner,
            },
        )
    db.set_activity(
        kind="subagent.start",
        message=f"subagent {child_subagent_id or child_session_id} start",
        task_id=os.environ.get("HERMES_KANBAN_TASK", ""),
        run_id=os.environ.get("HERMES_KANBAN_RUN_ID", ""),
        data={"child_subagent_id": child_subagent_id, "child_session_id": child_session_id},
    )


def on_subagent_stop(child_session_id: str = "", **kwargs) -> None:
    db = _state_db()
    if db is None:
        return
    # Release the EXACT child lease by child_session_id (out-of-order safe).
    # subagent_stop carries no child_subagent_id, so session id is the key.
    if child_session_id:
        db.release_lease(f"subagent-{child_session_id}")
    db.set_activity(
        kind="subagent.stop",
        message=f"subagent {child_session_id} stop",
        task_id=os.environ.get("HERMES_KANBAN_TASK", ""),
        run_id=os.environ.get("HERMES_KANBAN_RUN_ID", ""),
        data={"child_session_id": child_session_id},
    )


def on_session_end(**kwargs) -> None:
    db = _state_db()
    if db is None:
        return
    owner = _parent_owner()
    # Conservative orphan reconciliation: release this parent's leases (its
    # children cannot outlive it) plus any genuinely expired leases.
    try:
        with db.connection() as conn:
            if owner:
                conn.execute("DELETE FROM leases WHERE owner=?", (owner,))
            conn.execute(
                "DELETE FROM leases WHERE expires_at IS NOT NULL AND expires_at < ?",
                (time.time(),),
            )
    except Exception:
        pass
