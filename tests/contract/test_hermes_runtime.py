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


def test_worker_command_shape():
    from hca.hermes_compat import worker_command

    cmd = worker_command("hca-default-coder-01", "t_abc")
    assert "hermes" in cmd
    assert "work kanban task t_abc" in cmd
    assert "--cli" in cmd
