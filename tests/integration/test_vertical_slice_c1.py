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
import time
from pathlib import Path

from hca.config import load_fleet_config
from hca.hermes_compat import HermesCompatError
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
# Keep this deterministic contract worker focused on Kanban state. Plugin-hook
# behavior has separate integration coverage and can perform optional discovery
# that is intentionally absent from the minimal source-checkout test env.
kb._fire_kanban_lifecycle_hook = lambda *args, **kwargs: None
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

# Real upstream completion may carry the durable text handoff in the run's
# structured ``summary`` while leaving the task's optional ``result`` column
# empty. Hermes downstream context treats that summary as the worker result;
# HCA must project the same contract rather than report empty success.
_SUMMARY_ONLY_WORKER_SRC = r"""
import os, sys, time
sys.path.insert(0, os.environ["HCA_WORKER_HERMES_SRC"])
from hermes_cli import kanban_db as kb
kb._fire_kanban_lifecycle_hook = lambda *args, **kwargs: None
tid = os.environ["HERMES_KANBAN_TASK"]
rid = int(os.environ["HERMES_KANBAN_RUN_ID"])
time.sleep(0.15)
conn = kb.connect(board=os.environ.get("HERMES_KANBAN_BOARD") or None)
try:
    if not kb.complete_task(
        conn,
        tid,
        summary="durable summary-only result for " + tid,
        expected_run_id=rid,
    ):
        raise RuntimeError("summary-only completion CAS failed")
    conn.commit()
finally:
    conn.close()
"""

# A worker may create a ready task using its broad upstream Kanban toolset.
# HCA owns only the persisted graph it planned; the extra task must never gain
# a process, run record, or lease merely because it shares the board.
_OUT_OF_GRAPH_WORKER_SRC = r"""
import os, sys, time
sys.path.insert(0, os.environ["HCA_WORKER_HERMES_SRC"])
from hermes_cli import kanban_db as kb
kb._fire_kanban_lifecycle_hook = lambda *args, **kwargs: None
tid = os.environ["HERMES_KANBAN_TASK"]
rid = int(os.environ["HERMES_KANBAN_RUN_ID"])
profile = os.environ["HERMES_PROFILE"]
time.sleep(0.15)
conn = kb.connect(board=os.environ.get("HERMES_KANBAN_BOARD") or None)
try:
    task = kb.get_task(conn, tid)
    if task and task.title.startswith("Implement:"):
        kb.create_task(
            conn,
            title="worker-created out-of-graph task",
            body="must remain outside HCA ownership",
            assignee=profile,
            created_by=profile,
            board=os.environ.get("HERMES_KANBAN_BOARD") or None,
        )
    if not kb.complete_task(
        conn,
        tid,
        result="completed declared graph task " + tid,
        summary="declared task complete",
        expected_run_id=rid,
    ):
        raise RuntimeError("declared completion CAS failed")
    conn.commit()
finally:
    conn.close()
"""

# Fake-process worker that binds a real pid but NEVER completes the task.
_IDLE_WORKER_SRC = r"""
import time
time.sleep(30)
"""

_INPUT_WORKER_SRC = r"""
import os, sys, time
sys.path.insert(0, os.environ["HCA_WORKER_HERMES_SRC"])
from hermes_cli import kanban_db as kb
kb._fire_kanban_lifecycle_hook = lambda *args, **kwargs: None
tid = os.environ["HERMES_KANBAN_TASK"]
rid = int(os.environ["HERMES_KANBAN_RUN_ID"])
time.sleep(0.15)
conn = kb.connect(board=os.environ.get("HERMES_KANBAN_BOARD") or None)
try:
    task = kb.get_task(conn, tid)
    comments = kb.list_comments(conn, tid)
    answered = any(c.author == "hca-operator" for c in comments)
    if task and task.title.startswith("Implement:") and not answered:
        if not kb.block_task(
            conn,
            tid,
            reason="Which deployment target should be used?",
            kind="needs_input",
            expected_run_id=rid,
        ):
            raise RuntimeError("needs-input CAS failed")
    elif not kb.complete_task(
        conn,
        tid,
        result=f"completed {task.title if task else tid}",
        summary="deterministic input lifecycle worker",
        expected_run_id=rid,
    ):
        raise RuntimeError("completion CAS failed")
finally:
    conn.close()
"""

