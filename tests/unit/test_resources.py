from hca.devices.base import DeviceSignals
from hca.models import BackendConfig, CapacityConfig, CapacitySnapshot, Engine, FleetConfig
from hca.resources import admit, estimate_task_credits
from hca.state import StateDB


def _healthy_cfg():
    # openai_compat 'capacity' is a models-probe; point at an unreachable
    # endpoint so the *device* path is what we exercise deterministically by
    # injecting device_signals and asserting the device reason wins first.
    return FleetConfig(
        capacity=CapacityConfig(
            memory_high=0.90,
            memory_low=0.75,
            disk_high=0.90,
            disk_strict_percent=True,
        ),
        backend=BackendConfig(engine=Engine.OPENAI_COMPAT, endpoint="http://127.0.0.1:9/v1"),
    )


def test_device_memory_hysteresis(tmp_path):
    db = StateDB(tmp_path / "s.sqlite")
    cfg = _healthy_cfg()
    high = DeviceSignals(adapter="test", mem_pressure=0.95, disk_pressure=0.1)
    mid = DeviceSignals(adapter="test", mem_pressure=0.80, disk_pressure=0.1)
    low = DeviceSignals(adapter="test", mem_pressure=0.70, disk_pressure=0.1)

    # crosses high → blocked, gate closes
    d = admit(cfg, db, device_signals=high)
    assert not d.allowed and "memory pressure" in d.reason
    # still above low → stays blocked (hysteresis, no flapping)
    d = admit(cfg, db, device_signals=mid)
    assert not d.allowed and "low watermark" in d.reason
    # drops below low → gate reopens (device no longer blocks; backend may)
    d = admit(cfg, db, device_signals=low)
    assert "memory pressure" not in d.reason


def test_device_disk_high_blocks(tmp_path):
    db = StateDB(tmp_path / "s.sqlite")
    cfg = _healthy_cfg()
    sig = DeviceSignals(adapter="test", mem_pressure=0.1, disk_pressure=0.95)
    d = admit(cfg, db, device_signals=sig)
    assert not d.allowed and "disk pressure" in d.reason


def test_large_disk_high_percentage_admits_with_absolute_free_space_warning(tmp_path):
    db = StateDB(tmp_path / "s.sqlite")
    cfg = _healthy_cfg()
    cfg.capacity.disk_strict_percent = False
    sig = DeviceSignals(
        adapter="test",
        mem_pressure=0.1,
        disk_pressure=0.964,
        disk_free_mb=138 * 1024,
    )

    decision = admit(cfg, db, device_signals=sig, requested_disk_mb=5 * 1024)

    assert decision.allowed is True
    assert "admitted with warning" in decision.reason
    assert "138.0 GiB" in decision.warnings[0]


def test_absolute_disk_floor_blocks_and_reopens_with_hysteresis(tmp_path):
    db = StateDB(tmp_path / "s.sqlite")
    cfg = _healthy_cfg()
    cfg.capacity.disk_strict_percent = False
    low = DeviceSignals(adapter="test", mem_pressure=0.1, disk_pressure=0.99, disk_free_mb=4 * 1024)
    mid = DeviceSignals(adapter="test", mem_pressure=0.1, disk_pressure=0.50, disk_free_mb=22 * 1024)
    high = DeviceSignals(adapter="test", mem_pressure=0.1, disk_pressure=0.50, disk_free_mb=26 * 1024)

    assert not admit(cfg, db, device_signals=low).allowed
    held = admit(cfg, db, device_signals=mid)
    assert not held.allowed and "resume requires 25.0 GiB" in held.reason
    assert admit(cfg, db, device_signals=high).allowed


def test_run_disk_budget_must_fit_above_absolute_reserve(tmp_path):
    db = StateDB(tmp_path / "s.sqlite")
    cfg = _healthy_cfg()
    cfg.capacity.disk_strict_percent = False
    sig = DeviceSignals(adapter="test", mem_pressure=0.1, disk_pressure=0.1, disk_free_mb=24 * 1024)

    decision = admit(cfg, db, device_signals=sig, requested_disk_mb=5 * 1024)

    assert not decision.allowed
    assert "does not fit above absolute reserve" in decision.reason


