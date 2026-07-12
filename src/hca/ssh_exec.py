"""Passwordless SSH helper for GB10 clusters (BatchMode)."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class SSHResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int
    cmd: list[str]


def ssh_command(
    host: str,
    remote_command: str,
    *,
    user: str = "",
    port: int = 22,
    connect_timeout: int = 8,
    batch_mode: bool = True,
    control_master: bool = False,
    multiplex_path: str = "",
) -> list[str]:
    target = f"{user}@{host}" if user else host
    cmd = ["ssh"]
    if batch_mode:
        cmd += ["-o", "BatchMode=yes"]
    cmd += [
        "-o",
        f"ConnectTimeout={connect_timeout}",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if control_master and multiplex_path:
        cmd += [
            "-o",
            "ControlMaster=auto",
            "-o",
            f"ControlPath={multiplex_path}",
            "-o",
            "ControlPersist=60",
        ]
    if port and port != 22:
        cmd += ["-p", str(port)]
    cmd += [target, remote_command]
    return cmd


def run_ssh(
    host: str,
    remote_command: str,
    *,
    user: str = "",
    port: int = 22,
    timeout: int = 60,
    connect_timeout: int = 8,
    batch_mode: bool = True,
    control_master: bool = False,
    multiplex_path: str = "",
) -> SSHResult:
    cmd = ssh_command(
        host,
        remote_command,
        user=user,
        port=port,
        connect_timeout=connect_timeout,
        batch_mode=batch_mode,
        control_master=control_master,
        multiplex_path=multiplex_path,
    )
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return SSHResult(
            ok=proc.returncode == 0,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
            cmd=cmd,
        )
    except subprocess.TimeoutExpired as exc:
        return SSHResult(
            ok=False,
            stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
            stderr=f"ssh timeout after {timeout}s",
            returncode=124,
            cmd=cmd,
        )


def probe_hostname(host: str, **kwargs) -> SSHResult:
    return run_ssh(host, "hostname && whoami", **kwargs)