_REVIEW_WORKER_SRC = r"""
import os, sys, time
from pathlib import Path
sys.path.insert(0, os.environ["HCA_WORKER_HERMES_SRC"])
from hermes_cli import kanban_db as kb
# Keep this deterministic contract worker focused on Kanban state. Plugin-hook
# behavior has separate integration coverage and can perform optional discovery
# that is intentionally absent from the minimal source-checkout test env.
kb._fire_kanban_lifecycle_hook = lambda *args, **kwargs: None
tid = os.environ["HERMES_KANBAN_TASK"]
rid = int(os.environ["HERMES_KANBAN_RUN_ID"])
# Real Hermes startup imports the agent/tool stack before opening Kanban. Give
# dispatch time to persist worker_pid so the upstream first-open integrity
# probe does not race that write in this intentionally tiny fake worker.
time.sleep(0.15)
conn = kb.connect(board=os.environ.get("HERMES_KANBAN_BOARD") or None)
try:
    task = kb.get_task(conn, tid)
    title = task.title if task else ""
    result = "completed " + title
    if title.startswith("Independently verify"):
        state_file = Path(os.environ["HCA_REVIEW_STATE_FILE"])
        count = int(state_file.read_text() or "0") if state_file.exists() else 0
        count += 1
        state_file.write_text(str(count))
        mode = os.environ.get("HCA_REVIEW_MODE", "accept")
        reject = mode == "always_reject" or (mode == "reject_once" and count == 1)
        result = (
            "HCA_REVIEW: REJECT\nmissing required verification"
            if reject else
            "HCA_REVIEW: ACCEPT\nverification passed"
        )
    time.sleep(0.1)
    completed = kb.complete_task(
        conn,
        tid,
        result=result,
        summary="deterministic lifecycle worker",
        expected_run_id=rid,
    )
    if not completed:
        raise RuntimeError(f"completion CAS failed for {tid} run {rid}")
    conn.commit()
finally:
    conn.close()
"""


_PARALLEL_WORKER_SRC = r"""
import fcntl, json, os, sys, time
sys.path.insert(0, os.environ["HCA_WORKER_HERMES_SRC"])
from hermes_cli import kanban_db as kb
kb._fire_kanban_lifecycle_hook = lambda *args, **kwargs: None
tid = os.environ["HERMES_KANBAN_TASK"]
rid = int(os.environ["HERMES_KANBAN_RUN_ID"])
log_path = os.environ["HCA_PARALLEL_INTERVAL_LOG"]
time.sleep(0.15)
conn = kb.connect(board=os.environ.get("HERMES_KANBAN_BOARD") or None)
try:
    task = kb.get_task(conn, tid)
    title = task.title if task else ""
    parents = kb.parent_results(conn, tid)
finally:
    conn.close()

def emit(phase):
    row = {
        "task_id": tid,
        "title": title,
        "phase": phase,
        "time": time.monotonic(),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
    }
    with open(log_path, "a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
        fcntl.flock(stream, fcntl.LOCK_UN)

emit("start")
if title.startswith("Independent work slice"):
    time.sleep(0.6)
else:
    time.sleep(0.05)
if title.startswith("Integrate"):
    if len(parents) != 2 or not all(result for _, result in parents):
        raise RuntimeError("integration did not receive both parent results")
emit("end")
conn = kb.connect(board=os.environ.get("HERMES_KANBAN_BOARD") or None)
try:
    if not kb.complete_task(
        conn,
        tid,
        result=f"completed {title}; parent_results={len(parents)}",
        summary="deterministic parallel acceptance worker",
        expected_run_id=rid,
    ):
        raise RuntimeError(f"completion CAS failed for {tid}")
    conn.commit()
finally:
    conn.close()
"""


