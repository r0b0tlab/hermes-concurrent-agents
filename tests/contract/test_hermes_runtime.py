"""Contract tests against installed Hermes Agent."""

from __future__ import annotations

import os
import shutil
import sys

import pytest

# Ensure local hermes source is importable if present
_HERMES = os.path.expanduser("~/.hermes/hermes-agent")
if os.path.isdir(_HERMES) and _HERMES not in sys.path:
    sys.path.insert(0, _HERMES)


@pytest.mark.skipif(not shutil.which("hermes"), reason="hermes not on PATH")
def test_hermes_version_parseable():
    from hca.hermes_compat import hermes_version

    ver = hermes_version()
    assert "Hermes" in ver or "hermes" in ver.lower() or ver


@pytest.mark.skipif(not shutil.which("hermes"), reason="hermes not on PATH")
def test_dispatch_once_spawn_fn_contract():
    from hca.hermes_compat import assert_dispatch_contract

    info = assert_dispatch_contract()
    assert info.has_dispatch_once
    assert "spawn_fn" in info.dispatch_signature


@pytest.mark.skipif(not shutil.which("hermes"), reason="hermes not on PATH")
def test_installed_hermes_is_supported_lane():
    """The installed Hermes must satisfy the full HCA contract surface."""
    from hca.hermes_compat import classify_lane, probe_capabilities, provenance

    caps = probe_capabilities()
    assert caps.supported(), f"installed Hermes missing capabilities: {caps.missing()}"
    lane, reason = classify_lane(provenance().version_line, caps)
    # Installed baseline is the verified stable release v0.18.2.
    assert lane in {"stable", "edge"}, reason
    # These load-bearing seams must be present against the real module.
    assert "current_run_id" in caps.task_fields
    assert "claim_lock" in caps.task_fields
    assert {"spawn_fn", "board", "max_spawn"} <= caps.dispatch_params


@pytest.mark.skipif(not shutil.which("hermes"), reason="hermes not on PATH")
def test_compatibility_report_is_wellformed():
    """Report used by `hca doctor --json` — never touches a live gateway."""
    from hca.hermes_compat import compatibility_report

    # Inject a synthetic gateway probe so we do not disturb any running one.
    report = compatibility_report(
        "hca-test-board",
        gateway_pid_fn=lambda: None,
        dispatch_flag_fn=lambda: True,
    )
    assert report["lane"] in {"stable", "edge", "unsupported"}
    assert "provenance" in report and report["provenance"]["semver"]
    assert report["dispatcher_ownership"]["board"] == "hca-test-board"
    assert report["dispatcher_ownership"]["conflict"] is False
    assert report["subagent_hook_keys"]["correlation_key"] == "child_session_id"


def test_worker_command_shape():
    from hca.hermes_compat import worker_command

    cmd = worker_command("hca-default-coder-01", "t_abc")
    assert "hermes" in cmd
    assert "work kanban task t_abc" in cmd
    assert "--cli" in cmd
