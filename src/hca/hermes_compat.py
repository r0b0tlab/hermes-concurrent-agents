"""Quarantined Hermes private-API adapter.

HCA depends on a handful of load-bearing Hermes internals: the kanban
``dispatch_once`` scheduler, the ``Task`` row shape, the concrete worker
launch env/argv, and the plugin subagent hooks. Those are not a public,
versioned API, so instead of trusting a version string we *probe the
actual installed module* for the capabilities we need and fail closed with
an actionable message when a required seam is missing or a foreign
dispatcher could own our board.

Everything here is introspection over the installed ``hermes_cli`` /
``gateway`` packages. The probe helpers accept injected modules/callables
so the classification logic is unit-testable without a live Hermes install
or (critically) touching a running gateway.
"""

from __future__ import annotations

import importlib
import inspect
import contextlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional


class HermesCompatError(RuntimeError):
    """Raised when the installed Hermes cannot satisfy the HCA contract."""


_IMPORT_WARNINGS: list[str] = []


def _known_optional_plugin_diagnostic(line: str) -> bool:
    low = line.lower()
    return "plugin" in low and any(
        marker in low
        for marker in (
            "optional",
            "unavailable",
            "missing dependency",
            "missing optional",
            "could not import",
            "failed to import",
        )
    )