class FakeTmux:
    """Stand-in for TmuxManager: launches a real subprocess worker per slot."""

    def __init__(
        self,
        hermes_src: str,
        worker_src: str = _WORKER_SRC,
        extra_env: dict[str, str] | None = None,
    ):
        self.hermes_src = hermes_src
        self.worker_src = worker_src
        self.extra_env = dict(extra_env or {})
        self.procs: list[subprocess.Popen] = []
        self.calls: list[dict] = []

    def run_in_slot(self, name, command, *, env=None, unset_env=None,
                    workdir=None, log_path=None) -> int:
        worker_env = {**os.environ, **self.extra_env, **(env or {})}
        worker_env["HCA_WORKER_HERMES_SRC"] = self.hermes_src
        worker_env["PYTHONPATH"] = (
            self.hermes_src + os.pathsep + worker_env.get("PYTHONPATH", "")
        )
        # start_new_session=True gives the worker its own process group (as a
        # tmux pane would), so cancellation can killpg it without touching the
        # test runner.
        self.calls.append(
            {
                "name": name,
                "command": command,
                "env": dict(env or {}),
                "workdir": workdir,
                "log_path": log_path,
            }
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", self.worker_src],
            env=worker_env,
            cwd=workdir,
            start_new_session=True,
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
    import hca.kanban as hca_kanban
    from hca.resources import AdmissionDecision

    monkeypatch.setattr(
        hca_kanban,
        "admit",
        lambda *args, **kwargs: AdmissionDecision(True, "test admission"),
    )
    home = tmp_path / "hermes_home"
    (home / "profiles").mkdir(parents=True)
    board_db = tmp_path / "kanban.db"
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    state_dir = tmp_path / "hca_state"
    state_dir.mkdir()

    cfg = load_fleet_config(model="m", state_dir=str(state_dir))
    # The detached controller is a fresh Python process and cannot inherit the
    # in-process admit monkeypatch above. Serialize non-triggering test-only
    # pressure thresholds so this lifecycle fixture is independent of the
    # developer host's current disk/memory utilization. Dedicated admission
    # tests inject exact DeviceSignals and retain production fail-closed logic.
    cfg.capacity.memory_high = 1.01
    cfg.capacity.memory_low = 1.0
    cfg.capacity.disk_high = 1.01
    cfg.capacity.disk_low = 1.0
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
    monkeypatch.setattr(
        hermes_runtime.kb, "_fire_kanban_lifecycle_hook", lambda *args, **kwargs: None
    )
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


def test_dispatcher_conflict_blocks_before_creating_any_kanban_task(
    monkeypatch, tmp_path, hermes_runtime
):
    cfg, state_dir = _make_env(monkeypatch, tmp_path, hermes_runtime.src_path)
    board_path = Path(hermes_runtime.kb.kanban_db_path(board=cfg.board))

    def conflict(_board):
        raise HermesCompatError("live gateway owns this Hermes home")

    monkeypatch.setattr("hca.kanban_orchestrator.assert_sole_dispatcher", conflict)
    state = StateDB(state_dir / "hca.sqlite")
    orch = KanbanOrchestrator(
        cfg,
        state=state,
        tmux=FakeTmux(hermes_runtime.src_path),
        board=cfg.board,
        enforce_sole_dispatcher=True,
    )
    svc = FleetService(cfg, orchestrator=orch, store=RunStore(state_dir / "runs.sqlite"))
    result = svc.run("must not create an upstream task", review_policy="never")

    assert result.state == "blocked"
    assert "dispatcher ownership preflight failed" in result.remediation
    assert not board_path.exists()
    assert not any(
        event["kind"] == "run.kanban_root"
        for event in svc.store.list_events(result.run_id)
    )


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

    # every durable worker lease was released on terminal completion
    assert state.active_lease_credits() == 0.0


def test_summary_only_upstream_handoff_is_result_evidence(
    monkeypatch, tmp_path, hermes_runtime
):
    svc, res, _cfg, state = _run_slice(
        monkeypatch, tmp_path, hermes_runtime, _SUMMARY_ONLY_WORKER_SRC
    )

    assert res.state == "completed", res.to_dict()
    evidence = _evidence_event(svc.store, res.run_id)
    done = [task for task in evidence["tasks"] if task["terminal_status"] == "done"]
    assert done
    assert all(task["result"].startswith("durable summary-only result") for task in done)
    assert all(not task["block_reason"] for task in done)
    assert state.active_lease_credits() == 0.0


def test_worker_created_task_never_leaves_the_persisted_graph(
    monkeypatch, tmp_path, hermes_runtime
):
    svc, res, cfg, state = _run_slice(
        monkeypatch, tmp_path, hermes_runtime, _OUT_OF_GRAPH_WORKER_SRC
    )

    assert res.state == "completed", res.to_dict()
    mapping = next(
        event["data"]
        for event in svc.store.list_events(res.run_id)
        if event["kind"] == "run.kanban_root"
    )
    conn = hermes_runtime.kb.connect(board=cfg.board)
    try:
        extra = conn.execute(
            "SELECT id, status, block_kind FROM tasks WHERE title = ?",
            ("worker-created out-of-graph task",),
        ).fetchone()
        assert extra is not None
        block = conn.execute(
            "SELECT outcome, summary FROM task_runs "
            "WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (extra["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert extra["id"] not in mapping["child_task_ids"]
    assert extra["status"] == "blocked"
    assert extra["block_kind"] == "capability"
    assert block is not None
    assert block["outcome"] == "blocked"
    assert "HCA_OUT_OF_GRAPH" in block["summary"]
    assert "worker graph expansion denied" in block["summary"]
    assert state.latest_run_for_task(cfg.board, extra["id"]) is None
    denied = [
        event
        for event in svc.store.list_events(res.run_id)
        if event["kind"] == "run.graph_expansion_denied"
    ]
    assert denied and denied[-1]["data"]["task_ids"] == [extra["id"]]
    assert state.active_lease_credits() == 0.0


def test_parallel_acceptance_uses_distinct_workers_worktrees_and_real_overlap(
    monkeypatch, tmp_path, hermes_runtime
):
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "HCA Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "hca@example.invalid"],
        check=True,
    )
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)

    cfg, state_dir = _make_env(monkeypatch, tmp_path, hermes_runtime.src_path)
    cfg.name = "parallel"
    cfg.profile_slots = {"orchestrator": 1, "coder": 2, "qa": 1}
    home = Path(os.environ["HERMES_HOME"])
    for slot in concrete_slots(cfg):
        (home / "profiles" / slot.profile).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        hermes_runtime.kb, "_fire_kanban_lifecycle_hook", lambda *args, **kwargs: None
    )
    interval_log = tmp_path / "parallel-intervals.jsonl"
    state = StateDB(state_dir / "hca.sqlite")
    tmux = FakeTmux(
        hermes_runtime.src_path,
        worker_src=_PARALLEL_WORKER_SRC,
        extra_env={"HCA_PARALLEL_INTERVAL_LOG": str(interval_log)},
    )
    orch = KanbanOrchestrator(
        cfg,
        state=state,
        tmux=tmux,
        board=cfg.board,
        enforce_sole_dispatcher=False,
        max_wall_seconds=20,
        poll_interval=0.2,
    )
    svc = FleetService(cfg, orchestrator=orch, store=RunStore(state_dir / "runs.sqlite"))
    try:
        result = svc.run(
            "Produce two independent results and combine them",
            project_root=str(repo),
            acceptance_criteria=["produce alpha result", "produce beta result"],
            independent_criteria=True,
            concurrency=2,
            review_policy="never",
            budgets={"wall_seconds": 20, "max_tasks": 8},
        )
    finally:
        tmux.cleanup()

    assert result.state == "completed", result.to_dict()
    mapping = orch._mapping(svc.store, result.run_id)
    assert mapping is not None
    work_ids = [
        task_id
        for task_id, kind in mapping["node_kinds"].items()
        if kind == "work"
    ]
    assert len(work_ids) == 2
    assert list(mapping["node_kinds"].values()).count("integration") == 1

    conn = hermes_runtime.kb.connect(board=cfg.board)
    try:
        work_tasks = [hermes_runtime.kb.get_task(conn, task_id) for task_id in work_ids]
    finally:
        conn.close()
    assert len({task.assignee for task in work_tasks}) == 2
    assert all(task.workspace_kind == "worktree" for task in work_tasks)

    work_calls = [
        call
        for call in tmux.calls
        if call["env"].get("HERMES_KANBAN_TASK") in set(work_ids)
    ]
    assert len(work_calls) == 2
    workdirs = {str(call["workdir"]) for call in work_calls}
    assert len(workdirs) == 2
    assert all("/.worktrees/" in workdir for workdir in workdirs)
    assert all(Path(workdir).is_dir() for workdir in workdirs)

    rows = [json.loads(line) for line in interval_log.read_text().splitlines()]
    intervals = {}
    for row in rows:
        intervals.setdefault(row["task_id"], {})[row["phase"]] = row
    work_intervals = [intervals[task_id] for task_id in work_ids]
    latest_start = max(item["start"]["time"] for item in work_intervals)
    earliest_end = min(item["end"]["time"] for item in work_intervals)
    assert latest_start < earliest_end, "independent worker intervals did not overlap"
    serial_work = sum(
        item["end"]["time"] - item["start"]["time"] for item in work_intervals
    )
    parallel_critical_path = max(
        item["end"]["time"] for item in work_intervals
    ) - min(item["start"]["time"] for item in work_intervals)
    assert parallel_critical_path < serial_work * 0.75

    integration_id = next(
        task_id
        for task_id, kind in mapping["node_kinds"].items()
        if kind == "integration"
    )
    assert intervals[integration_id]["start"]["time"] >= max(
        item["end"]["time"] for item in work_intervals
    )
    metrics_path = os.environ.get("HCA_PARALLEL_METRICS_OUT", "").strip()
    if metrics_path:
        Path(metrics_path).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "independent_workers": len(work_ids),
                    "distinct_worker_profiles": len(
                        {task.assignee for task in work_tasks}
                    ),
                    "distinct_worktrees": len(workdirs),
                    "serial_work_seconds": serial_work,
                    "parallel_critical_path_seconds": parallel_critical_path,
                    "work_only_speedup": serial_work / parallel_critical_path,
                    "overlap_seconds": earliest_end - latest_start,
                    "dependency_fanin_after_parents": True,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    assert state.active_lease_credits() == 0.0


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


