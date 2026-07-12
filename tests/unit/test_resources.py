from hca.models import Engine, FleetConfig, BackendConfig, CapacityConfig
from hca.resources import admit, estimate_task_credits
from hca.state import StateDB


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
