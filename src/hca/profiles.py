"""Least-privilege HCA slot profile generation.

Generated slot profiles are cloned from the user's *valid source Hermes
profile* through the current ``hermes profile create`` API, then tightened via
``hermes config set``. The generator:

  * preserves source model/provider/fallback configuration and lets Hermes clone
    the source's owner-only credential file — no credential value is passed in
    an HCA command, log, manifest, or generated config;
  * explicitly enables the pip plugin ``hca`` (Hermes plugins are opt-in);
  * makes HCA the sole dispatcher (``kanban.dispatch_in_gateway: false``);
  * disables worker delegation by default (``max_concurrent_children: 0``,
    ``max_spawn_depth: 1``, ``orchestrator_enabled: false``) so durable
    fan-out is visible Kanban work;
  * filters operator / fleet / messaging / delegation toolsets by role while
    preserving ordinary work tools;
  * never weakens the source approval policy (refuses to emit ``approvals_yolo``);
  * writes owner-only files, backs up existing config, and runs
    ``hermes -p <slot> config check`` before accepting a slot.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from hca.config import PACKAGE_DIR, FleetConfig

ROLE_TEMPLATES = {
    "orchestrator": "orchestrator",
    "coder": "coder-worker",
    "research": "research-worker",
    "qa": "qa-worker",
    "creative": "creative-worker",
}

# Ordinary work toolsets preserved for every HCA role (kanban is task-scoped at
# launch via HERMES_KANBAN_TASK). The top-level `toolsets` key is deprecated
# upstream; tool config is per-platform under `platform_toolsets.cli`.
BASE_WORKER_TOOLSETS = ("terminal", "web", "file", "skills", "todo", "memory", "kanban")

# Operator / fleet / messaging / delegation powers filtered out of worker and
# reviewer profiles. A worker must not be able to drain or reconfigure a fleet,
# message channels, schedule cron automation, or spawn its own subagents.
OPERATOR_TOOLSETS = ("messaging", "cronjob", "delegation", "fleet", "account")


class ProfileDerivationError(RuntimeError):
    """Raised when a generated profile cannot be derived safely."""


def profiles_src_dir() -> Path:
    return PACKAGE_DIR / "templates" / "profiles"


def hermes_home() -> Path:
    """Return the active Hermes root without caching environment state."""
    return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def hermes_profiles_root() -> Path:
    return hermes_home() / "profiles"


def source_profile_dir(name: str) -> Path:
    """Resolve ``default`` or a named profile to its independent HERMES_HOME."""
    normalized = (name or "default").strip().lower()
    if normalized == "default":
        return hermes_home()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", normalized):
        raise ProfileDerivationError(f"invalid Hermes source profile name: {name!r}")
    return hermes_profiles_root() / normalized


def slot_profile_name(fleet: str, role: str, index: int) -> str:
    return f"hca-{fleet}-{role}-{index:02d}"


def iter_slot_profiles(cfg: FleetConfig) -> Iterable[tuple[str, str, int, str]]:
    """yield role, template_dir_name, index, profile_name"""
    for role, count in cfg.profile_slots.items():
        tmpl = ROLE_TEMPLATES.get(role, f"{role}-worker")
        for i in range(1, int(count) + 1):
            yield role, tmpl, i, slot_profile_name(cfg.name, role, i)


def role_toolsets(role: str) -> list[str]:
    """The CLI toolsets a role's workers may use (least privilege)."""
    # Every HCA role gets ordinary work tools; none get operator/messaging/
    # delegation. The planner (orchestrator) keeps kanban to create/link tasks;
    # workers keep kanban but it is task-scoped by the launch env.
    return list(BASE_WORKER_TOOLSETS)


# ---------------------------------------------------------------------------
# Source-profile safety checks
# ---------------------------------------------------------------------------