def test_review_rejection_stages_one_bounded_rework_then_accepts(
    monkeypatch, tmp_path, hermes_runtime
):
    cfg, state_dir = _make_env(monkeypatch, tmp_path, hermes_runtime.src_path)
    monkeypatch.setattr(
        hermes_runtime.kb, "_fire_kanban_lifecycle_hook", lambda *args, **kwargs: None
    )
    state = StateDB(state_dir / "hca.sqlite")
    tmux = FakeTmux(
        hermes_runtime.src_path,
        worker_src=_REVIEW_WORKER_SRC,
        extra_env={
            "HCA_REVIEW_MODE": "reject_once",
            "HCA_REVIEW_STATE_FILE": str(tmp_path / "review-count"),
        },
    )
    orch = KanbanOrchestrator(
        cfg,
        state=state,
        tmux=tmux,
        board=cfg.board,
        enforce_sole_dispatcher=False,
        max_ticks=160,
        max_wall_seconds=20,
        poll_interval=0.05,
    )
    svc = FleetService(cfg, orchestrator=orch, store=RunStore(state_dir / "runs.sqlite"))
    try:
        res = svc.run(
            "change reviewed code",
            review_policy="always",
            budgets={"max_review_cycles": 2, "wall_seconds": 20},
        )
    finally:
        tmux.cleanup()

    assert res.state == "completed", res.remediation
    events = svc.store.list_events(res.run_id)
    reworks = [event for event in events if event["kind"] == "run.review_rework"]
    assert len(reworks) == 1
    mapping = orch._mapping(svc.store, res.run_id)
    assert mapping is not None
    kinds = list(mapping["node_kinds"].values())
    assert kinds.count("rework") == 1
    assert kinds.count("review") == 2
    evidence = _evidence_event(svc.store, res.run_id)
    reviews = [task for task in evidence["tasks"] if task["kind"] == "review"]
    assert [task["review_verdict"] for task in reviews] == ["reject", "accept"]
    assert all(task["run_id"] and task["pid"] for task in reviews)
    assert state.active_lease_credits() == 0.0


