"""Additional unit tests for remaining v2 surface."""

from pathlib import Path
import time

from hca.bench import detect_knee, run_bench, LevelResult
from hca.logs import append_log, read_log, worker_log_id
from hca.state import StateDB, RunRecord
from hca.transcript import fetch_transcript
from hca.workspaces import mode_for_role
from hca.cli import main, build_parser


def test_mode_for_role():
    assert mode_for_role("coder") == "worktree"
    assert mode_for_role("research") == "shared-readonly"
    assert mode_for_role("orchestrator") == "none"


def test_logs_roundtrip(tmp_path: Path):
    append_log(str(tmp_path), "r1", "hello")
    append_log(str(tmp_path), "r1", "world")
    text = read_log(str(tmp_path), "r1", tail=10)
    assert "hello" in text and "world" in text


def test_worker_log_identity_namespaces_board_local_run_ids():
    first = worker_log_id("board-a", "t-one", 2)
    second = worker_log_id("board-b", "t-one", 2)
    assert first != second
    assert "/" not in first and ".." not in first
    assert worker_log_id("../unsafe", "t/one", 2) != "../unsafe--t/one--2"


def test_transcript_activity_fallback(tmp_path: Path):
    db = StateDB(tmp_path / "hca.sqlite")
    now = time.time()
    db.upsert_run(
        RunRecord(
            board="hca",
            task_id="t1",
            run_id="r1",
            slot="hca-x-coder-01",
            node="local",
            tmux_session="hca-x-coder-01",
            pid=1,
            hermes_session_id=None,
            workspace=None,
            status="running",
            started_at=now,
            updated_at=now,
            last_activity="start",
            error=None,
        )
    )
    db.set_activity(kind="tool.start", message="terminal", board="hca", task_id="t1", run_id="r1")
    data = fetch_transcript(db, "t1")
    assert data["run"]["task_id"] == "t1"
    assert data["source"] == "activity-fallback"
    assert data["messages"]


def test_detect_knee():
    levels = [
        LevelResult(1, 3, 3, 0, 0.1, 0.2, 0.15, 10.0, 0.0),
        LevelResult(2, 6, 6, 0, 0.2, 0.3, 0.25, 12.0, 0.0),
        LevelResult(4, 12, 6, 6, 1.0, 2.0, 1.5, 4.0, 0.5),
    ]
    rec, reason = detect_knee(levels)
    assert rec == 2
    assert "error" in reason


def test_bench_dry_run():
    r = run_bench(
        engine="vllm",
        endpoint="http://127.0.0.1:8000/v1",
        model="m",
        levels=[1, 2],
        dry_run=True,
    )
    assert r.dry_run
    assert len(r.levels) == 2


def test_parser_has_complete_commands():
    import argparse

    p = build_parser()
    subparsers = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    for cmd in [
        "version",
        "presets",
        "init",
        "doctor",
        "up",
        "drain",
        "down",
        "ps",
        "status",
        "watch",
        "peek",
        "attach",
        "logs",
        "activity",
        "transcript",
        "inspect",
        "explain",
        "dashboard",
        "plan",
        "bench",
        "task",
        "cluster",
    ]:
        assert cmd in subparsers.choices, f"missing subcommand: {cmd}"


def test_cli_smoke(tmp_path: Path):
    assert main(["plan", "--preset", "generic-linux", "--json"]) == 0
    assert main(["bench", "--dry-run", "--preset", "gb10-vllm", "--model", "x"]) == 0
    drain_dir = str(tmp_path / "drain")
    assert main(["drain", "--state-dir", drain_dir]) == 0
    assert main(["drain", "--clear", "--state-dir", drain_dir]) == 0