def test_unknown_mem_pressure_does_not_block(tmp_path):
    db = StateDB(tmp_path / "s.sqlite")
    cfg = _healthy_cfg()
    sig = DeviceSignals(adapter="test", mem_pressure=None, disk_pressure=0.1)
    d = admit(cfg, db, device_signals=sig)
    # unknown host mem must not be read as pressure; device does not block
    assert "memory pressure" not in d.reason


def test_estimate_credits_subagents():
    c = estimate_task_credits(task_class="llm-heavy", may_spawn_subagents=2, long_context=True)
    assert c > 2.0


def test_admit_respects_top_level_cap(tmp_path):
    db = StateDB(tmp_path / "s.sqlite")
    cfg = FleetConfig(
        capacity=CapacityConfig(max_top_level_runs=0, max_total_sequences=10),
        backend=BackendConfig(engine=Engine.OPENAI_COMPAT, endpoint="http://127.0.0.1:9/v1"),
    )
    # with max_top_level_runs=0, even healthy backend should block — but unhealthy also blocks
    d = admit(cfg, db, running_top_level=0)
    # endpoint 9 is unhealthy → not allowed
    assert d.allowed is False


def test_worker_attempt_admission_does_not_consume_top_level_mission_cap(tmp_path):
    db = StateDB(tmp_path / "s.sqlite")
    cfg = FleetConfig(
        capacity=CapacityConfig(max_top_level_runs=1, max_total_sequences=10),
        backend=BackendConfig(engine=Engine.OPENAI_COMPAT),
    )
    sig = DeviceSignals(adapter="test", mem_pressure=None, disk_pressure=None)

    decision = admit(
        cfg,
        db,
        running_top_level=1,
        enforce_top_level_cap=False,
        device_signals=sig,
    )

    assert decision.allowed is True


def test_core_admission_does_not_probe_profile_owned_endpoint(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "s.sqlite")
    cfg = _healthy_cfg()
    sig = DeviceSignals(adapter="test", mem_pressure=None, disk_pressure=None)

    def forbidden_probe(_cfg):
        raise AssertionError("core admission must not probe a profile-owned endpoint")

    monkeypatch.setattr("hca.resources.fetch_capacity", forbidden_probe)
    d = admit(cfg, db, device_signals=sig)
    assert d.allowed is True
    assert d.capacity is None


def test_optional_unhealthy_capacity_snapshot_blocks(tmp_path):
    db = StateDB(tmp_path / "s.sqlite")
    cfg = _healthy_cfg()
    sig = DeviceSignals(adapter="test", mem_pressure=None, disk_pressure=None)
    capacity = CapacitySnapshot(engine="openai_compat", healthy=False, detail="down")
    d = admit(cfg, db, device_signals=sig, capacity=capacity)
    assert d.allowed is False
    assert "backend unhealthy" in d.reason


def test_memory_pressure_hysteresis_requires_low_watermark_to_reopen(tmp_path):
    db = StateDB(tmp_path / "s.sqlite")
    cfg = _healthy_cfg()

    high = admit(
        cfg,
        db,
        device_signals=DeviceSignals(
            adapter="test", mem_pressure=cfg.capacity.memory_high + 0.01
        ),
    )
    assert high.allowed is False
    between_pressure = (
        cfg.capacity.memory_high + cfg.capacity.memory_low
    ) / 2
    between = admit(
        cfg,
        db,
        device_signals=DeviceSignals(adapter="test", mem_pressure=between_pressure),
    )
    assert between.allowed is False
    assert "low watermark" in between.reason
    unknown = admit(
        cfg,
        db,
        device_signals=DeviceSignals(adapter="test", mem_pressure=None),
    )
    assert unknown.allowed is False
    low = admit(
        cfg,
        db,
        device_signals=DeviceSignals(
            adapter="test", mem_pressure=cfg.capacity.memory_low - 0.01
        ),
    )
    assert low.allowed is True
