import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hca.config import config_shape, fleet_from_dict, load_fleet_config
from hca.controller import (
    _paths,
    _safe_config,
    _write_private_json,
    launch_controller,
    stop_controller,
)
from hca.process_identity import proc_start_ticks
from hca.run import RunSpec, RunState, RunStore


def test_controller_snapshot_omits_endpoints_cluster_and_credentials(tmp_path):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path))
    cfg.backend.endpoint = "https://user:secret@example.invalid/v1"
    cfg.backend.metrics_url = "https://token@example.invalid/metrics"
    shaped = config_shape(cfg)
    shaped["cluster"]["nodes"] = [
        {"host": "private.example", "ssh_user": "alice", "endpoint": "secret"}
    ]
    cfg = fleet_from_dict(shaped)

    safe = _safe_config(cfg)
    rendered = json.dumps(safe)
    assert "secret" not in rendered
    assert "private.example" not in rendered
    assert "endpoint" not in rendered
    assert "backend" not in safe
    assert "cluster" not in safe
    assert safe["fleet"]["state_dir"] == str(tmp_path)


def test_completed_run_controller_starts_with_private_files_and_exits(tmp_path):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path))
    store = RunStore(tmp_path / "hca.sqlite")
    spec = RunSpec(run_id="run_controller_done", goal="done", board=cfg.board)
    store.create_run(spec, state=RunState.COMPLETED)

    pid = launch_controller(cfg, spec.run_id)
    controller_dir = tmp_path / "controllers"
    identity_path = controller_dir / f"{spec.run_id}.pid.json"
    config_path = controller_dir / f"{spec.run_id}.json"
    deadline = time.time() + 10
    identity = {}
    while time.time() < deadline:
        if identity_path.is_file():
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            if identity.get("state") == "exited":
                break
        time.sleep(0.05)

    assert int(identity["pid"]) == pid
    assert identity["state"] == "exited"
    assert os.stat(controller_dir).st_mode & 0o777 == 0o700
    assert os.stat(identity_path).st_mode & 0o777 == 0o600
    assert os.stat(config_path).st_mode & 0o777 == 0o600
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert "backend" not in payload and "cluster" not in payload
    # The exact PID is no longer live as this controller exited cleanly.
    proc = Path(f"/proc/{pid}")
    deadline = time.time() + 5
    while proc.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert not proc.exists()


def test_stale_controller_identity_never_signals_reused_pid(tmp_path):
    sleeper = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        ticks = proc_start_ticks(sleeper.pid)
        assert ticks is not None
        run_id = "run_stale_identity"
        identity = _paths(tmp_path, run_id)["identity"]
        _write_private_json(
            identity,
            {
                "pid": sleeper.pid,
                "start_ticks": ticks + 1,
                "run_id": run_id,
                "state": "running",
            },
        )
        assert stop_controller(tmp_path, run_id) is False
        assert sleeper.poll() is None
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)


def test_concurrent_launch_calls_serialize_to_one_spawn(tmp_path, monkeypatch):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path))
    spawned = {"pid": None, "count": 0}

    def fake_launch(_cfg, _run_id):
        if spawned["pid"] is None:
            # If launch_controller's file lock were absent, several threads
            # would pass this check before the first writes the winner.
            time.sleep(0.03)
            spawned["count"] += 1
            spawned["pid"] = 424242
        return spawned["pid"]

    monkeypatch.setattr("hca.controller._launch_controller_locked", fake_launch)
    with ThreadPoolExecutor(max_workers=8) as pool:
        pids = list(pool.map(lambda _: launch_controller(cfg, "run_launch_race"), range(8)))
    assert pids == [424242] * 8
    assert spawned["count"] == 1
