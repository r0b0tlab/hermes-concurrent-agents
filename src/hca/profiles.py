"""Least-privilege HCA slot profile generation.

Generated slot profiles are cloned from the user's *valid source Hermes
profile* through the current ``hermes profile create`` API, then tightened via
``hermes config set``. The generator:

  * preserves source model/provider/fallback configuration and lets Hermes clone
    the source's owner-only credential file — no credential value is passed in
    an HCA command, log, manifest, or generated config;
  * disables optional plugins in worker slots so plugin toolsets cannot bypass
    the role allowlist (the HCA team plugin belongs in the user/control profile);
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
    "general": "general-worker",
    "coder": "coder-worker",
    "research": "research-worker",
    "qa": "qa-worker",
    "creative": "creative-worker",
}

# Role-scoped ordinary work tools. Task lifecycle tools are additionally pinned
# by HERMES_KANBAN_TASK, but keeping ``kanban`` explicit makes the generated
# profile self-describing. Worker profiles never receive memory, messaging,
# cron, delegation, fleet, account, browser/computer-use, or other unrelated
# operator surfaces.
ROLE_TOOLSETS = {
    "orchestrator": ("kanban",),
    "general": ("terminal", "file", "kanban"),
    "coder": ("terminal", "file", "kanban"),
    "research": ("web", "file", "kanban"),
    "qa": ("terminal", "file", "kanban"),
    "creative": ("file", "image_gen", "kanban"),
}
DEFAULT_WORKER_TOOLSETS = ("file", "kanban")

# Operator / fleet / messaging / delegation powers filtered out of every HCA
# profile. A worker must not be able to drain or reconfigure a fleet, message
# channels, schedule cron automation, or spawn its own subagents.
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
    """Return a concrete role's least-privilege CLI toolsets."""
    return list(ROLE_TOOLSETS.get((role or "").lower(), DEFAULT_WORKER_TOOLSETS))


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


def _write_yaml_string_list(
    config_path: Path,
    section: str,
    key: str,
    values: Iterable[str],
) -> None:
    """Persist one controlled YAML sequence without coercing it to a string.

    Hermes ``config set`` intentionally coerces booleans/numbers but does not
    parse sequence literals. Passing JSON therefore writes a string that looks
    like a list; Hermes then falls back to the broad ``hermes-cli`` toolset.
    Keep scalar mutations on the authoritative CLI and patch only these two
    controlled string-list fields before running ``config check``.
    """
    clean_values = [str(value) for value in values if str(value)]
    text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    lines = text.splitlines()
    section_index: Optional[int] = None
    section_indent = 0
    for index, raw in enumerate(lines):
        clean = raw.split("#", 1)[0].rstrip()
        if clean.strip() == f"{section}:":
            indent = len(clean) - len(clean.lstrip())
            if indent == 0:
                section_index = index
                section_indent = indent
                break

    if section_index is None:
        if lines and lines[-1].strip():
            lines.append("")
        section_index = len(lines)
        lines.append(f"{section}:")

    section_end = len(lines)
    for index in range(section_index + 1, len(lines)):
        clean = lines[index].split("#", 1)[0].rstrip()
        if not clean.strip():
            continue
        indent = len(clean) - len(clean.lstrip())
        if indent <= section_indent:
            section_end = index
            break

    key_index: Optional[int] = None
    key_indent = section_indent + 2
    for index in range(section_index + 1, section_end):
        clean = lines[index].split("#", 1)[0].rstrip()
        if not clean.strip():
            continue
        indent = len(clean) - len(clean.lstrip())
        if indent == key_indent and clean.strip().startswith(f"{key}:"):
            key_index = index
            break

    if clean_values:
        block = [" " * key_indent + f"{key}:"] + [
            " " * (key_indent + 2) + f"- {json.dumps(value)}"
            for value in clean_values
        ]
    else:
        block = [" " * key_indent + f"{key}: []"]
    if key_index is None:
        lines[section_end:section_end] = block
    else:
        key_end = key_index + 1
        while key_end < section_end:
            clean = lines[key_end].split("#", 1)[0].rstrip()
            if clean.strip():
                indent = len(clean) - len(clean.lstrip())
                if indent <= key_indent:
                    break
            key_end += 1
        lines[key_index:key_end] = block

    rendered = "\n".join(lines).rstrip() + "\n"
    temp = config_path.with_name(f".{config_path.name}.hca-list-{time.time_ns()}")
    try:
        temp.write_text(rendered, encoding="utf-8")
        temp.chmod(0o600)
        os.replace(temp, config_path)
        config_path.chmod(0o600)
    finally:
        if temp.exists():
            temp.unlink()


def _write_yaml_top_level_string_list(
    config_path: Path,
    key: str,
    values: Iterable[str],
) -> None:
    """Persist one top-level controlled YAML string sequence."""
    clean_values = [str(value) for value in values if str(value)]
    text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    lines = text.splitlines()
    key_index: Optional[int] = None
    for index, raw in enumerate(lines):
        clean = raw.split("#", 1)[0].rstrip()
        if clean and not clean.startswith((" ", "\t")) and clean.startswith(f"{key}:"):
            key_index = index
            break
    if clean_values:
        block = [f"{key}:"] + [f"  - {json.dumps(value)}" for value in clean_values]
    else:
        block = [f"{key}: []"]
    if key_index is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(block)
    else:
        key_end = key_index + 1
        while key_end < len(lines):
            clean = lines[key_end].split("#", 1)[0].rstrip()
            if clean.strip() and not clean.startswith((" ", "\t")):
                break
            key_end += 1
        lines[key_index:key_end] = block

    rendered = "\n".join(lines).rstrip() + "\n"
    temp = config_path.with_name(f".{config_path.name}.hca-list-{time.time_ns()}")
    try:
        temp.write_text(rendered, encoding="utf-8")
        temp.chmod(0o600)
        os.replace(temp, config_path)
        config_path.chmod(0o600)
    finally:
        if temp.exists():
            temp.unlink()


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
    operations: tuple[tuple[str, str], ...] = (
        ("kanban.dispatch_in_gateway", "false"),
        ("delegation.max_concurrent_children", str(max(0, cfg.delegation_max_children))),
        ("delegation.max_spawn_depth", "1"),
        ("delegation.orchestrator_enabled", "false"),
        ("delegation.subagent_auto_approve", "false"),
        ("approvals.mode", "manual"),
        ("hooks_auto_accept", "false"),
    )
    for key, value in operations:
        result = runner("-p", profile, "config", "set", key, value)
        if int(getattr(result, "returncode", 1)) != 0:
            raise _command_failure(result, f"hermes -p {profile} config set {key}")
    _write_yaml_string_list(config_path, "plugins", "enabled", ())
    _write_yaml_string_list(
        config_path, "platform_toolsets", "cli", role_toolsets(role)
    )
    _write_yaml_top_level_string_list(config_path, "command_allowlist", ())
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
