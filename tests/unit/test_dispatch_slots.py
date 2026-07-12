"""Slot picking must never hand out a busy slot (respawn kills the worker)."""

import time
from pathlib import Path

from hca.config import load_fleet_config
from hca.kanban import pick_idle_slot
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
