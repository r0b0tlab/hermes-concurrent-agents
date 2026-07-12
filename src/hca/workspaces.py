"""Per-role workspace / git worktree policy."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class WorkspaceSpec:
    path: Path
    mode: str  # worktree | shared-readonly | none
    role: str
    task_id: str
    created: bool = False


def default_worktree_root(state_dir: str, fleet: str) -> Path:
    return Path(state_dir).expanduser() / "worktrees" / fleet


def ensure_worktree(
    *,
    repo: str,
    task_id: str,
    role: str,
    state_dir: str,
    fleet: str,
    branch: Optional[str] = None,
    mode: str = "worktree",
) -> WorkspaceSpec:
    """
    Create an isolated git worktree for a task when mode=worktree.
    Never shares a single checkout across concurrent writers.
    """
    repo_path = Path(repo).expanduser().resolve()
    if mode == "none":
        return WorkspaceSpec(path=repo_path, mode=mode, role=role, task_id=task_id)

    if mode == "shared-readonly":
        if not repo_path.is_dir():
            raise FileNotFoundError(f"repo not found: {repo_path}")
        return WorkspaceSpec(path=repo_path, mode=mode, role=role, task_id=task_id)

    if not (repo_path / ".git").exists() and not (repo_path / ".git").is_file():
        # not a git repo — fall back to a plain task directory under state
        root = default_worktree_root(state_dir, fleet) / role
        root.mkdir(parents=True, exist_ok=True)
        dest = root / task_id
        dest.mkdir(parents=True, exist_ok=True)
        return WorkspaceSpec(path=dest, mode="plain", role=role, task_id=task_id, created=True)

    root = default_worktree_root(state_dir, fleet) / role
    root.mkdir(parents=True, exist_ok=True)
    dest = root / task_id
    if dest.exists():
        return WorkspaceSpec(path=dest, mode="worktree", role=role, task_id=task_id, created=False)

    branch_name = branch or f"hca/{role}/{task_id}"
    # Prefer git worktree add
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", "-B", branch_name, str(dest)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # fallback: clone local
        proc2 = subprocess.run(
            ["git", "clone", str(repo_path), str(dest)],
            capture_output=True,
            text=True,
        )
        if proc2.returncode != 0:
            raise RuntimeError(
                f"worktree failed: {proc.stderr.strip()} / clone failed: {proc2.stderr.strip()}"
            )
    return WorkspaceSpec(path=dest, mode="worktree", role=role, task_id=task_id, created=True)


def remove_worktree(
    *,
    repo: str,
    workspace: str,
    force: bool = False,
) -> None:
    dest = Path(workspace).expanduser()
    repo_path = Path(repo).expanduser().resolve()
    if not dest.exists():
        return
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "remove"]
        + (["--force"] if force else [])
        + [str(dest)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 and dest.exists():
        shutil.rmtree(dest, ignore_errors=True)


def mode_for_role(role: str) -> str:
    r = (role or "").lower()
    if r in {"research", "qa"}:
        return "shared-readonly"
    if r in {"coder", "creative"}:
        return "worktree"
    if r == "orchestrator":
        return "none"
    return "worktree"
