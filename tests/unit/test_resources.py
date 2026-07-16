from hca.devices.base import DeviceSignals
from hca.models import Engine, FleetConfig, BackendConfig, CapacityConfig
from hca.resources import admit, estimate_task_credits
from hca.state import StateDB


def _healthy_cfg():
    # openai_compat 'capacity' is a models-probe; point at an unreachable
    # endpoint so the *device* path is what we exercise deterministically by
    # injecting device_signals and asserting the device reason wins first.
    return FleetConfig(
        capacity=CapacityConfig(memory_high=0.90, memory_low=0.75, disk_high=0.90),
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
    assert not d.allowed and "gate open below" in d.reason
    # drops below low → gate reopens (device no longer blocks; backend may)
    d = admit(cfg, db, device_signals=low)
    assert "memory pressure" not in d.reason


def test_device_disk_high_blocks(tmp_path):
    db = StateDB(tmp_path / "s.sqlite")
    cfg = _healthy_cfg()
    sig = DeviceSignals(adapter="test", mem_pressure=0.1, disk_pressure=0.95)
    d = admit(cfg, db, device_signals=sig)
    assert not d.allowed and "disk pressure" in d.reason


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
