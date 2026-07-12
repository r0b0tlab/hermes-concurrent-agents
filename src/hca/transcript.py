"""Transcript resolution from Hermes sessions / run mappings."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from hca.observe import redact_text
from hca.state import StateDB


def _session_db_candidates(profile: str = "") -> list[Path]:
    home = Path(os.path.expanduser("~/.hermes"))
    cands = []
    if profile:
        cands.append(home / "profiles" / profile / "state.db")
        cands.append(home / "profiles" / profile / "sessions.db")
    cands += [
        home / "state.db",
        home / "sessions.db",
        home / "hermes_state.db",
    ]
    return cands


def _read_sqlite_messages(db_path: Path, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Try common Hermes schemas
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        msgs: list[dict[str, Any]] = []
        if "messages" in tables:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
            # flexible select
            session_col = "session_id" if "session_id" in cols else ("conversation_id" if "conversation_id" in cols else None)
            if not session_col:
                return []
            role_col = "role" if "role" in cols else "message_role"
            content_col = "content" if "content" in cols else ("text" if "text" in cols else None)
            if not content_col:
                return []
            q = f"SELECT * FROM messages WHERE {session_col}=? ORDER BY rowid DESC LIMIT ?"
            rows = conn.execute(q, (session_id, limit)).fetchall()
            for r in reversed(rows):
                msgs.append(
                    {
                        "role": r[role_col] if role_col in r.keys() else "",
                        "content": r[content_col] if content_col in r.keys() else "",
                    }
                )
            return msgs
        if "session_messages" in tables:
            rows = conn.execute(
                "SELECT role, content FROM session_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        return []
    finally:
        conn.close()


def resolve_run(
    state: StateDB,
    target: str,
) -> Optional[dict[str, Any]]:
    """Resolve task_id, run_id, or slot to a run record dict."""
    runs = state.list_runs(status=None)
    for r in runs:
        if target in {r.task_id, r.run_id, r.slot, r.tmux_session}:
            return {
                "board": r.board,
                "task_id": r.task_id,
                "run_id": r.run_id,
                "slot": r.slot,
                "node": r.node,
                "tmux_session": r.tmux_session,
                "pid": r.pid,
                "hermes_session_id": r.hermes_session_id,
                "workspace": r.workspace,
                "status": r.status,
                "last_activity": r.last_activity,
            }
    # match prefix of task ids
    for r in runs:
        if r.task_id.startswith(target) or r.run_id.startswith(target):
            return {
                "board": r.board,
                "task_id": r.task_id,
                "run_id": r.run_id,
                "slot": r.slot,
                "node": r.node,
                "tmux_session": r.tmux_session,
                "pid": r.pid,
                "hermes_session_id": r.hermes_session_id,
                "workspace": r.workspace,
                "status": r.status,
                "last_activity": r.last_activity,
            }
    return None


def fetch_transcript(
    state: StateDB,
    target: str,
    *,
    profile_hint: str = "",
    limit: int = 100,
    redact_patterns: Optional[list[str]] = None,
) -> dict[str, Any]:
    rec = resolve_run(state, target)
    out: dict[str, Any] = {
        "target": target,
        "run": rec,
        "messages": [],
        "source": "none",
        "error": "",
    }
    session_id = (rec or {}).get("hermes_session_id") or ""
    # Try Hermes CLI export if available
    if not session_id and rec:
        # Infer profile from slot hca-<fleet>-<role>-NN
        slot = rec.get("slot") or ""
        parts = slot.split("-")
        if len(parts) >= 4:
            profile_hint = profile_hint or slot

    if session_id:
        for db in _session_db_candidates(profile_hint):
            if not db.is_file():
                continue
            try:
                msgs = _read_sqlite_messages(db, session_id, limit=limit)
                if msgs:
                    if redact_patterns:
                        for m in msgs:
                            if isinstance(m.get("content"), str):
                                m["content"] = redact_text(m["content"], redact_patterns)
                    out["messages"] = msgs
                    out["source"] = f"sqlite:{db}"
                    return out
            except Exception as exc:
                out["error"] = str(exc)

    # Fallback: activity stream for the run
    if rec:
        acts = [
            a
            for a in state.recent_activity(200)
            if a.get("run_id") == rec.get("run_id") or a.get("task_id") == rec.get("task_id")
        ]
        out["messages"] = [
            {"role": "system", "content": f"{a['kind']}: {a.get('message','')}"}
            for a in reversed(acts)
        ]
        out["source"] = "activity-fallback"
        if not out["messages"]:
            out["error"] = "no hermes session id and no activity; attach/peek for live pane"
    else:
        out["error"] = f"no run mapping for {target!r}; try slot name or run id after spawn"
    return out