def source_defines_yolo(config_text: str) -> bool:
    """True when a source config contains an approval bypass.

    The generated config is tightened to manual approvals, but refusing a source
    that is already in bypass mode avoids creating a briefly permissive clone if
    profile mutation fails halfway through.
    """
    in_approvals = False
    approvals_indent = -1
    for raw in config_text.splitlines():
        clean = raw.split("#", 1)[0].rstrip()
        if not clean.strip():
            continue
        indent = len(clean) - len(clean.lstrip())
        s = clean.strip()
        if indent == 0:
            in_approvals = s == "approvals:"
            approvals_indent = indent if in_approvals else -1
        if re.match(r"approvals?_yolo\s*:\s*(true|1|yes)\b", s, re.I):
            return True
        if re.match(r"yolo\s*:\s*(true|1|yes)\b", s, re.I):
            return True
        if re.match(r"hooks_auto_accept\s*:\s*(true|1|yes)\b", s, re.I):
            return True
        if in_approvals and indent > approvals_indent:
            if re.match(r"mode\s*:\s*['\"]?off['\"]?\s*$", s, re.I):
                return True
    return False


def _nested_string_list(config_text: str, section: str, key: str) -> list[str]:
    """Read a small nested YAML string-list without importing a YAML package."""
    lines = config_text.splitlines()
    section_indent: Optional[int] = None
    key_indent: Optional[int] = None
    values: list[str] = []
    for raw in lines:
        clean = raw.split("#", 1)[0].rstrip()
        if not clean.strip():
            continue
        indent = len(clean) - len(clean.lstrip())
        s = clean.strip()
        if section_indent is None:
            if s == f"{section}:":
                section_indent = indent
            continue
        if indent <= section_indent:
            break
        if key_indent is None:
            if s.startswith(f"{key}:"):
                key_indent = indent
                inline = s.split(":", 1)[1].strip()
                if inline.startswith("[") and inline.endswith("]"):
                    try:
                        parsed = json.loads(inline.replace("'", '"'))
                    except (ValueError, TypeError):
                        parsed = []
                    return [str(v) for v in parsed if isinstance(v, str)]
            continue
        if indent <= key_indent:
            break
        if s.startswith("-"):
            value = s[1:].strip().strip('"').strip("'")
            if value:
                values.append(value)
    return values


# ---------------------------------------------------------------------------
# Profile initialisation
# ---------------------------------------------------------------------------


def _command_failure(result: object, action: str) -> ProfileDerivationError:
    rc = getattr(result, "returncode", "?")
    stderr = str(getattr(result, "stderr", "") or "").strip()
    # Commands below never contain credential values; still avoid echoing arbitrary
    # config output into diagnostics. A short final stderr line is sufficient.
    detail = stderr.splitlines()[-1][:300] if stderr else "no diagnostic"
    return ProfileDerivationError(f"{action} failed (exit {rc}): {detail}")


def _configure_profile(
    cfg: FleetConfig,
    *,
    profile: str,
    role: str,
    config_path: Path,
    runner: Callable[..., object],
) -> None:
    """Tighten a cloned profile through Hermes' authoritative config CLI."""
    text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    plugins = _nested_string_list(text, "plugins", "enabled")
    if "hca" not in plugins:
        plugins.append("hca")

    operations: tuple[tuple[str, str], ...] = (
        ("plugins.enabled", json.dumps(sorted(set(plugins)))),
        ("kanban.dispatch_in_gateway", "false"),
        ("delegation.max_concurrent_children", str(max(0, cfg.delegation_max_children))),
        ("delegation.max_spawn_depth", "1"),
        ("delegation.orchestrator_enabled", "false"),
        ("delegation.subagent_auto_approve", "false"),
        ("platform_toolsets.cli", json.dumps(role_toolsets(role))),
        ("approvals.mode", "manual"),
        ("hooks_auto_accept", "false"),
        ("command_allowlist", "[]"),
    )
    for key, value in operations:
        result = runner("-p", profile, "config", "set", key, value)
        if int(getattr(result, "returncode", 1)) != 0:
            raise _command_failure(result, f"hermes -p {profile} config set {key}")
    checked = runner("-p", profile, "config", "check")
    if int(getattr(checked, "returncode", 1)) != 0:
        raise _command_failure(checked, f"hermes -p {profile} config check")


