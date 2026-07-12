"""Quarantined Hermes private API adapter (fail closed on signature drift)."""

from __future__ import annotations

import importlib
import inspect
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional


class HermesCompatError(RuntimeError):
    pass


@dataclass
class HermesInfo:
    version: str
    path: str
    has_dispatch_once: bool
    dispatch_signature: str


def hermes_bin() -> str:
    path = shutil.which("hermes")
    if not path:
        raise HermesCompatError("hermes not found on PATH")
    return path


def hermes_version() -> str:
    proc = subprocess.run(
        [hermes_bin(), "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise HermesCompatError(f"hermes --version failed: {proc.stderr}")
    return (proc.stdout or proc.stderr).strip()


def import_kanban_db():
    try:
        return importlib.import_module("hermes_cli.kanban_db")
    except ImportError as exc:
        # Try adding common install path
        candidate = os.path.expanduser("~/.hermes/hermes-agent")
        if candidate not in sys.path and os.path.isdir(candidate):
            sys.path.insert(0, candidate)
            try:
                return importlib.import_module("hermes_cli.kanban_db")
            except ImportError:
                pass
        raise HermesCompatError(
            f"cannot import hermes_cli.kanban_db ({exc}). "
            "Install Hermes Agent and ensure its package is importable."
        ) from exc


def inspect_dispatch_once() -> HermesInfo:
    version = hermes_version()
    kb = import_kanban_db()
    fn = getattr(kb, "dispatch_once", None)
    if fn is None:
        return HermesInfo(version, getattr(kb, "__file__", ""), False, "")
    sig = str(inspect.signature(fn))
    return HermesInfo(version, getattr(kb, "__file__", ""), True, sig)


REQUIRED_SPAWN_FN_HINTS = ("spawn_fn",)


def assert_dispatch_contract() -> HermesInfo:
    info = inspect_dispatch_once()
    if not info.has_dispatch_once:
        raise HermesCompatError(
            "hermes_cli.kanban_db.dispatch_once missing — HCA requires spawn_fn support"
        )
    if "spawn_fn" not in info.dispatch_signature:
        raise HermesCompatError(
            f"dispatch_once signature drift (no spawn_fn): {info.dispatch_signature}"
        )
    return info


def run_hermes(*args: str, env: Optional[dict] = None, timeout: int = 120) -> subprocess.CompletedProcess:
    cmd = [hermes_bin(), *args]
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, env=full_env, timeout=timeout)


def kanban_json(*args: str) -> Any:
    proc = run_hermes("kanban", *args, "--json")
    # some verbs use different flag order; retry without forcing if needed
    if proc.returncode != 0:
        proc = run_hermes("kanban", *args)
    if proc.returncode != 0:
        raise HermesCompatError(proc.stderr or proc.stdout or "kanban command failed")
    text = proc.stdout.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def dispatch_once_with_spawn(
    conn,
    spawn_fn: Callable,
    **kwargs,
):
    """Call Hermes dispatch_once; fail closed if API drifts."""
    info = assert_dispatch_contract()
    kb = import_kanban_db()
    return kb.dispatch_once(conn, spawn_fn=spawn_fn, **kwargs)


def default_worker_env(
    *,
    hermes_home: str,
    profile: str,
    task_id: str,
    run_id: str,
    claim_lock: str,
    board: str,
    kanban_db: str,
    workspace: str,
    workspaces_root: str,
    extra: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    env = {
        "HERMES_HOME": hermes_home,
        "HERMES_PROFILE": profile,
        "HERMES_KANBAN_TASK": task_id,
        "HERMES_KANBAN_RUN_ID": run_id,
        "HERMES_KANBAN_CLAIM_LOCK": claim_lock,
        "HERMES_KANBAN_BOARD": board,
        "HERMES_KANBAN_DB": kanban_db,
        "HERMES_KANBAN_WORKSPACE": workspace,
        "HERMES_KANBAN_WORKSPACES_ROOT": workspaces_root,
    }
    if extra:
        env.update(extra)
    return env


def worker_command(profile: str, task_id: str, *, yolo: bool = False) -> str:
    """Safe one-shot worker shape aligned with Hermes kanban workers."""
    parts = [
        "hermes",
        "-p",
        profile,
        "--cli",
        "--accept-hooks",
    ]
    if yolo:
        parts.append("--yolo")
    parts += ["chat", "-q", f"work kanban task {task_id}"]
    # join for bash -lc exec; caller should shlex carefully if embedding
    import shlex

    return " ".join(shlex.quote(p) for p in parts)
