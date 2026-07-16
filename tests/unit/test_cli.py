import json

import hca.cli as cli
from hca.cli import build_parser, main
from hca.observe import redact_text


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
