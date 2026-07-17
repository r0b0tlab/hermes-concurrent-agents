"""Slot picking must never hand out a busy slot (respawn kills the worker)."""

import time
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from hca.config import load_fleet_config
from hca.kanban import _reconcile_owned_worker_identities, pick_idle_slot
from hca.state import RunRecord, StateDB


def _running(slot: str, run_id: str) -> RunRecord:
    now = time.time()
    return RunRecord(
        board="hca",
        task_id=f"task-{run_id}",
        run_id=run_id,
        slot=slot,
        node="local",
        tmux_session=slot,
        pid=1234,
        hermes_session_id=None,
        workspace=None,
        status="running",
        started_at=now,
        updated_at=now,
        last_activity="spawned",
        error=None,
    )


def test_pick_idle_slot_skips_busy(tmp_path: Path):
    cfg = load_fleet_config(preset="gb10-vllm", model="m", state_dir=str(tmp_path))
    state = StateDB(tmp_path / "hca.sqlite")
    cursors: dict[str, int] = {}

    first = pick_idle_slot(cfg, state, cursors, "coder-worker")
    assert first == "hca-gb10-coder-01"

    state.upsert_run(_running("hca-gb10-coder-01", "r1"))
    second = pick_idle_slot(cfg, state, {}, "coder-worker")
    assert second == "hca-gb10-coder-02"


def test_pick_idle_slot_saturated_returns_none(tmp_path: Path):
    cfg = load_fleet_config(preset="gb10-vllm", model="m", state_dir=str(tmp_path))
    state = StateDB(tmp_path / "hca.sqlite")
    state.upsert_run(_running("hca-gb10-coder-01", "r1"))
    state.upsert_run(_running("hca-gb10-coder-02", "r2"))
    assert pick_idle_slot(cfg, state, {}, "coder-worker") is None


def test_pick_idle_slot_round_robins(tmp_path: Path):
    cfg = load_fleet_config(preset="gb10-vllm", model="m", state_dir=str(tmp_path))
    state = StateDB(tmp_path / "hca.sqlite")
    cursors: dict[str, int] = {}
    a = pick_idle_slot(cfg, state, cursors, "coder-worker")
    b = pick_idle_slot(cfg, state, cursors, "coder-worker")
    assert {a, b} == {"hca-gb10-coder-01", "hca-gb10-coder-02"}


def test_supervisor_replacement_budget_is_separate_and_bounded(tmp_path: Path):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path))
    state = StateDB(tmp_path / "hca.sqlite")
    blocked = []
    reclaimed = []

    class KB:
        @staticmethod
        def reclaim_task(_conn, _task_id, **_kwargs):
            reclaimed.append(_task_id)
            return True

        @staticmethod
        def block_task(_conn, task_id, *, reason):
            blocked.append((task_id, reason))
            return True

    def dead(task_id: str, run_id: str, slot: str):
        now = time.time()
        state.upsert_run(
            RunRecord(
                board=cfg.board,
                task_id=task_id,
                run_id=run_id,
                slot=slot,
                node="local",
                tmux_session=slot,
                pid=999_999,
                hermes_session_id=None,
                workspace=None,
                status="running",
                started_at=now,
                updated_at=now,
                last_activity="spawned",
                error=None,
                pid_start_ticks=1,
            )
        )

    conn = sqlite3.connect(":memory:")
    dead("other-run-task", "99", "slot-other")
    dead("task-1", "1", "slot-1")
    _reconcile_owned_worker_identities(
        KB,
        conn,
        cfg,
        state,
        owner_run_id="run-owner",
        max_supervisor_replacements=1,
        allowed_task_ids={"task-1"},
    )
    assert state.get_meta("supervisor_replacements:run-owner") == "1"
    assert blocked == []
    assert reclaimed == ["task-1"]
    other = state.latest_run_for_task(cfg.board, "other-run-task")
    assert other is not None and other.status == "running"

    dead("task-2", "2", "slot-2")
    _reconcile_owned_worker_identities(
        KB,
        conn,
        cfg,
        state,
        owner_run_id="run-owner",
        max_supervisor_replacements=1,
        allowed_task_ids={"task-2"},
    )
    conn.close()

    assert state.get_meta("supervisor_replacements:run-owner") == "1"
    assert blocked and blocked[0][0] == "task-2"
    assert "budget exhausted" in blocked[0][1]
    activities = state.recent_activity(20)
    assert any(
        row["kind"] == "attempt.terminated"
        and row["data"]["termination_class"] == "worker_crash"
        for row in activities
    )


def test_dead_worker_with_terminal_upstream_truth_is_settled_not_reclaimed(
    tmp_path: Path,
):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path))
    state = StateDB(tmp_path / "hca.sqlite")
    now = time.time()
    state.upsert_run(
        RunRecord(
            board=cfg.board,
            task_id="task-done",
            run_id="7",
            slot="slot-1",
            node="local",
            tmux_session="slot-1",
            pid=999_999,
            hermes_session_id=None,
            workspace=None,
            status="running",
            started_at=now,
            updated_at=now,
            last_activity="spawned",
            error=None,
            pid_start_ticks=1,
        )
    )
    reclaimed = []

    class KB:
        @staticmethod
        def get_task(_conn, task_id):
            assert task_id == "task-done"
            return SimpleNamespace(status="done")

        @staticmethod
        def reclaim_task(_conn, task_id, **_kwargs):
            reclaimed.append(task_id)
            return True

    conn = sqlite3.connect(":memory:")
    try:
        result = _reconcile_owned_worker_identities(
            KB,
            conn,
            cfg,
            state,
            owner_run_id="run-owner",
            max_supervisor_replacements=2,
            allowed_task_ids={"task-done"},
        )
    finally:
        conn.close()

    assert result == []
    assert reclaimed == []
    settled = state.latest_run_for_task(cfg.board, "task-done")
    assert settled is not None and settled.status == "completed"
    assert state.get_meta("supervisor_replacements:run-owner") == ""
    assert any(
        row["kind"] == "attempt.settled"
        and row["data"]["termination_class"] == "task_terminal"
        for row in state.recent_activity(10)
    )
