"""tmux session management for durable HCA slots."""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional


class TmuxError(RuntimeError):
    pass


def sanitize_session_name(name: str) -> str:
    """tmux targets use ':'; session names must not contain ':'."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")
    return cleaned[:80] or "hca-slot"


@dataclass
class TmuxSession:
    name: str
    socket: str
    exists: bool
    pane_pid: Optional[int] = None


class TmuxManager:
    def __init__(self, socket: str = "hca-default"):
        self.socket = socket

    def _cmd(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["tmux", "-L", self.socket, *args]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise TmuxError(
                f"tmux failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
            )
        return proc

    def ensure_server(self) -> None:
        # start-server is a no-op if already running
        self._cmd("start-server", check=False)
        # A tmux server retains the environment from its creator. Parent chat
        # identity must not persist into current or future HCA worker panes.
        self.sanitize_server_environment()
        # remain-on-exit helps inspect dead panes
        self._cmd("set-option", "-g", "remain-on-exit", "on", check=False)

    def sanitize_server_environment(self) -> list[str]:
        """Remove every retained Hermes session-identity key from this server."""
        proc = self._cmd("show-environment", "-g", check=False)
        removed: list[str] = []
        if proc.returncode != 0:
            return removed
        for line in proc.stdout.splitlines():
            raw = line[1:] if line.startswith("-") else line
            key = raw.split("=", 1)[0]
            if not key.startswith("HERMES_SESSION_"):
                continue
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                continue
            self._cmd("set-environment", "-g", "-u", key, check=False)
            removed.append(key)
        return removed

    def has_session(self, name: str) -> bool:
        name = sanitize_session_name(name)
        proc = self._cmd("has-session", "-t", name, check=False)
        return proc.returncode == 0

    def list_sessions(self) -> list[str]:
        proc = self._cmd("list-sessions", "-F", "#{session_name}", check=False)
        if proc.returncode != 0:
            return []
        return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]

    def create_slot(self, name: str) -> str:
        """Create an idle durable slot session (no Hermes child yet)."""
        self.ensure_server()
        name = sanitize_session_name(name)
        if self.has_session(name):
            return name
        # Keep a long-lived process so the slot exists without model context
        proc = self._cmd(
            "new-session",
            "-d",
            "-s",
            name,
            "-x",
            "120",
            "-y",
            "40",
            "sleep",
            "2147483647",
            check=False,
        )
        if proc.returncode != 0 and not self.has_session(name):
            raise TmuxError(
                f"failed to create session {name}: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return name

    def kill_session(self, name: str) -> None:
        name = sanitize_session_name(name)
        self._cmd("kill-session", "-t", name, check=False)

    def pane_pid(self, name: str) -> Optional[int]:
        name = sanitize_session_name(name)
        proc = self._cmd(
            "display-message",
            "-p",
            "-t",
            name,
            "#{pane_pid}",
            check=False,
        )
        if proc.returncode != 0:
            return None
        try:
            return int(proc.stdout.strip())
        except ValueError:
            return None

    def capture_pane(self, name: str, lines: int = 40) -> str:
        name = sanitize_session_name(name)
        start = max(-lines, -10000)
        proc = self._cmd(
            "capture-pane",
            "-p",
            "-J",
            "-t",
            name,
            "-S",
            str(start),
            check=False,
        )
        if proc.returncode != 0:
            raise TmuxError(f"capture-pane failed for {name}: {proc.stderr.strip()}")
        return proc.stdout

    def run_in_slot(
        self,
        name: str,
        command: str,
        *,
        env: Optional[dict[str, str]] = None,
        unset_env: Optional[list[str]] = None,
        workdir: Optional[str] = None,
        log_path: Optional[str] = None,
    ) -> int:
        """
        Replace the slot's pane process with `exec <command>` so pane_pid == worker pid.
        Does not use send-keys for task content.
        """
        self.ensure_server()
        name = sanitize_session_name(name)
        if not self.has_session(name):
            self.create_slot(name)

        env = env or {}
        exports = " ".join(
            f"{shlex.quote(k)}={shlex.quote(v)}" for k, v in sorted(env.items())
        )
        unset_names = []
        for key in unset_env or []:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise TmuxError(f"invalid environment variable name: {key!r}")
            unset_names.append(key)
        unset = f"unset {' '.join(unset_names)}; " if unset_names else ""
        export = f"export {exports}; " if exports else ""
        cd = f"cd {shlex.quote(workdir)} && " if workdir else ""
        # close any pipe left over from the previous run of this pane
        self._cmd("pipe-pane", "-t", name, check=False)
        # respawn-pane -k kills current pane process and runs new command
        shell = f"{cd}{unset}{export}exec {command}"
        self._cmd("respawn-pane", "-k", "-t", name, "bash", "-lc", shell)
        if log_path:
            self._cmd(
                "pipe-pane",
                "-t",
                name,
                f"cat >> {shlex.quote(log_path)}",
                check=False,
            )
        pid = self.pane_pid(name)
        if pid is None:
            raise TmuxError(f"no pane pid after respawn for {name}")
        return pid

    def signal_pane(self, name: str, signal: str = "INT") -> None:
        pid = self.pane_pid(name)
        if pid is None:
            return
        subprocess.run(["kill", f"-{signal}", str(pid)], check=False)

    def attach_command(self, name: str) -> list[str]:
        name = sanitize_session_name(name)
        return ["tmux", "-L", self.socket, "attach-session", "-t", name]
