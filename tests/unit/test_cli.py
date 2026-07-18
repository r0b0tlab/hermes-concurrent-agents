import json
from types import SimpleNamespace

import hca.cli as cli
from hca.cli import build_parser, main
from hca.config import load_fleet_config
from hca.observe import redact_text
from hca.service import FleetService, PreflightOrchestrator


def test_parser_has_core_commands():
    p = build_parser()
    # ensure subparsers exist
    assert p.parse_args(["presets"]).cmd == "presets"


def test_redact():
    text = "Authorization: Bearer SECRET123 and api_key=abc"
    out = redact_text(text, [r"(?i)authorization:\s*bearer\s+\S+", r"(?i)api[_-]?key\s*[:=]\s*\S+"])
    assert "SECRET123" not in out
    assert "abc" not in out


def test_main_presets_exit_zero():
    assert main(["presets"]) == 0


def test_down_slots_is_idempotent_and_preserves_active_without_kill(
    monkeypatch, tmp_path, capsys
):
    cfg = SimpleNamespace(name="fleet", state_dir=str(tmp_path), tmux_socket="sock")
    active = SimpleNamespace(tmux_session="hca-fleet-coder-01", board="b", run_id="7")

    class State:
        def list_runs(self, status=""):
            assert status == "running"
            return [active]

        def set_activity(self, **_kwargs):
            return None

    class Tmux:
        sessions = ["hca-fleet-coder-01", "hca-fleet-coder-02", "foreign"]

        def list_sessions(self):
            return list(self.sessions)

        def kill_session(self, name):
            self.sessions.remove(name)

        def signal_pane(self, *_args):
            raise AssertionError("active worker must not be signalled without --kill")

    tmux = Tmux()
    monkeypatch.setattr(cli, "_cfg_from_args", lambda _args: cfg)
    monkeypatch.setattr(cli, "_state", lambda _cfg: State())
    monkeypatch.setattr(cli, "TmuxManager", lambda _socket: tmux)
    args = SimpleNamespace(kill=False, slots=True, json=True)

    assert cli.cmd_down(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["affected"] == ["hca-fleet-coder-02"]
    assert first["retained_active_slots"] == ["hca-fleet-coder-01"]

    assert cli.cmd_down(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["affected"] == []
    assert second["retained_active_slots"] == ["hca-fleet-coder-01"]


def test_inspect_json_remediates_high_level_run_identifier(tmp_path, capsys):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path))
    service = FleetService(cfg, orchestrator=PreflightOrchestrator())
    started = service.run("goal")

    rc = main(
        [
            "inspect",
            started.run_id,
            "--state-dir",
            str(tmp_path),
            "--model",
            "m",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 2
    assert captured.err == ""
    assert payload["supplied_identifier_kind"] == "high_level_run_id"
    assert "hca run-status" in payload["remediation"]


def test_plan_json_never_serializes_connection_string(capsys):
    endpoint = "https://alice:sensitive@inference.example.invalid/v1"
    assert main(["plan", "--endpoint", endpoint, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["endpoint_scope"] == "remote"
    assert "endpoint" not in payload
    for forbidden in (endpoint, "alice", "sensitive", "inference.example.invalid"):
        assert forbidden not in str(payload)


def test_init_dry_run_has_no_state_or_connection_output(monkeypatch, tmp_path, capsys):
    endpoint = "https://alice:sensitive@inference.example.invalid/v1"
    state_dir = tmp_path / "must-not-exist"
    monkeypatch.setattr(cli, "init_profiles", lambda *_args, **_kwargs: ["hca-general-01"])
    assert (
        main(
            [
                "init",
                "--dry-run",
                "--endpoint",
                endpoint,
                "--state-dir",
                str(state_dir),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["endpoint_scope"] == "remote"
    assert "backend" not in payload
    assert not state_dir.exists()
    for forbidden in (endpoint, "alice", "sensitive", "inference.example.invalid"):
        assert forbidden not in str(payload)


def test_task_swarm_workers_is_rejected_not_ignored(capsys):
    # --workers must fail visibly (exit 2), never be silently ignored.
    rc = main(["task", "swarm", "do a thing", "--workers", "8"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not supported" in err
    assert "--workers" in err


def test_run_parser_exposes_shared_goal_contract():
    args = build_parser().parse_args(
        [
            "run",
            "ship it",
            "--project",
            "/tmp/p",
            "--constraint",
            "offline",
            "--acceptance",
            "tests pass",
            "--acceptance",
            "docs complete",
            "--independent-criteria",
            "--source-profile",
            "default",
            "--budget",
            "wall_seconds=60",
            "--team",
            "reviewed",
            "--concurrency",
            "2",
            "--review",
            "always",
            "--detach",
        ]
    )
    assert args.goal == "ship it"
    assert args.constraint == ["offline"]
    assert args.acceptance == ["tests pass", "docs complete"]
    assert args.independent_criteria is True
    assert args.source_profiles == ["default"]
    assert args.budget == ["wall_seconds=60"]
    assert args.detach is True
