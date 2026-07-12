import time
from pathlib import Path

from hca.state import RunRecord, StateDB


def test_state_roundtrip(tmp_path: Path):
    db = StateDB(tmp_path / "hca.sqlite")
    rec = RunRecord(
        board="hca",
        task_id="t1",
        run_id="r1",
        slot="hca-default-coder-01",
        node="local",
        tmux_session="hca-default-coder-01",
        pid=123,
        hermes_session_id="s1",
        workspace="/tmp/ws",
        status="running",
        started_at=time.time(),
        updated_at=time.time(),
        last_activity="start",
        error=None,
    )
    db.upsert_run(rec)
    rows = db.list_runs(status="running")
    assert len(rows) == 1
    assert rows[0].task_id == "t1"
    db.set_activity(kind="tool.start", message="terminal", board="hca", task_id="t1", run_id="r1")
    acts = db.recent_activity(10)
    assert acts[0]["kind"] == "tool.start"
    assert db.acquire_lease("L1", "subagent", "t1", credits=1.0)
    assert db.active_lease_credits("subagent") == 1.0
    db.release_lease("L1")
    assert db.active_lease_credits("subagent") == 0.0