def test_review_rejection_budget_blocks_final_without_unbounded_loop(
    monkeypatch, tmp_path, hermes_runtime
):
    cfg, state_dir = _make_env(monkeypatch, tmp_path, hermes_runtime.src_path)
    monkeypatch.setattr(
        hermes_runtime.kb, "_fire_kanban_lifecycle_hook", lambda *args, **kwargs: None
    )
    state = StateDB(state_dir / "hca.sqlite")
    tmux = FakeTmux(
        hermes_runtime.src_path,
        worker_src=_REVIEW_WORKER_SRC,
        extra_env={
            "HCA_REVIEW_MODE": "always_reject",
            "HCA_REVIEW_STATE_FILE": str(tmp_path / "review-count"),
        },
    )
    orch = KanbanOrchestrator(
        cfg,
        state=state,
        tmux=tmux,
        board=cfg.board,
        enforce_sole_dispatcher=False,
        max_ticks=160,
        max_wall_seconds=20,
        poll_interval=0.05,
    )
    svc = FleetService(cfg, orchestrator=orch, store=RunStore(state_dir / "runs.sqlite"))
    try:
        res = svc.run(
            "change rejected code",
            review_policy="always",
            budgets={"max_review_cycles": 2, "wall_seconds": 20},
        )
    finally:
        tmux.cleanup()

    assert res.state == "blocked"
    mapping = orch._mapping(svc.store, res.run_id)
    assert mapping is not None
    kinds = list(mapping["node_kinds"].values())
    assert kinds.count("review") == 2
    assert kinds.count("rework") == 1
    assert sum(
        event["kind"] == "run.review_rework"
        for event in svc.store.list_events(res.run_id)
    ) == 1
    gate_id = next(
        task_id for task_id, kind in mapping["node_kinds"].items() if kind == "gate"
    )
    final_id = next(
        task_id for task_id, kind in mapping["node_kinds"].items() if kind == "final"
    )
    assert orch._statuses([gate_id])[gate_id] == "blocked"
    assert orch._statuses([final_id])[final_id] == "todo"
    assert state.active_lease_credits() == 0.0


