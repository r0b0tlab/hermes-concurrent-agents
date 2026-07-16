"""Detached per-run controller with exact process identity and restart recovery."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from hca.config import FleetConfig, config_shape, fleet_from_dict
from hca.process_identity import proc_start_ticks, process_identity_matches
from hca.run import TERMINAL_STATES, RunState

_RUN_ID = re.compile(r"^run[-_][A-Za-z0-9_-]+$")
_TERMINAL = set(TERMINAL_STATES)
_STOP = False


def _controller_dir(state_dir: str | Path) -> Path:
    root = Path(state_dir).expanduser().resolve() / "controllers"
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root


def _validate_run_id(run_id: str) -> str:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run id")
    return run_id


def _paths(state_dir: str | Path, run_id: str) -> dict[str, Path]:
    rid = _validate_run_id(run_id)
    root = _controller_dir(state_dir)
    return {
        "config": root / f"{rid}.json",
        "identity": root / f"{rid}.pid.json",
        "lock": root / f"{rid}.lock",
        "launch_lock": root / f"{rid}.launch.lock",
        "log": root / f"{rid}.log",
    }


def _safe_config(cfg: FleetConfig) -> dict[str, Any]:
    """Serialize only scheduling data; never endpoints, nodes, or credentials."""
    shaped = config_shape(cfg)
    return {
        "fleet": shaped["fleet"],
        "capacity": shaped["capacity"],
        "profiles": shaped["profiles"],
        "delegation": shaped["delegation"],
        "approvals": {"yolo": False},
    }


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


# Backward-compatible private alias for existing controller diagnostics/tests.
_proc_start_ticks = proc_start_ticks


def _identity_alive(identity: dict[str, Any], run_id: str) -> bool:
    try:
        pid = int(identity["pid"])
        expected_ticks = int(identity["start_ticks"])
    except (KeyError, TypeError, ValueError):
        return False
    if not process_identity_matches(pid, expected_ticks):
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ")
    except OSError:
        return False
    return b"hca.controller" in cmdline and run_id.encode() in cmdline


def controller_alive(state_dir: str | Path, run_id: str) -> bool:
    identity_path = _paths(state_dir, run_id)["identity"]
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return _identity_alive(identity, run_id)


def _launch_controller_locked(cfg: FleetConfig, run_id: str) -> int:
    """Start one detached controller while the caller holds its launch lock."""
    paths = _paths(cfg.state_dir, run_id)
    if controller_alive(cfg.state_dir, run_id):
        identity = json.loads(paths["identity"].read_text(encoding="utf-8"))
        return int(identity["pid"])

    _write_private_json(paths["config"], _safe_config(cfg))
    log_fd = os.open(paths["log"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "hca.controller",
                "--state-dir",
                str(Path(cfg.state_dir).expanduser().resolve()),
                "--run-id",
                run_id,
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=log_fd,
            close_fds=True,
            start_new_session=True,
        )
    finally:
        os.close(log_fd)
    start_ticks = None
    for _ in range(50):
        start_ticks = _proc_start_ticks(proc.pid)
        if start_ticks is not None:
            break
        if proc.poll() is not None:
            break
        time.sleep(0.01)
    if start_ticks is None or proc.poll() is not None:
        raise RuntimeError("detached controller failed to start")
    _write_private_json(
        paths["identity"],
        {
            "pid": proc.pid,
            "start_ticks": start_ticks,
            "run_id": run_id,
            "started_at": time.time(),
            "heartbeat_at": time.time(),
            "state": "starting",
        },
    )
    threading.Thread(target=proc.wait, name=f"hca-reap-{run_id}", daemon=True).start()
    return int(proc.pid)


def launch_controller(cfg: FleetConfig, run_id: str) -> int:
    """Start exactly one detached controller and return its live PID."""
    paths = _paths(cfg.state_dir, run_id)
    lock_fd = os.open(paths["launch_lock"], os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return _launch_controller_locked(cfg, run_id)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def stop_controller(state_dir: str | Path, run_id: str, *, grace: float = 2.0) -> bool:
    """Stop only the exact PID/start-tick/run-id controller identity."""
    paths = _paths(state_dir, run_id)
    try:
        identity = json.loads(paths["identity"].read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not _identity_alive(identity, run_id):
        return False
    pid = int(identity["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + max(0.0, grace)
    while time.monotonic() < deadline:
        if not _identity_alive(identity, run_id):
            return True
        time.sleep(0.05)
    # Re-check exact identity immediately before escalation. PID reuse between
    # TERM and KILL must never target a replacement process.
    if not _identity_alive(identity, run_id):
        return True
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _identity_alive(identity, run_id):
            return True
        time.sleep(0.05)
    return not _identity_alive(identity, run_id)


def _handle_stop(_signum, _frame) -> None:
    global _STOP
    _STOP = True


def run_controller(state_dir: str, run_id: str) -> int:
    global _STOP
    _STOP = False
    paths = _paths(state_dir, run_id)
    lock_fd = os.open(paths["lock"], os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        identity_deadline = time.time() + 2.0
        while time.time() < identity_deadline:
            try:
                identity = json.loads(paths["identity"].read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                identity = {}
            if int(identity.get("pid", -1)) == os.getpid():
                break
            time.sleep(0.01)
        else:
            return 2
        payload = json.loads(paths["config"].read_text(encoding="utf-8"))
        cfg = fleet_from_dict(payload)
        cfg.state_dir = str(Path(state_dir).expanduser().resolve())

        # Delayed imports keep launch/identity helpers lightweight.
        from hca.service import FleetService

        service = FleetService(cfg, launch_controller=False)
        signal.signal(signal.SIGTERM, _handle_stop)
        signal.signal(signal.SIGINT, _handle_stop)
        spec = service.store.get_spec(run_id)
        if spec is None:
            return 2
        deadline = spec.created_at + max(1, int(spec.budgets.wall_seconds))
        interval = max(0.2, min(5.0, float(cfg.dispatch_interval_seconds or 1.0)))

        while not _STOP:
            projection = service.store.get_run(run_id)
            if projection is None:
                return 2
            if (
                projection.state in _TERMINAL
                or projection.state in {RunState.NEEDS_INPUT, RunState.STOPPING}
            ):
                break
            result = service.reconcile(run_id, dispatch=True)
            projection = service.store.get_run(run_id)
            identity = {
                "pid": os.getpid(),
                "start_ticks": _proc_start_ticks(os.getpid()),
                "run_id": run_id,
                "started_at": projection.created_at if projection else time.time(),
                "heartbeat_at": time.time(),
                "state": projection.state.value if projection else result.state,
            }
            _write_private_json(paths["identity"], identity)
            if projection is None or projection.state in _TERMINAL:
                break
            if projection.state in {RunState.NEEDS_INPUT, RunState.STOPPING}:
                break
            if time.time() >= deadline:
                service.store.append_event(
                    run_id,
                    "run.controller_budget_exhausted",
                    "controller wall-time budget exhausted",
                )
                service._safe_set(
                    run_id,
                    RunState.BLOCKED,
                    "controller wall-time budget exhausted; partial evidence preserved",
                )
                break
            time.sleep(interval)
        return 0
    finally:
        try:
            identity = json.loads(paths["identity"].read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            identity = {}
        if int(identity.get("pid", -1)) == os.getpid():
            identity["heartbeat_at"] = time.time()
            identity["state"] = "exited"
            _write_private_json(paths["identity"], identity)
        os.close(lock_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hca.controller")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        return run_controller(args.state_dir, args.run_id)
    except Exception as exc:
        print(f"controller failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
