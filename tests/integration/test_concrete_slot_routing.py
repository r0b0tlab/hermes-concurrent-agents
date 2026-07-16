"""Real-Kanban integration: concrete-slot routing + crash-safe spawn.

Drives HCA's reservation-first ``dispatch_tick`` against a real temporary
Hermes kanban board with a fake tmux, proving two independent tasks route to
distinct concrete profiles with integer run ids, a third is capacity-capped,
and an unreserved spawn raises (never a silent stuck claim).

The board path is asserted to live under the temp HERMES_HOME before any
writes, so a cached home can never pollute the real ~/.hermes.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sqlite3
import sys

import pytest

_HERMES = os.path.expanduser("~/.hermes/hermes-agent")
if os.path.isdir(_HERMES) and _HERMES not in sys.path:
    sys.path.insert(0, _HERMES)

pytestmark = pytest.mark.skipif(not shutil.which("hermes"), reason="hermes not on PATH")


class FakeTmux:
    """Records launches; never touches real tmux."""

    def __init__(self):
        self.calls: list[dict] = []
        self._pid = 5000

    def run_in_slot(self, name, command, *, env=None, workdir=None, log_path=None):
        self._pid += 1
        self.calls.append(
            {"slot": name, "env": dict(env or {}), "command": command, "pid": self._pid}
        )
        return self._pid


def _kb():
    from hca.hermes_compat import import_kanban_db

    return import_kanban_db()


def _profiles():
    return importlib.import_module("hermes_cli.profiles")


def _make_cfg(board, state_dir):
    from hca.config import load_fleet_config

    cfg = load_fleet_config(model="m", board=board, state_dir=str(state_dir))
    cfg.name = "t"
    cfg.profile_slots = {"coder": 2}
    cfg.capacity.max_top_level_runs = 8
    return cfg


@pytest.fixture
def hermes_env(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    kb = _kb()
    board = "hcart"
    # SAFETY: refuse to run if the board path is not under our temp home
    # (cached home would otherwise write into the real ~/.hermes).
    db_path = kb.kanban_db_path(board=board)
    if not str(db_path).startswith(str(home)):
        pytest.skip(f"HERMES_HOME cached; board path {db_path} not under {home}")
    kb.init_db(board=board)
    profiles = _profiles()
    for i in (1, 2):
        profiles.get_profile_dir(f"hca-t-coder-0{i}").mkdir(parents=True, exist_ok=True)
    return {"home": home, "board": board, "kb": kb, "db_path": db_path}


def _create_task(kb, db_path, board, title, assignee):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        tid = kb.create_task(
            conn, title=title, assignee=assignee, initial_status="blocked", board=board
        )
        conn.commit()
        return tid
    finally:
        conn.close()


def test_two_tasks_route_to_distinct_concrete_slots(hermes_env, tmp_path):
    from hca.kanban import dispatch_tick
    from hca.state import StateDB

    kb, board, db_path = hermes_env["kb"], hermes_env["board"], hermes_env["db_path"]
    _create_task(kb, db_path, board, "t1", "hca-t-coder-01")
    _create_task(kb, db_path, board, "t2", "hca-t-coder-02")

    cfg = _make_cfg(board, tmp_path / "state")
    state = StateDB(tmp_path / "state" / "hca.sqlite")
    tmux = FakeTmux()

    result = dispatch_tick(cfg, state, tmux, skip_sole_dispatcher_check=True, max_spawn=4)

    spawned_profiles = sorted(p for _, p, _ in result["spawned"])
    assert spawned_profiles == ["hca-t-coder-01", "hca-t-coder-02"]

    runs = state.list_runs(status="running")
    assert sorted(r.slot for r in runs) == ["hca-t-coder-01", "hca-t-coder-02"]
    for r in runs:
        assert int(r.run_id) >= 1  # integer current_run_id
        assert r.pid

    # tmux launched with the exact contract env for distinct profiles
    launched = {c["env"]["HERMES_PROFILE"] for c in tmux.calls}
    assert launched == {"hca-t-coder-01", "hca-t-coder-02"}
    for c in tmux.calls:
        assert c["env"]["HERMES_KANBAN_RUN_ID"].isdigit()
        assert c["env"]["HERMES_KANBAN_BOARD"] == board
        assert "--cli" in c["command"]


def test_third_task_on_busy_profile_is_capacity_capped(hermes_env, tmp_path):
    from hca.kanban import dispatch_tick
    from hca.state import StateDB

    kb, board, db_path = hermes_env["kb"], hermes_env["board"], hermes_env["db_path"]
    _create_task(kb, db_path, board, "t1", "hca-t-coder-01")
    _create_task(kb, db_path, board, "t2", "hca-t-coder-01")  # same profile

    cfg = _make_cfg(board, tmp_path / "state")
    state = StateDB(tmp_path / "state" / "hca.sqlite")
    tmux = FakeTmux()

    result = dispatch_tick(cfg, state, tmux, skip_sole_dispatcher_check=True, max_spawn=4)

    # exactly one spawned on the profile; the other is per-profile-capped and
    # stays ready — never a silent duplicate on the same concrete slot.
    assert len(result["spawned"]) == 1
    assert len(result["skipped_per_profile_capped"]) == 1
    # no duplicate live-slot rows
    running = state.list_runs(status="running")
    assert len(running) == 1


def test_unreserved_spawn_raises_not_none(hermes_env, tmp_path):
    """A task reaching spawn without a reservation must raise (auto-block),
    never return None (which upstream records as an invisible stuck claim)."""
    from hca.routing import Reservations
    from hca.kanban import make_tmux_spawn_fn
    from hca.state import StateDB
    from hca.worker_launch import WorkerLaunchError

    cfg = _make_cfg(hermes_env["board"], tmp_path / "state")
    state = StateDB(tmp_path / "state" / "hca.sqlite")
    tmux = FakeTmux()
    reservations = Reservations()  # empty — nothing reserved
    spawn_fn = make_tmux_spawn_fn(cfg, state, tmux, reservations)

    class _Task:
        id = "t_x"
        assignee = "hca-t-coder-01"
        current_run_id = 7

    with pytest.raises(WorkerLaunchError):
        spawn_fn(_Task(), str(tmp_path / "ws"), board=hermes_env["board"])
    # nothing launched
    assert not tmux.calls