def test_needs_input_response_updates_upstream_and_resumes_exact_branch(
    monkeypatch, tmp_path, hermes_runtime
):
    from hca.run import RunSpec, RunState, new_run_id

    cfg, state_dir = _make_env(monkeypatch, tmp_path, hermes_runtime.src_path)
    monkeypatch.setattr(
        hermes_runtime.kb, "_fire_kanban_lifecycle_hook", lambda *args, **kwargs: None
    )
    state = StateDB(state_dir / "hca.sqlite")
    tmux = FakeTmux(hermes_runtime.src_path, worker_src=_INPUT_WORKER_SRC)
    orch = KanbanOrchestrator(
        cfg,
        state=state,
        tmux=tmux,
        board=cfg.board,
        enforce_sole_dispatcher=False,
        max_wall_seconds=15,
        poll_interval=0.2,
    )
    store = RunStore(state_dir / "runs.sqlite")
    svc = FleetService(cfg, orchestrator=orch, store=store)
    try:
        res = svc.run("deploy input-gated change", review_policy="never")
        assert res.state == "needs_input", res.to_dict()
        questions = store.open_questions(res.run_id)
        assert len(questions) == 1
        question = questions[0]
        assert question.task_id
        assert "deployment target" in question.prompt
        assert state.active_lease_credits() == 0.0

        # A real but different run cannot answer this question.
        other = RunSpec(
            run_id=new_run_id(),
            goal="unrelated",
            board=cfg.board,
            created_at=time.time(),
        )
        store.create_run(other, state=RunState.QUEUED)
        wrong = svc.respond(other.run_id, question.question_id, "staging")
        assert not wrong.ok and "belongs to run" in wrong.message

        answered = svc.respond(res.run_id, question.question_id, "staging")
        assert answered.state in {"running", "completed"}
        duplicate = svc.respond(res.run_id, question.question_id, "staging")
        assert not duplicate.ok and "already answered" in duplicate.message

        deadline = time.time() + 15
        status = answered
        while time.time() < deadline and status.state != "completed":
            status = svc.reconcile(res.run_id, dispatch=True)
            time.sleep(0.2)
        assert status.state == "completed", status.to_dict()
        assert not store.open_questions(res.run_id)

        mapping = orch._mapping(store, res.run_id)
        assert mapping is not None
        work_id = next(
            task_id
            for task_id, kind in mapping["node_kinds"].items()
            if kind == "work"
        )
        conn = hermes_runtime.kb.connect(board=cfg.board)
        try:
            comments = hermes_runtime.kb.list_comments(conn, work_id)
            operator_comments = [c for c in comments if c.author == "hca-operator"]
            assert len(operator_comments) == 1
            assert "staging" in operator_comments[0].body
            attempts = conn.execute(
                "SELECT id, status, outcome FROM task_runs WHERE task_id = ? ORDER BY id",
                (work_id,),
            ).fetchall()
        finally:
            conn.close()
        assert len(attempts) == 2
        assert attempts[0][2] == "blocked"
        assert attempts[1][2] == "completed"
        assert state.active_lease_credits() == 0.0
    finally:
        tmux.cleanup()


def test_c1_detach_returns_running_handle_without_corrupting_status(
    monkeypatch, tmp_path, hermes_runtime
):
    import time as _t

    cfg, state_dir = _make_env(monkeypatch, tmp_path, hermes_runtime.src_path)
    monkeypatch.setattr(
        hermes_runtime.kb, "_fire_kanban_lifecycle_hook", lambda *args, **kwargs: None
    )
    state = StateDB(state_dir / "hca.sqlite")
    tmux = FakeTmux(hermes_runtime.src_path, worker_src=_IDLE_WORKER_SRC)
    orch = KanbanOrchestrator(
        cfg,
        state=state,
        tmux=tmux,
        board=cfg.board,
        enforce_sole_dispatcher=False,
    )
    svc = FleetService(cfg, orchestrator=orch, store=RunStore(state_dir / "runs.sqlite"))
    started = _t.monotonic()
    try:
        res = svc.run("long detached goal", review_policy="never", detach=True)
        elapsed = _t.monotonic() - started
        assert elapsed < 3.0
        assert res.state == "running"
        # A read-only status projection sees a live worker and must remain
        # running — merely inspecting a detached run must never mark it blocked.
        status = svc.status(res.run_id)
        assert status.state == "running"
        assert state.active_lease_credits() >= 1.0
        stopped = svc.stop(res.run_id)
        assert stopped.state == "cancelled"
    finally:
        tmux.cleanup()


