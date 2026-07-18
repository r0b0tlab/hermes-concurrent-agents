"""Typed worker launch contract mirroring Hermes ``_default_spawn``.

HCA launches kanban workers inside durable tmux slots rather than as
fire-and-forget subprocesses, so it cannot call ``_default_spawn`` directly.
Instead it assembles the *same* load-bearing env/argv the upstream spawner
builds — profile, board, DB, workspaces root, TERMINAL_CWD, branch, tenant,
the integer ``current_run_id``, goal mode + ``-Q``, task-pinned skills, model
override, profile-effective toolsets, and runtime-derived terminal timeouts —
then hands ``(env, argv)`` to the tmux slot.

Reused Hermes seams (probed, with contained fallbacks so a private-helper
rename degrades a detail instead of crashing):
  * ``kanban_db.kanban_db_path`` / ``workspaces_root`` (required)
  * ``profiles.normalize_profile_name`` / ``resolve_profile_env`` (required)
  * ``kanban_db._resolve_hermes_argv`` (fallback: ``[hermes]``)
  * ``kanban_db._resolve_worker_cli_toolsets`` (fallback: none)
  * ``kanban_db._worker_terminal_timeout_env`` (fallback: none)

The spec never imports ``_default_spawn``; the golden tests pin the exact
env/argv so drift is caught, not silently absorbed.
"""

from __future__ import annotations

import os
import subprocess
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from hca.hermes_compat import (
    HermesCompatError,
    hermes_bin,
    import_kanban_db,
    import_profiles,
)


class WorkerLaunchError(RuntimeError):
    """Raised when a worker cannot be launched safely (fail before claim)."""


SESSION_ENV_PREFIX = "HERMES_SESSION_"


def worker_unset_env(environ: Optional[dict[str, str]] = None) -> list[str]:
    """Return parent UI/session keys that detached workers must not inherit."""
    source = os.environ if environ is None else environ
    return sorted(
        {"HERMES_TUI", *(key for key in source if key.startswith(SESSION_ENV_PREFIX))}
    )


# ---------------------------------------------------------------------------
# Probed Hermes helper accessors (contained fallbacks)
# ---------------------------------------------------------------------------


def _kb():
    return import_kanban_db()


def resolve_hermes_argv() -> list[str]:
    kb = _kb()
    fn = getattr(kb, "_resolve_hermes_argv", None)
    if callable(fn):
        try:
            argv = list(fn())
            if argv:
                return argv
        except Exception:
            pass
    return [hermes_bin()]


