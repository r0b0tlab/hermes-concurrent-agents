"""Shared fixtures for integration tests that need a real Hermes Kanban.

The vertical-slice tests exercise the *actual* upstream ``hermes_cli.kanban_db``
(``create_task`` / ``decompose_triage_task`` / ``dispatch_once`` /
``complete_task``) against a real temporary Kanban DB. When Hermes is installed
(pip) that module is importable directly; in a source checkout the operator
points ``HCA_HERMES_SRC`` at the Hermes tree. If neither is available the test
*skips with a structured reason* — it never fabricates a pass.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from typing import Optional

import pytest

_KNOWN_SRC_CANDIDATES = (
    "/tmp/nous-hermes-agent-review",
    os.path.expanduser("~/.hermes/hermes-agent"),
)


def _locate_hermes_src() -> Optional[str]:
    """Return a source dir to add to ``sys.path``, ``""`` if already importable,
    or ``None`` if Hermes cannot be found."""
    try:
        importlib.import_module("hermes_cli.kanban_db")
        return ""  # already importable (installed)
    except Exception:
        pass
    for env in ("HCA_HERMES_SRC", "HERMES_SRC"):
        p = os.environ.get(env, "").strip()
        if p and os.path.isdir(os.path.join(p, "hermes_cli")):
            return p
    for cand in _KNOWN_SRC_CANDIDATES:
        if os.path.isdir(os.path.join(cand, "hermes_cli")):
            return cand
    return None


@dataclass
class HermesRuntime:
    kb: object
    src_path: str  # "" when installed; a dir when loaded from a checkout


@pytest.fixture(scope="session")
def hermes_runtime() -> HermesRuntime:
    src = _locate_hermes_src()
    if src is None:
        pytest.skip(
            "hermes_cli not importable — set HCA_HERMES_SRC to a Hermes source "
            "tree (or pip install Hermes) to run the real Kanban vertical slice"
        )
    if src:
        sys.path.insert(0, src)
    try:
        kb = importlib.import_module("hermes_cli.kanban_db")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"hermes_cli.kanban_db import failed: {exc}")
    # Resolve the real on-disk source dir for subprocess PYTHONPATH.
    resolved = src or os.path.dirname(os.path.dirname(getattr(kb, "__file__", "")))
    return HermesRuntime(kb=kb, src_path=resolved)
