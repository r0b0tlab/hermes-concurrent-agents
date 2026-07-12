"""Human observability helpers: status tables, peek, redact, activity."""

from __future__ import annotations

import re
import time
from typing import Any, Iterable, Optional

from hca.models import FleetConfig
from hca.state import StateDB
from hca.tmux import TmuxManager, sanitize_session_name


def redact_text(text: str, patterns: Iterable[str]) -> str:
    out = text
    for pat in patterns:
        try:
            out = re.sub(pat, "[REDACTED]", out)
        except re.error:
            continue
    return out


def slot_name(fleet: str, role: str, index: int) -> str:
    return sanitize_session_name(f"hca-{fleet}-{role}-{index:02d}")


def list_expected_slots(cfg: FleetConfig) -> list[str]:
    names = []
    for role, count in cfg.profile_slots.items():
        for i in range(1, int(count) + 1):
            names.append(slot_name(cfg.name, role, i))
    return names


def status_rows(cfg: FleetConfig, state: StateDB, tmux: TmuxManager) -> list[dict[str, Any]]:
    runs = {r.slot: r for r in state.list_runs(status="running")}
    rows = []
    for slot in list_expected_slots(cfg):
        exists = tmux.has_session(slot)
        rec = runs.get(slot)
        rows.append(
            {
                "slot": slot,
                "tmux": "up" if exists else "missing",
                "board": rec.board if rec else cfg.board,
                "task_id": rec.task_id if rec else "",
                "run_id": rec.run_id if rec else "",
                "pid": rec.pid if rec else tmux.pane_pid(slot) if exists else None,
                "node": rec.node if rec else "local",
                "activity": rec.last_activity if rec else ("idle" if exists else "absent"),
                "status": rec.status if rec else ("idle" if exists else "absent"),
                "age_s": round(time.time() - rec.started_at, 1) if rec else None,
            }
        )
    # orphan running mappings not in expected slots
    for slot, rec in runs.items():
        if slot in {r["slot"] for r in rows}:
            continue
        rows.append(
            {
                "slot": slot,
                "tmux": "up" if tmux.has_session(slot) else "missing",
                "board": rec.board,
                "task_id": rec.task_id,
                "run_id": rec.run_id,
                "pid": rec.pid,
                "node": rec.node,
                "activity": rec.last_activity or "",
                "status": rec.status,
                "age_s": round(time.time() - rec.started_at, 1),
                "orphan": True,
            }
        )
    return rows


def format_status_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(no slots)"
    headers = ["SLOT", "TMUX", "STATUS", "TASK", "RUN", "PID", "ACTIVITY", "AGE"]
    lines = ["  ".join(headers)]
    for r in rows:
        lines.append(
            "  ".join(
                [
                    str(r.get("slot", ""))[:28],
                    str(r.get("tmux", "")),
                    str(r.get("status", "")),
                    str(r.get("task_id", ""))[:12],
                    str(r.get("run_id", ""))[:12],
                    str(r.get("pid") or "-"),
                    str(r.get("activity", ""))[:40],
                    str(r.get("age_s") if r.get("age_s") is not None else "-"),
                ]
            )
        )
    return "\n".join(lines)


def peek_slot(cfg: FleetConfig, tmux: TmuxManager, slot_or_task: str) -> str:
    # resolve by slot name first
    name = sanitize_session_name(slot_or_task)
    if not tmux.has_session(name):
        # try match running task id
        # caller may pass task id; scan sessions is limited — use literal
        name = slot_or_task
    text = tmux.capture_pane(name, lines=cfg.observe.peek_lines)
    return redact_text(text, cfg.observe.redact_patterns)