def attest_worker_workspace(task: Any, workspace: str) -> str:
    """Fail closed unless a project task resolves to an HCA child worktree.

    Hermes treats an existing linked worktree path as an already-materialized
    target.  Without this guard, submitting a linked canonical checkout can
    therefore cause a worker to run in that checkout rather than in a child
    worktree.  HCA project workers are restricted to ``.worktrees/<task>``
    descendants; scratch tasks retain their existing behavior.
    """
    root = Path(workspace).expanduser()
    kind = str(_task_attr(task, "workspace_kind", "") or "")
    if kind != "worktree":
        return str(root)
    if not root.is_absolute() or not root.is_dir():
        raise WorkerLaunchError(
            f"task {_task_attr(task, 'id', '')} worktree workspace must be an "
            f"existing absolute directory: {workspace!r}"
        )
    root = root.resolve(strict=True)

    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return completed.stdout.strip()

    try:
        top = Path(git("rev-parse", "--show-toplevel")).resolve(strict=True)
        git_dir = Path(git("rev-parse", "--absolute-git-dir")).resolve(strict=True)
        common_raw = Path(git("rev-parse", "--git-common-dir"))
        common_dir = (
            common_raw if common_raw.is_absolute() else root / common_raw
        ).resolve(strict=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkerLaunchError(
            f"task {_task_attr(task, 'id', '')} workspace is not a verifiable "
            "Git worktree"
        ) from exc
    if top != root:
        raise WorkerLaunchError("worker workspace is not the Git worktree root")
    if git_dir == common_dir:
        raise WorkerLaunchError(
            "worker workspace is the primary checkout, not a linked child worktree"
        )
    if root.parent.name != ".worktrees":
        raise WorkerLaunchError(
            "worker workspace is not under the repository's .worktrees directory"
        )
    return str(root)


def resolve_worker_toolsets(hermes_home: Optional[str]) -> list[str]:
    kb = _kb()
    fn = getattr(kb, "_resolve_worker_cli_toolsets", None)
    if callable(fn):
        try:
            out = fn(hermes_home)
            return list(out) if out else []
        except Exception:
            return []
    return []


def resolve_terminal_timeout(
    max_runtime_seconds: Optional[int], current: Optional[str]
) -> Optional[str]:
    kb = _kb()
    fn = getattr(kb, "_worker_terminal_timeout_env", None)
    if callable(fn):
        try:
            return fn(max_runtime_seconds, current)
        except Exception:
            return None
    return None


def kanban_db_path(board: Optional[str]) -> str:
    kb = _kb()
    fn = getattr(kb, "kanban_db_path", None)
    if not callable(fn):
        raise WorkerLaunchError("hermes_cli.kanban_db.kanban_db_path missing")
    return str(fn(board=board))


def workspaces_root(board: Optional[str]) -> str:
    kb = _kb()
    fn = getattr(kb, "workspaces_root", None)
    if not callable(fn):
        raise WorkerLaunchError("hermes_cli.kanban_db.workspaces_root missing")
    return str(fn(board=board))


def normalize_profile_name(name: str) -> str:
    try:
        profiles = import_profiles()
    except HermesCompatError:
        return name
    fn = getattr(profiles, "normalize_profile_name", None)
    return fn(name) if callable(fn) else name


def resolve_profile_env(profile: str) -> Optional[str]:
    """Resolve the profile-scoped HERMES_HOME; None if the profile is absent."""
    try:
        profiles = import_profiles()
    except HermesCompatError:
        return None
    fn = getattr(profiles, "resolve_profile_env", None)
    if not callable(fn):
        return None
    try:
        return fn(profile)
    except FileNotFoundError:
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# The launch spec
# ---------------------------------------------------------------------------


@dataclass
class WorkerLaunchSpec:
    profile: str
    task_id: str
    run_id: int
    board: str
    kanban_db: str
    workspaces_root: str
    workspace: str
    hermes_home: str = ""
    terminal_cwd: str = ""
    claim_lock: str = ""
    branch: str = ""
    tenant: str = ""
    goal_mode: bool = False
    goal_max_turns: Optional[int] = None
    skills: tuple[str, ...] = ()
    model_override: str = ""
    toolsets: tuple[str, ...] = ()
    terminal_timeout: str = ""
    foreground_timeout: str = ""
    hermes_argv: tuple[str, ...] = ()
    accept_hooks: bool = True
    # HCA-owned additions (plugin subagent ledger); not part of the Hermes
    # contract but injected so the plugin can find its state.
    hca_extra_env: dict[str, str] = field(default_factory=dict)

    def env(self) -> dict[str, str]:
        """Env overlay applied on top of the inherited (tmux) environment.

        Mirrors ``_default_spawn`` env assembly. Returns only the overlay
        keys — the tmux slot inherits the supervisor env and exports these
        on top, exactly as ``_default_spawn`` overlays onto ``os.environ``.
        """
        env: dict[str, str] = {}
        if self.hermes_home:
            env["HERMES_HOME"] = self.hermes_home
        if self.tenant:
            env["HERMES_TENANT"] = self.tenant
        env["HERMES_KANBAN_TASK"] = self.task_id
        env["HERMES_KANBAN_WORKSPACE"] = self.workspace
        # TERMINAL_CWD only when it is a real absolute directory (file_tools
        # rejects relative / sentinel values).
        if (
            self.terminal_cwd
            and os.path.isabs(self.terminal_cwd)
            and os.path.isdir(self.terminal_cwd)
        ):
            env["TERMINAL_CWD"] = self.terminal_cwd
        if self.branch:
            env["HERMES_KANBAN_BRANCH"] = self.branch
        # Integer run id is mandatory — assembled from task.current_run_id.
        env["HERMES_KANBAN_RUN_ID"] = str(int(self.run_id))
        if self.claim_lock:
            env["HERMES_KANBAN_CLAIM_LOCK"] = self.claim_lock
        if self.goal_mode:
            env["HERMES_KANBAN_GOAL_MODE"] = "1"
            if self.goal_max_turns is not None:
                env["HERMES_KANBAN_GOAL_MAX_TURNS"] = str(int(self.goal_max_turns))
        if self.terminal_timeout:
            env["TERMINAL_TIMEOUT"] = self.terminal_timeout
        if self.foreground_timeout:
            env["TERMINAL_MAX_FOREGROUND_TIMEOUT"] = self.foreground_timeout
        env["HERMES_KANBAN_DB"] = self.kanban_db
        env["HERMES_KANBAN_WORKSPACES_ROOT"] = self.workspaces_root
        env["HERMES_KANBAN_BOARD"] = self.board
        env["HERMES_PROFILE"] = self.profile
        # HCA plugin ledger env (subagent budget etc.)
        env.update(
            {
                key: value
                for key, value in self.hca_extra_env.items()
                if not key.startswith(SESSION_ENV_PREFIX)
            }
        )
        return env

    def argv(self) -> list[str]:
        """Full worker argv mirroring ``_default_spawn``."""
        argv = list(self.hermes_argv) or [hermes_bin()]
        argv += ["-p", self.profile, "--cli"]
        if self.accept_hooks:
            argv.append("--accept-hooks")
        for sk in self.skills:
            if sk:
                argv += ["--skills", sk]
        if self.model_override:
            argv += ["-m", self.model_override]
        if self.toolsets:
            argv += ["--toolsets", ",".join(self.toolsets)]
        argv += ["chat", "-q", f"work kanban task {self.task_id}"]
        if self.goal_mode:
            argv.append("-Q")
        return argv

    def command(self) -> str:
        """Shell-quoted argv for tmux ``respawn-pane`` (no secrets in argv)."""
        return " ".join(shlex.quote(a) for a in self.argv())


def _task_attr(task: Any, name: str, default: Any = None) -> Any:
    return getattr(task, name, default)


def build_worker_launch_spec(
    task: Any,
    workspace: str,
    *,
    board: Optional[str],
    profile: Optional[str] = None,
    hca_extra_env: Optional[dict[str, str]] = None,
) -> WorkerLaunchSpec:
    """Assemble a :class:`WorkerLaunchSpec` from a *claimed* Hermes task.

    Fails closed (``WorkerLaunchError``) if the task has no integer
    ``current_run_id`` or no assignee — a worker must never be launched
    without a run id after the task has been claimed.
    """
    task_id = str(_task_attr(task, "id", "") or "")
    if not task_id:
        raise WorkerLaunchError("task has no id")

    assignee = _task_attr(task, "assignee", None)
    if profile is None:
        if not assignee:
            raise WorkerLaunchError(f"task {task_id} has no assignee/profile")
        profile = normalize_profile_name(str(assignee))
    else:
        profile = normalize_profile_name(str(profile))

    # current_run_id is the integer lifecycle-ownership key. A claimed task
    # MUST carry it; anything else is a compat bug we refuse to paper over.
    run_id_raw = _task_attr(task, "current_run_id", None)
    if run_id_raw is None:
        raise WorkerLaunchError(
            f"task {task_id} has no current_run_id — refusing to spawn a "
            "worker without an integer run id (claimed-but-unspawned risk)"
        )
    try:
        run_id = int(run_id_raw)
    except (TypeError, ValueError) as exc:
        raise WorkerLaunchError(
            f"task {task_id} current_run_id={run_id_raw!r} is not an integer"
        ) from exc

    hermes_home = resolve_profile_env(profile) or ""
    kdb = kanban_db_path(board)
    wsroot = workspaces_root(board)

    abs_ws = ""
    if workspace:
        abs_ws = attest_worker_workspace(task, workspace)

    max_runtime = _task_attr(task, "max_runtime_seconds", None)
    tt = resolve_terminal_timeout(max_runtime, os.environ.get("TERMINAL_TIMEOUT"))
    ft = resolve_terminal_timeout(
        max_runtime, os.environ.get("TERMINAL_MAX_FOREGROUND_TIMEOUT")
    )

    skills_raw = _task_attr(task, "skills", None) or ()
    skills = tuple(s for s in skills_raw if s)
    toolsets = tuple(resolve_worker_toolsets(hermes_home or None))

    return WorkerLaunchSpec(
        profile=profile,
        task_id=task_id,
        run_id=run_id,
        board=str(board or ""),
        kanban_db=kdb,
        workspaces_root=wsroot,
        workspace=abs_ws,
        hermes_home=hermes_home,
        terminal_cwd=abs_ws,
        claim_lock=str(_task_attr(task, "claim_lock", "") or ""),
        branch=str(_task_attr(task, "branch_name", "") or ""),
        tenant=str(_task_attr(task, "tenant", "") or ""),
        goal_mode=bool(_task_attr(task, "goal_mode", False)),
        goal_max_turns=_task_attr(task, "goal_max_turns", None),
        skills=skills,
        model_override=str(_task_attr(task, "model_override", "") or ""),
        toolsets=toolsets,
        terminal_timeout=tt or "",
        foreground_timeout=ft or "",
        hermes_argv=tuple(resolve_hermes_argv()),
        hca_extra_env=dict(hca_extra_env or {}),
    )