def _import_module_with_clean_streams(name: str):
    """Capture only known optional-plugin discovery noise during import."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            module = importlib.import_module(name)
    finally:
        for text, target in ((stdout.getvalue(), sys.stdout), (stderr.getvalue(), sys.stderr)):
            for line in text.splitlines():
                if _known_optional_plugin_diagnostic(line):
                    if line not in _IMPORT_WARNINGS:
                        _IMPORT_WARNINGS.append(line)
                else:
                    print(line, file=target)
    return module


def compatibility_import_warnings() -> list[str]:
    """Known optional-plugin diagnostics captured from Hermes imports."""
    return list(_IMPORT_WARNINGS)


# ---------------------------------------------------------------------------
# Required capabilities (the HCA contract surface)
# ---------------------------------------------------------------------------

# dispatch_once keyword parameters HCA relies on to run its own spawn_fn,
# pin the board, and cap concurrency per concrete profile slot.
REQUIRED_DISPATCH_PARAMS: frozenset[str] = frozenset(
    {"spawn_fn", "board", "max_spawn", "max_in_progress", "dry_run"}
)
# Optional-but-used: absence downgrades a feature, not the whole lane.
OPTIONAL_DISPATCH_PARAMS: frozenset[str] = frozenset(
    {"max_in_progress_per_profile", "default_assignee", "stale_timeout_seconds"}
)

# DispatchResult fields HCA reads to project lifecycle/capacity telemetry.
REQUIRED_DISPATCH_RESULT_FIELDS: frozenset[str] = frozenset(
    {"reclaimed", "promoted", "spawned", "crashed", "skipped_nonspawnable"}
)

# Task columns the worker launch contract maps. current_run_id is the
# integer lifecycle-ownership key (HCA previously guessed at active_run_id).
REQUIRED_TASK_FIELDS: frozenset[str] = frozenset(
    {"id", "assignee", "current_run_id", "claim_lock", "workspace_path"}
)

# kanban_db / profiles helpers HCA reuses rather than reimplementing.
REQUIRED_KB_HELPERS: frozenset[str] = frozenset(
    {"dispatch_once", "kanban_db_path", "workspaces_root"}
)
REQUIRED_PROFILE_HELPERS: frozenset[str] = frozenset(
    {"normalize_profile_name", "resolve_profile_env"}
)

# Plugin subagent hook payload keys. NOTE: subagent_stop carries only
# child_session_id (no child_subagent_id), so child_session_id is the
# durable start/stop correlation key. See Task 4 subagent leases.
SUBAGENT_START_KEYS: frozenset[str] = frozenset(
    {
        "parent_session_id",
        "parent_turn_id",
        "parent_subagent_id",
        "child_session_id",
        "child_subagent_id",
        "child_role",
        "child_goal",
    }
)
SUBAGENT_STOP_KEYS: frozenset[str] = frozenset(
    {
        "parent_session_id",
        "parent_turn_id",
        "child_session_id",
        "child_role",
        "child_summary",
        "child_status",
        "duration_ms",
    }
)

# Hermes releases explicitly verified as the HCA stable contract lane.
KNOWN_STABLE_VERSIONS: frozenset[str] = frozenset({"0.18.2", "2026.7.7.2"})

LANE_STABLE = "stable"
LANE_EDGE = "edge"
LANE_UNSUPPORTED = "unsupported"


# ---------------------------------------------------------------------------
# Backwards-compatible thin helpers
# ---------------------------------------------------------------------------


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
        return _import_module_with_clean_streams("hermes_cli.kanban_db")
    except ImportError as exc:
        # Try adding common install path
        candidate = os.path.expanduser("~/.hermes/hermes-agent")
        if candidate not in sys.path and os.path.isdir(candidate):
            sys.path.insert(0, candidate)
            try:
                return _import_module_with_clean_streams("hermes_cli.kanban_db")
            except ImportError:
                pass
        raise HermesCompatError(
            f"cannot import hermes_cli.kanban_db ({exc}). "
            "Install Hermes Agent and ensure its package is importable."
        ) from exc


def import_profiles():
    try:
        return _import_module_with_clean_streams("hermes_cli.profiles")
    except ImportError as exc:
        raise HermesCompatError(
            f"cannot import hermes_cli.profiles ({exc}). Install Hermes Agent."
        ) from exc


def inspect_dispatch_once() -> HermesInfo:
    version = hermes_version()
    kb = import_kanban_db()
    fn = getattr(kb, "dispatch_once", None)
    if fn is None:
        return HermesInfo(version, getattr(kb, "__file__", ""), False, "")
    sig = str(inspect.signature(fn))
    return HermesInfo(version, getattr(kb, "__file__", ""), True, sig)


# ---------------------------------------------------------------------------
# Version parsing + provenance
# ---------------------------------------------------------------------------


@dataclass
class HermesProvenance:
    version_line: str = ""
    semver: str = ""
    calver: str = ""
    upstream_commit: str = ""
    install_dir: str = ""
    install_method: str = ""
    kanban_db_module: str = ""
    python_version: str = ""
    platform: str = ""
    tmux_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_version_line(line: str) -> dict[str, str]:
    """Parse ``Hermes Agent v0.18.2 (2026.7.7.2) · upstream f8ddf4fd``.

    Returns semver / calver / upstream_commit best-effort; missing pieces
    come back as empty strings rather than raising.
    """
    import re

    out = {"semver": "", "calver": "", "upstream_commit": ""}
    m = re.search(r"v(\d+\.\d+\.\d+(?:[.\-]\w+)?)", line)
    if m:
        out["semver"] = m.group(1)
    m = re.search(r"\((\d{4}\.\d+\.\d+(?:\.\d+)?)\)", line)
    if m:
        out["calver"] = m.group(1)
    m = re.search(r"upstream\s+([0-9a-f]{7,40})", line, re.IGNORECASE)
    if m:
        out["upstream_commit"] = m.group(1)
    return out


def _tmux_version() -> str:
    tmux = shutil.which("tmux")
    if not tmux:
        return ""
    try:
        proc = subprocess.run([tmux, "-V"], capture_output=True, text=True, timeout=10)
        return proc.stdout.strip() or proc.stderr.strip()
    except Exception:
        return ""


def provenance() -> HermesProvenance:
    """Full HCA/Hermes provenance for the compatibility report and audit."""
    line = ""
    install_dir = ""
    install_method = ""
    try:
        line = hermes_version()
    except HermesCompatError:
        line = ""
    # `hermes --version` also prints install dir / method on extra lines;
    # capture them defensively.
    try:
        proc = subprocess.run(
            [hermes_bin(), "--version"], capture_output=True, text=True, timeout=30
        )
        for raw in (proc.stdout or "").splitlines():
            low = raw.lower()
            if "install directory" in low:
                install_dir = raw.split(":", 1)[-1].strip()
            elif "install method" in low:
                install_method = raw.split(":", 1)[-1].strip()
    except Exception:
        pass
    parsed = parse_version_line(line)
    kb_path = ""
    try:
        kb_path = getattr(import_kanban_db(), "__file__", "") or ""
    except HermesCompatError:
        pass
    return HermesProvenance(
        version_line=line,
        semver=parsed["semver"],
        calver=parsed["calver"],
        upstream_commit=parsed["upstream_commit"],
        install_dir=install_dir,
        install_method=install_method,
        kanban_db_module=kb_path,
        python_version=platform.python_version(),
        platform=platform.platform(),
        tmux_version=_tmux_version(),
    )


# ---------------------------------------------------------------------------
# Capability probing
# ---------------------------------------------------------------------------


@dataclass
class Capabilities:
    has_dispatch_once: bool = False
    dispatch_params: frozenset[str] = field(default_factory=frozenset)
    dispatch_result_fields: frozenset[str] = field(default_factory=frozenset)
    task_fields: frozenset[str] = field(default_factory=frozenset)
    kb_helpers: frozenset[str] = field(default_factory=frozenset)
    profile_helpers: frozenset[str] = field(default_factory=frozenset)
    dispatch_signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, frozenset):
                d[k] = sorted(v)
        return d

    # --- derived requirement checks ---

    def missing_dispatch_params(self) -> set[str]:
        return set(REQUIRED_DISPATCH_PARAMS) - set(self.dispatch_params)

    def missing_dispatch_result_fields(self) -> set[str]:
        return set(REQUIRED_DISPATCH_RESULT_FIELDS) - set(self.dispatch_result_fields)

    def missing_task_fields(self) -> set[str]:
        return set(REQUIRED_TASK_FIELDS) - set(self.task_fields)

    def missing_kb_helpers(self) -> set[str]:
        return set(REQUIRED_KB_HELPERS) - set(self.kb_helpers)

    def missing_profile_helpers(self) -> set[str]:
        return set(REQUIRED_PROFILE_HELPERS) - set(self.profile_helpers)

    def missing(self) -> dict[str, list[str]]:
        """All missing required capabilities, grouped, empty when supported."""
        groups = {
            "dispatch_params": self.missing_dispatch_params(),
            "dispatch_result_fields": self.missing_dispatch_result_fields(),
            "task_fields": self.missing_task_fields(),
            "kb_helpers": self.missing_kb_helpers(),
            "profile_helpers": self.missing_profile_helpers(),
        }
        if not self.has_dispatch_once:
            groups["dispatch_once"] = {"dispatch_once"}
        return {k: sorted(v) for k, v in groups.items() if v}

    def supported(self) -> bool:
        return not self.missing()


def _dataclass_field_names(obj: Any) -> frozenset[str]:
    import dataclasses as _dc

    if obj is None:
        return frozenset()
    try:
        if _dc.is_dataclass(obj):
            return frozenset(f.name for f in _dc.fields(obj))
    except Exception:
        pass
    # Fall back to annotations for non-dataclass typed objects.
    return frozenset(getattr(obj, "__annotations__", {}).keys())


def probe_capabilities(kb: Any = None, profiles: Any = None) -> Capabilities:
    """Introspect installed (or injected) Hermes modules for HCA's contract.

    ``kb`` / ``profiles`` are injectable for deterministic unit tests; when
    omitted they resolve to the installed ``hermes_cli`` modules.
    """
    if kb is None:
        kb = import_kanban_db()
    if profiles is None:
        try:
            profiles = import_profiles()
        except HermesCompatError:
            profiles = None

    fn = getattr(kb, "dispatch_once", None)
    has_dispatch = fn is not None
    params: frozenset[str] = frozenset()
    sig = ""
    if has_dispatch:
        try:
            signature = inspect.signature(fn)
            params = frozenset(signature.parameters.keys())
            sig = str(signature)
        except (TypeError, ValueError):
            params = frozenset()

    result_fields = _dataclass_field_names(getattr(kb, "DispatchResult", None))
    task_fields = _dataclass_field_names(getattr(kb, "Task", None))

    kb_helpers = frozenset(
        name for name in REQUIRED_KB_HELPERS if callable(getattr(kb, name, None))
    )
    profile_helpers = frozenset(
        name
        for name in REQUIRED_PROFILE_HELPERS
        if profiles is not None and callable(getattr(profiles, name, None))
    )

    return Capabilities(
        has_dispatch_once=has_dispatch,
        dispatch_params=params,
        dispatch_result_fields=result_fields,
        task_fields=task_fields,
        kb_helpers=kb_helpers,
        profile_helpers=profile_helpers,
        dispatch_signature=sig,
    )


def classify_lane(version_line: str, caps: Capabilities) -> tuple[str, str]:
    """Return ``(lane, reason)``.

    - ``unsupported``: a required capability is missing (fail closed).
    - ``stable``: capabilities complete AND version is an explicitly
      verified release.
    - ``edge``: capabilities complete but the version is not a known
      release — advisory, may drift.
    """
    missing = caps.missing()
    if missing:
        flat = "; ".join(f"{k}: {', '.join(v)}" for k, v in missing.items())
        return LANE_UNSUPPORTED, f"missing required capabilities — {flat}"
    parsed = parse_version_line(version_line)
    if parsed["semver"] in KNOWN_STABLE_VERSIONS or parsed["calver"] in KNOWN_STABLE_VERSIONS:
        return LANE_STABLE, f"verified release {parsed['semver'] or parsed['calver']}"
    return (
        LANE_EDGE,
        f"capabilities complete but version {parsed['semver'] or version_line!r} "
        "is not an explicitly verified release (advisory lane)",
    )


# ---------------------------------------------------------------------------
# Contract assertions (fail closed)
# ---------------------------------------------------------------------------

REQUIRED_SPAWN_FN_HINTS = ("spawn_fn",)


def assert_dispatch_contract(kb: Any = None, profiles: Any = None) -> HermesInfo:
    """Fail closed unless the full HCA dispatch contract is satisfied.

    Kept name-compatible with the previous thin check (callers in doctor /
    kanban rely on the returned HermesInfo) but now enforces the complete
    capability surface, not merely the presence of ``spawn_fn``.
    """
    caps = probe_capabilities(kb=kb, profiles=profiles)
    if not caps.has_dispatch_once:
        raise HermesCompatError(
            "hermes_cli.kanban_db.dispatch_once missing — HCA requires the "
            "kanban dispatcher with spawn_fn support"
        )
    missing = caps.missing()
    if missing:
        flat = "; ".join(f"{k}: {', '.join(v)}" for k, v in missing.items())
        raise HermesCompatError(
            "installed Hermes does not satisfy the HCA contract — "
            f"{flat}. This Hermes is unsupported; upgrade/downgrade to a "
            "verified release or narrow the HCA operation."
        )
    version = ""
    try:
        version = hermes_version()
    except HermesCompatError:
        version = ""
    kb_mod = kb if kb is not None else import_kanban_db()
    return HermesInfo(
        version=version,
        path=getattr(kb_mod, "__file__", "") or "",
        has_dispatch_once=True,
        dispatch_signature=caps.dispatch_signature,
    )


def require_supported(caps: Optional[Capabilities] = None) -> Capabilities:
    """Raise HermesCompatError with a precise message unless supported."""
    caps = caps or probe_capabilities()
    missing = caps.missing()
    if missing:
        flat = "; ".join(f"{k}: {', '.join(v)}" for k, v in missing.items())
        raise HermesCompatError(f"unsupported Hermes — missing {flat}")
    return caps


# ---------------------------------------------------------------------------
# Dispatcher ownership (sole-dispatcher fail-closed detection)
# ---------------------------------------------------------------------------


@dataclass
class DispatcherOwnership:
    board: str = ""
    gateway_running: bool = False
    gateway_pid: Optional[int] = None
    dispatch_in_gateway: bool = True
    conflict: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _default_gateway_pid() -> Optional[int]:
    """Best-effort live gateway pid without touching the gateway."""
    try:
        from gateway.status import get_running_pid  # type: ignore

        return get_running_pid()
    except Exception:
        return None


def _default_dispatch_flag() -> bool:
    """Read kanban.dispatch_in_gateway from the active Hermes config."""
    try:
        from hermes_cli.config import load_config  # type: ignore

        cfg = load_config()
        return bool(cfg.get("kanban", {}).get("dispatch_in_gateway", True))
    except Exception:
        # Fail closed: if we cannot prove the gateway will NOT dispatch,
        # assume the default (it does).
        return True


def dispatcher_ownership(
    board: str,
    *,
    gateway_pid_fn: Optional[Callable[[], Optional[int]]] = None,
    dispatch_flag_fn: Optional[Callable[[], bool]] = None,
) -> DispatcherOwnership:
    """Detect whether a foreign Hermes gateway could dispatch ``board``.

    HCA is the sole dispatcher for an HCA-owned board. If a gateway is
    live *and* its config leaves ``kanban.dispatch_in_gateway`` on, the two
    dispatchers race to claim the same tasks. The injectable callables keep
    this testable without querying — let alone disturbing — a real gateway.
    """
    pid_fn = gateway_pid_fn or _default_gateway_pid
    flag_fn = dispatch_flag_fn or _default_dispatch_flag
    pid = None
    try:
        pid = pid_fn()
    except Exception:
        pid = None
    running = bool(pid)
    dispatch_on = True
    try:
        dispatch_on = bool(flag_fn())
    except Exception:
        dispatch_on = True
    conflict = running and dispatch_on
    if conflict:
        reason = (
            f"a Hermes gateway (pid={pid}) is live with "
            "kanban.dispatch_in_gateway=true; it can claim board "
            f"{board!r} before the HCA supervisor. Set dispatch_in_gateway: "
            "false in the participating profile(s) and restart the gateway, "
            "or stop the gateway, so HCA is the sole dispatcher."
        )
    elif running and not dispatch_on:
        reason = (
            f"gateway pid={pid} is live but kanban.dispatch_in_gateway=false — "
            "HCA is the sole dispatcher for this board"
        )
    else:
        reason = "no live gateway dispatcher — HCA is the sole dispatcher"
    return DispatcherOwnership(
        board=board,
        gateway_running=running,
        gateway_pid=pid,
        dispatch_in_gateway=dispatch_on,
        conflict=conflict,
        reason=reason,
    )


def assert_sole_dispatcher(
    board: str,
    *,
    gateway_pid_fn: Optional[Callable[[], Optional[int]]] = None,
    dispatch_flag_fn: Optional[Callable[[], bool]] = None,
) -> DispatcherOwnership:
    """Fail closed before any claim/spawn if another dispatcher can win."""
    own = dispatcher_ownership(
        board,
        gateway_pid_fn=gateway_pid_fn,
        dispatch_flag_fn=dispatch_flag_fn,
    )
    if own.conflict:
        raise HermesCompatError(own.reason)
    return own


# ---------------------------------------------------------------------------
# Normalized compatibility report (surfaced in `hca doctor --json`)
# ---------------------------------------------------------------------------


def compatibility_report(
    board: Optional[str] = None,
    *,
    gateway_pid_fn: Optional[Callable[[], Optional[int]]] = None,
    dispatch_flag_fn: Optional[Callable[[], bool]] = None,
) -> dict[str, Any]:
    """One normalized dict describing lane, provenance, caps, and ownership."""
    prov = provenance()
    try:
        caps = probe_capabilities()
    except HermesCompatError as exc:
        return {
            "lane": LANE_UNSUPPORTED,
            "lane_reason": str(exc),
            "provenance": prov.to_dict(),
            "capabilities": {},
            "missing": {"import": ["hermes_cli.kanban_db"]},
            "supported": False,
        }
    lane, reason = classify_lane(prov.version_line, caps)
    report: dict[str, Any] = {
        "lane": lane,
        "lane_reason": reason,
        "supported": caps.supported(),
        "provenance": prov.to_dict(),
        "capabilities": caps.to_dict(),
        "missing": caps.missing(),
        "subagent_hook_keys": {
            "subagent_start": sorted(SUBAGENT_START_KEYS),
            "subagent_stop": sorted(SUBAGENT_STOP_KEYS),
            "correlation_key": "child_session_id",
        },
    }
    if board is not None:
        report["dispatcher_ownership"] = dispatcher_ownership(
            board,
            gateway_pid_fn=gateway_pid_fn,
            dispatch_flag_fn=dispatch_flag_fn,
        ).to_dict()
    return report


# ---------------------------------------------------------------------------
# Subprocess helpers (unchanged behavior)
# ---------------------------------------------------------------------------


def run_hermes(
    *args: str, env: Optional[dict] = None, timeout: int = 120
) -> subprocess.CompletedProcess:
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


def dispatch_once_with_spawn(conn, spawn_fn: Callable, **kwargs):
    """Call Hermes dispatch_once; fail closed if API drifts."""
    assert_dispatch_contract()
    kb = import_kanban_db()
    return kb.dispatch_once(conn, spawn_fn=spawn_fn, **kwargs)


def worker_command(profile: str, task_id: str, *, yolo: bool = False) -> str:
    """Legacy one-shot worker shape (superseded by WorkerLaunchSpec).

    Retained for the existing contract test and any callers not yet moved
    onto the typed launch spec. New code should use
    :mod:`hca.worker_launch`.
    """
    import shlex

    parts = ["hermes", "-p", profile, "--cli", "--accept-hooks"]
    if yolo:
        parts.append("--yolo")
    parts += ["chat", "-q", f"work kanban task {task_id}"]
    return " ".join(shlex.quote(p) for p in parts)