def test_detached_controller_finishes_real_tmux_kanban_chain(
    monkeypatch, tmp_path, hermes_runtime
):
    """A detached run keeps dispatching after the submitting service returns."""
    import time as _t

    from hca.controller import stop_controller
    from hca.run import RunState
    from hca.tmux import TmuxManager

    cfg, state_dir = _make_env(monkeypatch, tmp_path, hermes_runtime.src_path)
    cfg.tmux_socket = f"hca-c1-detach-{os.getpid()}-{tmp_path.name}"
    monkeypatch.setenv(
        "PYTHONPATH",
        hermes_runtime.src_path + os.pathsep + os.environ.get("PYTHONPATH", ""),
    )
    shim = tmp_path / "hermes-worker-shim"
    shim.write_text(
        f'''#!{sys.executable}
import os
import time
from hermes_cli import kanban_db as kb
kb._fire_kanban_lifecycle_hook = lambda *args, **kwargs: None
time.sleep(0.1)
conn = kb.connect(board=os.environ.get("HERMES_KANBAN_BOARD") or None)
try:
    task_id = os.environ["HERMES_KANBAN_TASK"]
    run_id = int(os.environ["HERMES_KANBAN_RUN_ID"])
    if not kb.complete_task(
        conn,
        task_id,
        result=f"completed {{task_id}}",
        summary="detached controller worker",
        expected_run_id=run_id,
    ):
        raise SystemExit(3)
finally:
    conn.close()
''',
        encoding="utf-8",
    )
    shim.chmod(0o700)
    monkeypatch.setenv("HERMES_BIN", str(shim))

    svc = FleetService(cfg)
    res = svc.run(
        "finish detached chain",
        review_policy="never",
        budgets={"wall_seconds": 30},
        detach=True,
    )
    run_id = res.run_id
    status = res
    controller_identity = state_dir / "controllers" / f"{run_id}.pid.json"
    original = json.loads(controller_identity.read_text(encoding="utf-8"))
    original_pid = int(original["pid"])
    os.kill(original_pid, 9)
    gone_deadline = _t.time() + 5
    while Path(f"/proc/{original_pid}").exists() and _t.time() < gone_deadline:
        _t.sleep(0.05)
    assert not Path(f"/proc/{original_pid}").exists()
    # A status call recognizes the dead exact identity and starts one replacement.
    svc.status(run_id)
    replacement_deadline = _t.time() + 5
    replacement = original
    while _t.time() < replacement_deadline:
        replacement = json.loads(controller_identity.read_text(encoding="utf-8"))
        if int(replacement["pid"]) != original_pid:
            break
        _t.sleep(0.05)
    assert int(replacement["pid"]) != original_pid
    try:
        deadline = _t.time() + 30
        while _t.time() < deadline:
            status = svc.status(run_id)
            if status.state in {"completed", "failed", "cancelled"}:
                break
            _t.sleep(0.2)
        assert status.state == "completed", status.to_dict()
        assert isinstance(svc.orchestrator, KanbanOrchestrator)
        mapping = svc.orchestrator._mapping(svc.store, run_id)
        assert mapping is not None
        statuses = svc.orchestrator._statuses(mapping["child_task_ids"])
        assert set(statuses.values()) == {"done"}
        state = StateDB(state_dir / "hca.sqlite")
        assert state.active_lease_credits() == 0.0
        runs = state.list_runs(status=None)
        assert len(runs) == 2
        assert all(run.pid and run.status == "completed" for run in runs)
    finally:
        projection = svc.store.get_run(run_id)
        if projection and projection.state not in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            svc.stop(run_id)
        stop_controller(cfg.state_dir, run_id)
        tmux = TmuxManager(cfg.tmux_socket)
        for name in tmux.list_sessions():
            tmux.kill_session(name)