def init_profiles(
    cfg: FleetConfig,
    *,
    force: bool = False,
    dry_run: bool = False,
    source_config: Optional[str | Path] = None,
    source_profile: str = "default",
    runner: Optional[Callable[..., object]] = None,
) -> list[str]:
    """Create/refresh isolated slot profiles from a real Hermes source profile.

    New slots are provisioned with ``hermes profile create --clone-from`` so
    current Hermes owns directory layout, config migration, skills, and the
    owner-only credential file. HCA then applies only its least-privilege
    overrides through ``hermes config set``; model/provider/fallback settings
    remain source-profile authority. No credential value enters an HCA command,
    log, manifest, or generated config.

    ``force`` never re-clones credentials. It backs up and tightens the slot's
    existing config, preserving that slot's current provider and auth state.
    """
    source_profile = (source_profile or "default").strip().lower()
    source_path = (
        Path(source_config).expanduser()
        if source_config
        else source_profile_dir(source_profile) / "config.yaml"
    )
    if not source_path.is_file():
        raise ProfileDerivationError(
            f"source profile config not found: {source_path}; authenticate and "
            f"configure Hermes profile {source_profile!r} first"
        )
    source_text = source_path.read_text(encoding="utf-8")
    if source_defines_yolo(source_text):
        raise ProfileDerivationError(
            "source profile enables an approval bypass (approvals.mode=off, "
            "yolo, or hooks_auto_accept); refusing to clone it into unattended "
            "workers. Tighten the source approval policy first."
        )

    if source_config and source_path != source_profile_dir(source_profile) / "config.yaml":
        # A free-form path is useful for previews/tests but cannot be passed to
        # Hermes' clone API as a profile identity.
        raise ProfileDerivationError(
            "profile creation requires --source-profile, not an arbitrary "
            "--source-config path"
        )

    from hca.hermes_compat import run_hermes

    run = runner or run_hermes
    src_root = profiles_src_dir()
    dst_root = hermes_profiles_root()
    created: list[str] = []

    for role, tmpl, _index, profile in iter_slot_profiles(cfg):
        dst = dst_root / profile
        src = src_root / tmpl
        if dry_run:
            created.append(str(dst))
            continue

        new_profile = not dst.exists()
        if new_profile:
            description = (
                f"HCA {role} slot for fleet {cfg.name}; task-scoped Kanban worker "
                "with operator and messaging powers disabled."
            )
            result = run(
                "profile",
                "create",
                profile,
                "--clone-from",
                source_profile,
                "--no-alias",
                "--description",
                description,
            )
            if int(getattr(result, "returncode", 1)) != 0:
                raise _command_failure(result, f"create Hermes profile {profile}")
            if not dst.is_dir():
                raise ProfileDerivationError(
                    f"Hermes reported profile {profile!r} created but {dst} is missing"
                )
        elif not force:
            created.append(f"{profile} (config preserved; use --force to tighten)")
            continue

        dst.chmod(0o700)
        cfg_path = dst / "config.yaml"
        if not cfg_path.is_file():
            raise ProfileDerivationError(f"created profile has no config: {cfg_path}")
        backup = dst / f"config.yaml.hca-bak.{time.time_ns()}"
        shutil.copy2(cfg_path, backup)
        backup.chmod(0o600)
        try:
            _configure_profile(
                cfg, profile=profile, role=role, config_path=cfg_path, runner=run
            )
        except Exception:
            shutil.copy2(backup, cfg_path)
            cfg_path.chmod(0o600)
            raise

        soul_src = src / "SOUL.md"
        if soul_src.is_file():
            shutil.copy2(soul_src, dst / "SOUL.md")
        cfg_path.chmod(0o600)
        created.append(profile)
    return created
