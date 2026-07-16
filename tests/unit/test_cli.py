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


def test_task_swarm_workers_is_rejected_not_ignored(capsys):
    # --workers must fail visibly (exit 2), never be silently ignored.
    rc = main(["task", "swarm", "do a thing", "--workers", "8"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not supported" in err
    assert "--workers" in err