def test_c1_stop_terminates_owned_worker_and_reconciles(
    monkeypatch, tmp_path, hermes_runtime
):
    # Spawn one real, long-lived (idle) worker bound to a running task, then
    # stop the run and prove the owned process group is terminated, the Kanban
    # claim is released, and the run is cancelled — never completed.
    import time as _t

    from hca.run import RunSpec, RunState, new_run_id

    cfg, state_dir = _make_env(monkeypatch, tmp_path, hermes_runtime.src_path)
    monkeypatch.setattr(
        hermes_runtime.kb, "_fire_kanban_lifecycle_hook", lambda *args, **kwargs: None
    )
    state = StateDB(state_dir / "hca.sqlite")
    tmux = FakeTmux(hermes_runtime.src_path, worker_src=_IDLE_WORKER_SRC)
    orch = KanbanOrchestrator(
        cfg, state=state, tmux=tmux, board=cfg.board,
        enforce_sole_dispatcher=False,
    )
    store = RunStore(state_dir / "runs.sqlite")
    svc = FleetService(cfg, orchestrator=orch, store=store)

    spec = RunSpec(
        run_id=new_run_id(), goal="a long-running goal",
        board=cfg.board, created_at=_t.time(),
    )
    store.create_run(spec, state=RunState.QUEUED)
    store.set_state(spec.run_id, RunState.PLANNING)
    assert orch.plan(spec, store) == RunState.PLANNING
    store.set_state(spec.run_id, RunState.RUNNING)
    mapping = orch._mapping(store, spec.run_id)
    assert mapping is not None
    orch._dispatch_tick(1, mapping["child_task_ids"])

    mapping = orch._mapping(store, spec.run_id)
    work_id = mapping["child_task_ids"][0]
    rec = state.latest_run_for_task(cfg.board, work_id)
    assert rec is not None and rec.pid, "no worker pid was bound"
    pid = rec.pid
    assert orch._wait_pid_gone(pid, 0.0) is False  # worker is alive
    assert orch._statuses([work_id])[work_id] == "running"
    # a durable lease is held while the worker runs (governs admission)
    assert state.active_lease_credits() >= 1.0
    state.acquire_lease(
        "subagent-active-child",
        kind="subagent",
        owner=work_id,
        credits=1.0,
        meta={"phase": "active", "parent": work_id},
    )
    assert state.active_lease_credits(kind="subagent") == 1.0

    try:
        res = svc.stop(spec.run_id)
    finally:
        tmux.cleanup()

    assert res.state == "cancelled"
    assert orch._wait_pid_gone(pid, 3.0) is True  # process group terminated
    assert orch._statuses([work_id])[work_id] != "running"  # claim released
    assert state.active_lease_credits() == 0.0  # lease released on stop
    col = svc.collect(spec.run_id)
    assert col.data["result"]["outcome"] == "cancelled"


def test_c1_dead_worker_is_reclaimed_and_replaced_once(
    monkeypatch, tmp_path, hermes_runtime
):
    import signal
    import time as _t

    from hca.run import RunSpec, RunState, new_run_id

    cfg, state_dir = _make_env(monkeypatch, tmp_path, hermes_runtime.src_path)
    monkeypatch.setattr(
        hermes_runtime.kb, "_fire_kanban_lifecycle_hook", lambda *args, **kwargs: None
    )
    state = StateDB(state_dir / "hca.sqlite")
    tmux = FakeTmux(hermes_runtime.src_path, worker_src=_IDLE_WORKER_SRC)
    orch = KanbanOrchestrator(
        cfg,
        state=state,
        tmux=tmux,
        board=cfg.board,
        enforce_sole_dispatcher=False,
    )
    store = RunStore(state_dir / "runs.sqlite")
    spec = RunSpec(
        run_id=new_run_id(),
        goal="recover one crashed worker",
        board=cfg.board,
        created_at=_t.time(),
    )
    store.create_run(spec, state=RunState.QUEUED)
    store.set_state(spec.run_id, RunState.PLANNING)
    orch.plan(spec, store)
    store.set_state(spec.run_id, RunState.RUNNING)
    mapping = orch._mapping(store, spec.run_id)
    assert mapping is not None
    orch._dispatch_tick(1, mapping["child_task_ids"])

    mapping = orch._mapping(store, spec.run_id)
    work_id = mapping["child_task_ids"][0]
    first = state.latest_run_for_task(cfg.board, work_id)
    assert first is not None and first.pid and first.pid_start_ticks
    os.killpg(os.getpgid(first.pid), signal.SIGKILL)
    for proc in tmux.procs:
        if proc.pid == first.pid:
            proc.wait(timeout=5)
            break

    try:
        orch.tick(spec, store, dispatch=True)
        second = state.latest_run_for_task(cfg.board, work_id)
        assert second is not None
        assert second.run_id != first.run_id
        assert second.pid != first.pid
        assert second.status == "running"
        attempts = [run for run in state.list_runs(status=None) if run.task_id == work_id]
        assert sorted(run.status for run in attempts) == ["crashed", "running"]
        assert state.active_lease_credits() == 1.0
        assert orch._statuses([work_id])[work_id] == "running"
    finally:
        svc = FleetService(cfg, orchestrator=orch, store=store)
        svc.stop(spec.run_id)
        tmux.cleanup()
