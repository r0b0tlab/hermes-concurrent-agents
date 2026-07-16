"""Golden worker-launch-spec tests mirroring Hermes ``_default_spawn``.

Deterministic path: build_worker_launch_spec is exercised with the Hermes
helper accessors monkeypatched, so the exact env/argv are pinned without a
live install. A live drift-guard (skipped without hermes) asserts the real
``_default_spawn`` source still sets every env key we emit.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Optional

import pytest

from hca import worker_launch as wl
from hca.worker_launch import WorkerLaunchError, build_worker_launch_spec


@dataclass
class FakeTask:
    id: str = "t_abc"
    assignee: Optional[str] = "hca-default-worker-01"
    current_run_id: Optional[int] = 42
    claim_lock: Optional[str] = "lock-xyz"
    workspace_path: Optional[str] = None
    branch_name: Optional[str] = None
    tenant: Optional[str] = None
    skills: Optional[list] = None
    model_override: Optional[str] = None
    goal_mode: bool = False
    goal_max_turns: Optional[int] = None
    max_runtime_seconds: Optional[int] = None


@pytest.fixture
def stub_helpers(monkeypatch, tmp_path):
    """Pin the Hermes helper accessors to deterministic values."""
    kdb = str(tmp_path / "kanban.db")
    wsroot = str(tmp_path / "workspaces")
    monkeypatch.setattr(wl, "kanban_db_path", lambda board: kdb)
    monkeypatch.setattr(wl, "workspaces_root", lambda board: wsroot)
    monkeypatch.setattr(wl, "resolve_profile_env", lambda p: f"/home/.hermes/profiles/{p}")
    monkeypatch.setattr(wl, "normalize_profile_name", lambda n: n)
    monkeypatch.setattr(wl, "resolve_hermes_argv", lambda: ["/usr/bin/hermes"])
    monkeypatch.setattr(wl, "resolve_worker_toolsets", lambda home: [])
    monkeypatch.setattr(wl, "resolve_terminal_timeout", lambda mx, cur: None)
    return {"kdb": kdb, "wsroot": wsroot}


def test_basic_launch_spec_env_and_argv(stub_helpers, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = build_worker_launch_spec(FakeTask(), str(ws), board="hca")
    env = spec.env()
    # Load-bearing env keys
    assert env["HERMES_KANBAN_TASK"] == "t_abc"
    assert env["HERMES_KANBAN_RUN_ID"] == "42"  # integer current_run_id
    assert env["HERMES_KANBAN_BOARD"] == "hca"
    assert env["HERMES_KANBAN_DB"] == stub_helpers["kdb"]
    assert env["HERMES_KANBAN_WORKSPACES_ROOT"] == stub_helpers["wsroot"]
    assert env["HERMES_PROFILE"] == "hca-default-worker-01"
    assert env["HERMES_KANBAN_CLAIM_LOCK"] == "lock-xyz"
    assert env["HERMES_HOME"].endswith("hca-default-worker-01")
    # workspace is an absolute existing dir → TERMINAL_CWD pinned
    assert env["TERMINAL_CWD"] == str(ws)
    # argv
    argv = spec.argv()
    assert argv == [
        "/usr/bin/hermes",
        "-p",
        "hca-default-worker-01",
        "--cli",
        "--accept-hooks",
        "chat",
        "-q",
        "work kanban task t_abc",
    ]
    # no goal mode → no -Q
    assert "-Q" not in argv


def test_goal_mode_sets_Q_and_env(stub_helpers, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    task = FakeTask(goal_mode=True, goal_max_turns=7)
    spec = build_worker_launch_spec(task, str(ws), board="hca")
    env = spec.env()
    assert env["HERMES_KANBAN_GOAL_MODE"] == "1"
    assert env["HERMES_KANBAN_GOAL_MAX_TURNS"] == "7"
    assert spec.argv()[-1] == "-Q"


def test_skills_model_branch_tenant(stub_helpers, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    task = FakeTask(
        skills=["python", "web"],
        model_override="qwen3-coder",
        branch_name="feat/x",
        tenant="acme",
    )
    spec = build_worker_launch_spec(task, str(ws), board="hca")
    argv = spec.argv()
    assert "--skills" in argv
    # each skill in its own pair
    assert argv.count("--skills") == 2
    i = argv.index("-m")
    assert argv[i + 1] == "qwen3-coder"
    assert "--toolsets" not in argv  # stubbed empty
    env = spec.env()
    assert env["HERMES_KANBAN_BRANCH"] == "feat/x"
    assert env["HERMES_TENANT"] == "acme"


def test_toolsets_from_profile(stub_helpers, monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(wl, "resolve_worker_toolsets", lambda home: ["files", "kanban"])
    spec = build_worker_launch_spec(FakeTask(), str(ws), board="hca")
    argv = spec.argv()
    i = argv.index("--toolsets")
    assert argv[i + 1] == "files,kanban"


def test_runtime_limit_sets_terminal_timeouts(stub_helpers, monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(wl, "resolve_terminal_timeout", lambda mx, cur: "1800" if mx else None)
    task = FakeTask(max_runtime_seconds=1800)
    spec = build_worker_launch_spec(task, str(ws), board="hca")
    env = spec.env()
    assert env["TERMINAL_TIMEOUT"] == "1800"
    assert env["TERMINAL_MAX_FOREGROUND_TIMEOUT"] == "1800"


def test_missing_run_id_raises_before_spawn(stub_helpers, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    task = FakeTask(current_run_id=None)
    with pytest.raises(WorkerLaunchError) as exc:
        build_worker_launch_spec(task, str(ws), board="hca")
    assert "current_run_id" in str(exc.value)


def test_noninteger_run_id_raises(stub_helpers, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    task = FakeTask(current_run_id="not-an-int")
    with pytest.raises(WorkerLaunchError):
        build_worker_launch_spec(task, str(ws), board="hca")


def test_missing_assignee_and_profile_raises(stub_helpers, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    task = FakeTask(assignee=None)
    with pytest.raises(WorkerLaunchError) as exc:
        build_worker_launch_spec(task, str(ws), board="hca")
    assert "assignee" in str(exc.value)


def test_explicit_profile_overrides_assignee(stub_helpers, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    # routing supplies a concrete slot profile distinct from the logical role
    spec = build_worker_launch_spec(
        FakeTask(assignee="coder"), str(ws), board="hca", profile="hca-default-worker-02"
    )
    assert spec.profile == "hca-default-worker-02"
    assert spec.env()["HERMES_PROFILE"] == "hca-default-worker-02"


def test_command_is_shell_safe(stub_helpers, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = build_worker_launch_spec(FakeTask(), str(ws), board="hca")
    cmd = spec.command()
    # the prompt with spaces must be single-quoted
    assert "'work kanban task t_abc'" in cmd
    assert cmd.startswith("/usr/bin/hermes -p hca-default-worker-01 --cli")


def test_hca_extra_env_merged(stub_helpers, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = build_worker_launch_spec(
        FakeTask(), str(ws), board="hca",
        hca_extra_env={"HCA_STATE_DB": "/x/hca.sqlite", "HCA_MAX_SUBAGENT_CREDITS": "0"},
    )
    env = spec.env()
    assert env["HCA_STATE_DB"] == "/x/hca.sqlite"
    assert env["HCA_MAX_SUBAGENT_CREDITS"] == "0"


# --- live drift guard against the real _default_spawn contract --------------


@pytest.mark.skipif(not shutil.which("hermes"), reason="hermes not on PATH")
def test_env_keys_match_default_spawn_source():
    """Every HERMES_* env key HCA emits must appear in _default_spawn source.

    A cheap, safe drift guard: if upstream renames an env var, this fails
    before a real fleet does. We introspect the source rather than execute
    the fire-and-forget spawner.
    """
    import inspect

    from hca.hermes_compat import import_kanban_db

    src = inspect.getsource(import_kanban_db()._default_spawn)
    emitted = {
        "HERMES_KANBAN_TASK",
        "HERMES_KANBAN_WORKSPACE",
        "HERMES_KANBAN_RUN_ID",
        "HERMES_KANBAN_CLAIM_LOCK",
        "HERMES_KANBAN_DB",
        "HERMES_KANBAN_WORKSPACES_ROOT",
        "HERMES_KANBAN_BOARD",
        "HERMES_KANBAN_BRANCH",
        "HERMES_KANBAN_GOAL_MODE",
        "HERMES_PROFILE",
        "TERMINAL_CWD",
        "TERMINAL_TIMEOUT",
    }
    missing = {k for k in emitted if k not in src}
    assert not missing, f"env keys not found in _default_spawn (drift): {missing}"


@pytest.mark.skipif(not shutil.which("hermes"), reason="hermes not on PATH")
def test_live_build_spec_from_fake_task_has_real_paths(tmp_path):
    """Against the real helpers, a spec resolves real kanban/workspaces paths."""
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = build_worker_launch_spec(FakeTask(), str(ws), board="hca-drift-probe")
    env = spec.env()
    assert env["HERMES_KANBAN_RUN_ID"] == "42"
    assert env["HERMES_KANBAN_DB"]  # real kanban_db_path resolved
    assert env["HERMES_KANBAN_WORKSPACES_ROOT"]
    # argv starts with the real hermes executable
    assert spec.argv()[0].endswith("hermes")
