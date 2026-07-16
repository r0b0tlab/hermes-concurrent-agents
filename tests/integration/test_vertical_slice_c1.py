"""Real c1 goal-to-result vertical slice against a temporary Hermes Kanban.

This is the acceptance the controller demanded to replace the synthetic
`CompletingOrchestrator` path. It uses:

  * a real temporary Kanban DB (via ``HERMES_KANBAN_DB``),
  * the *actual* upstream ``dispatch_once`` through the HCA reservation-first
    spawn seam (``kanban.dispatch_tick`` → ``make_tmux_spawn_fn``),
  * a fake-process worker that binds a **real** OS PID and completes its task
    through the real ``complete_task`` API (standing in for a Hermes LLM
    worker's ``kanban_complete`` call).

It fails unless a concrete task gets an integer ``current_run_id``, a real PID
is bound, upstream completion + a result are observed, and ``status``/``collect``
reconcile the run to an evidence-backed ``completed``. A parallel negative test
proves the same machinery leaves the run ``blocked`` when the worker never
completes — no fabricated success.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from hca.config import load_fleet_config
from hca.kanban_orchestrator import KanbanOrchestrator
from hca.routing import concrete_slots
from hca.run import RunStore
from hca.service import FleetService
from hca.state import StateDB

# Fake-process worker: stays alive briefly (so the dispatcher observes a live
# pid on the running task), then completes the task through the real Kanban API
# and exits. Launched as a real subprocess → a real OS PID is bound.
_WORKER_SRC = r"""
import os, sys, time
sys.path.insert(0, os.environ["HCA_WORKER_HERMES_SRC"])
from hermes_cli import kanban_db as kb
tid = os.environ["HERMES_KANBAN_TASK"]
rid = int(os.environ["HERMES_KANBAN_RUN_ID"])
time.sleep(0.2)
conn = kb.connect(board=os.environ.get("HERMES_KANBAN_BOARD") or None)
try:
    kb.complete_task(conn, tid, result="done by fake worker " + tid,
                     summary="fake worker complete", expected_run_id=rid)
    conn.commit()
finally:
    conn.close()
"""

# Fake-process worker that binds a real pid but NEVER completes the task.
_IDLE_WORKER_SRC = r"""
import time
time.sleep(1)
"""


class FakeTmux:
    """Stand-in for TmuxManager: launches a real subprocess worker per slot."""

    def __init__(self, hermes_src: str, worker_src: str = _WORKER_SRC):
        self.hermes_src = hermes_src
        self.worker_src = worker_src
        self.procs: list[subprocess.Popen] = []

    def run_in_slot(self, name, command, *, env=None, unset_env=None,
                    workdir=None, log_path=None) -> int:
        worker_env = {**os.environ, **(env or {})}
        worker_env["HCA_WORKER_HERMES_SRC"] = self.hermes_src
        worker_env["PYTHONPATH"] = (
            self.hermes_src + os.pathsep + worker_env.get("PYTHONPATH", "")
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", self.worker_src], env=worker_env
        )
        self.procs.append(proc)
        return proc.pid

    def cleanup(self) -> None:
        for p in self.procs:
            try:
                p.terminate()
            except Exception:
                pass


def _make_env(monkeypatch, tmp_path: Path, hermes_src: str):
    home = tmp_path / "hermes_home"
    (home / "profiles").mkdir(parents=True)
    board_db = tmp_path / "kanban.db"
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    state_dir = tmp_path / "hca_state"
    state_dir.mkdir()

    cfg = load_fleet_config(model="m", state_dir=str(state_dir))
    # Create a real (minimal) profile dir per concrete slot so upstream
    # ``profile_exists`` accepts the assignee and the ready-dispatch path runs.
    for slot in concrete_slots(cfg):
        (home / "profiles" / slot.profile).mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(board_db))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACES_ROOT", str(ws_root))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", cfg.board)
    return cfg, state_dir


def _run_slice(
    monkeypatch, tmp_path, hermes_runtime, worker_src,
    *, max_wall_seconds=15.0, max_ticks=60, poll_interval=0.1,
):
    cfg, state_dir = _make_env(monkeypatch, tmp_path, hermes_runtime.src_path)
    state = StateDB(state_dir / "hca.sqlite")
    tmux = FakeTmux(hermes_runtime.src_path, worker_src=worker_src)
    orch = KanbanOrchestrator(
        cfg,
        state=state,
        tmux=tmux,
        board=cfg.board,
        enforce_sole_dispatcher=False,  # no competing gateway in this temp env
        max_wall_seconds=max_wall_seconds,
        max_ticks=max_ticks,
        poll_interval=poll_interval,
    )
    store = RunStore(state_dir / "runs.sqlite")
    svc = FleetService(cfg, orchestrator=orch, store=store)
    try:
        res = svc.run("Write a short greeting to a file", review_policy="never")
    finally:
        tmux.cleanup()
    return svc, res, cfg, state


def _evidence_event(store, run_id):
    ev = {}
    for e in store.list_events(run_id):
        if e["kind"] == "run.evidence":
            ev = e["data"].get("evidence", {})
    return ev


def test_c1_vertical_slice_completes_with_real_evidence(
    monkeypatch, tmp_path, hermes_runtime
):
    svc, res, cfg, state = _run_slice(
        monkeypatch, tmp_path, hermes_runtime, _WORKER_SRC
    )

    assert res.state == "completed", (
        f"expected completed, got {res.state}: {res.remediation}"
    )
    assert res.code == 0

    # --- durable real work was submitted to the Kanban board ---
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "kanban.db"))
    conn.row_factory = sqlite3.Row
    try:
        n_tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        assert n_tasks >= 2  # root triage container + at least one child
        # at least one task terminally done with a real result
        done = conn.execute(
            "SELECT id, result FROM tasks WHERE status='done' AND result IS NOT NULL"
        ).fetchall()
        assert done, "no done task with a result on the board"
        # a task_runs row proves an integer run identity existed
        n_runs = conn.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0]
        assert n_runs >= 1
    finally:
        conn.close()

    # --- evidence captured integer run id + bound pid + result ---
    ev = _evidence_event(svc.store, res.run_id)
    tasks = ev.get("tasks", [])
    assert tasks
    proven = [
        t for t in tasks
        if t["terminal_status"] == "done"
        and isinstance(t["run_id"], int)
        and isinstance(t["pid"], int)
        and t["result"]
    ]
    assert proven, f"no task with integer run_id + pid + result: {json.dumps(tasks)}"

    # --- status reconciles to completed; collect returns evidence-backed success ---
    st = svc.status(res.run_id)
    assert st.state == "completed"
    col = svc.collect(res.run_id)
    manifest = col.data["result"]
    assert manifest["outcome"] == "success"
    assert manifest["artifacts"], "collect must link a real result/artifact"
    assert len(manifest["manifest_sha256"]) == 64


def test_c1_slice_blocks_when_worker_never_completes(
    monkeypatch, tmp_path, hermes_runtime
):
    # Same real machinery, but the fake worker binds a pid and exits without
    # completing. The run must NOT be reported as success — it stays blocked.
    svc, res, cfg, state = _run_slice(
        monkeypatch, tmp_path, hermes_runtime, _IDLE_WORKER_SRC,
        max_wall_seconds=4.0, max_ticks=12, poll_interval=0.25,
    )
    assert res.state != "completed"
    assert res.state in ("blocked", "failed")
    col = svc.collect(res.run_id)
    assert col.data["result"]["outcome"] in ("blocked", "failed", "partial")
