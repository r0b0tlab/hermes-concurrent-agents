"""Unit tests for logical-role → concrete-slot routing (no silent fallback)."""

from __future__ import annotations

from pathlib import Path

from hca.config import load_fleet_config
from hca.routing import (
    Reservations,
    Unroutable,
    concrete_slots,
    resolve_role_hint,
    route_task,
    worker_slots,
)
from hca.state import RunRecord, StateDB


def _cfg(tmp_path: Path):
    return load_fleet_config(preset="gb10-vllm", model="m", state_dir=str(tmp_path))


def test_concrete_slots_have_stable_identity(tmp_path):
    cfg = _cfg(tmp_path)
    slots = concrete_slots(cfg)
    names = {s.profile for s in slots}
    # every slot name is unique and concrete
    assert len(names) == len(slots)
    assert all(s.profile.startswith(f"hca-{cfg.name}-") for s in slots)


def test_resolve_known_and_unknown_hints():
    assert resolve_role_hint("coding") == ("coder", None)
    assert resolve_role_hint("research") == ("research", None)
    role, err = resolve_role_hint("wizardry")
    assert role is None and "unknown" in err
    # None / empty → any worker
    assert resolve_role_hint(None) == ("", None)
    assert resolve_role_hint("  ") == ("", None)


def test_unknown_role_is_unroutable_not_coder(tmp_path):
    cfg = _cfg(tmp_path)
    state = StateDB(tmp_path / "hca.sqlite")
    res = Reservations()
    out = route_task(cfg, state, res, task_id="t1", role_hint="teleportation")
    assert isinstance(out, Unroutable)
    assert "teleportation" in out.reason
    # nothing reserved on failure
    assert not res.reserved


def test_route_reserves_distinct_slots(tmp_path):
    cfg = _cfg(tmp_path)
    state = StateDB(tmp_path / "hca.sqlite")
    res = Reservations()
    a = route_task(cfg, state, res, task_id="t1", role_hint="coding")
    b = route_task(cfg, state, res, task_id="t2", role_hint="coding")
    assert a.profile != b.profile
    assert res.reserved == {a.profile, b.profile}


def test_route_capacity_exhaustion_is_visible(tmp_path):
    cfg = _cfg(tmp_path)
    # shrink coder pool to 1 to force exhaustion
    cfg.profile_slots = {"coder": 1}
    state = StateDB(tmp_path / "hca.sqlite")
    res = Reservations()
    first = route_task(cfg, state, res, task_id="t1", role_hint="coding")
    assert not isinstance(first, Unroutable)
    second = route_task(cfg, state, res, task_id="t2", role_hint="coding")
    assert isinstance(second, Unroutable)
    assert "busy" in second.reason or "capacity" in second.reason.lower()


def test_running_slot_is_not_reoffered(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.profile_slots = {"coder": 2}
    state = StateDB(tmp_path / "hca.sqlite")
    slots = worker_slots(cfg)
    # mark the first worker slot as running
    busy_profile = slots[0].profile
    now = 1.0
    state.upsert_run(
        RunRecord(
            board="hca", task_id="x", run_id="r1", slot=busy_profile, node="local",
            tmux_session=busy_profile, pid=1, hermes_session_id=None, workspace=None,
            status="running", started_at=now, updated_at=now, last_activity="s", error=None,
        )
    )
    res = Reservations()
    out = route_task(cfg, state, res, task_id="t1", role_hint="coding")
    assert out.profile != busy_profile


def test_no_matching_role_slot_is_unroutable(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.profile_slots = {"coder": 1}  # no research slots
    state = StateDB(tmp_path / "hca.sqlite")
    res = Reservations()
    out = route_task(cfg, state, res, task_id="t1", role_hint="research")
    assert isinstance(out, Unroutable)
    assert "no concrete slot" in out.reason
