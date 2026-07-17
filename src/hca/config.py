"""Fleet configuration loading (TOML presets + overrides)."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any, Optional

from hca.models import (
    BackendConfig,
    CapacityConfig,
    ClusterConfig,
    ClusterNode,
    Engine,
    FleetConfig,
    FleetRole,
    ObserveConfig,
    RetentionConfig,
    Transport,
)

# Data files ship inside the package so wheel installs work, not just -e.
PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PRESET_DIR = PACKAGE_DIR / "presets"


def _enum(cls, value: Any, default):
    if value is None or value == "":
        return default
    if isinstance(value, cls):
        return value
    return cls(str(value))


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def resolve_preset_path(name: str, preset_dir: Optional[Path] = None) -> Path:
    root = preset_dir or DEFAULT_PRESET_DIR
    candidates = [
        root / f"{name}.toml",
        root / name,
        Path(name),
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(f"preset not found: {name} (looked in {root})")


def fleet_from_dict(data: dict[str, Any]) -> FleetConfig:
    fleet = data.get("fleet", {})
    backend = data.get("backend", {})
    capacity = data.get("capacity", {})
    cluster = data.get("cluster", {})
    observe = data.get("observe", {})
    retention = data.get("retention", {})
    profiles = data.get("profiles", {})
    delegation = data.get("delegation", {})
    approvals = data.get("approvals", {})

    nodes = []
    for n in cluster.get("nodes", []) or []:
        if isinstance(n, str):
            nodes.append(ClusterNode(host=n))
        else:
            nodes.append(
                ClusterNode(
                    host=str(n.get("host", "")),
                    ssh_user=str(n.get("ssh_user", "") or ""),
                    ssh_port=int(n.get("ssh_port", 22) or 22),
                    labels=dict(n.get("labels") or {}),
                    fabric_ip=str(n.get("fabric_ip", "") or ""),
                    engine=_enum(Engine, n.get("engine"), Engine.VLLM),
                    endpoint=str(n.get("endpoint", "") or ""),
                )
            )

    state_dir = fleet.get("state_dir") or os.path.expanduser("~/.hca")
    return FleetConfig(
        name=str(fleet.get("name", "default")),
        board=str(fleet.get("board", "hca")),
        role=_enum(FleetRole, fleet.get("role"), FleetRole.SINGLE),
        tmux_socket=str(fleet.get("tmux_socket", f"hca-{fleet.get('name', 'default')}")),
        dispatch_interval_seconds=float(fleet.get("dispatch_interval_seconds", 5.0)),
        warm_slots=bool(fleet.get("warm_slots", True)),
        drain_policy=str(fleet.get("drain_policy", "graceful")),
        state_dir=str(state_dir),
        backend=BackendConfig(
            engine=_enum(Engine, backend.get("engine"), Engine.VLLM),
            endpoint=str(backend.get("endpoint", "http://127.0.0.1:8000/v1")),
            model=str(backend.get("model", "")),
            api_mode=str(backend.get("api_mode", "openai")),
            local_only=bool(backend.get("local_only", True)),
            metrics_url=str(backend.get("metrics_url", "") or ""),
            auxiliary_endpoint=str(backend.get("auxiliary_endpoint", "") or ""),
            api_key_env=str(backend.get("api_key_env", "") or ""),
        ),
        capacity=CapacityConfig(
            max_top_level_runs=int(capacity.get("max_top_level_runs", 3)),
            max_total_sequences=float(capacity.get("max_total_sequences", 4.0)),
            memory_high=float(capacity.get("memory_high", 0.90)),
            memory_low=float(capacity.get("memory_low", 0.75)),
            disk_high=float(capacity.get("disk_high", 0.90)),
            disk_low=float(capacity.get("disk_low", 0.80)),
            disk_min_free_gb=float(capacity.get("disk_min_free_gb", 20.0)),
            disk_resume_free_gb=float(capacity.get("disk_resume_free_gb", 25.0)),
            disk_strict_percent=bool(capacity.get("disk_strict_percent", False)),
            per_role_caps=dict(capacity.get("per_role_caps") or {}),
            reserve_retry_lane=int(capacity.get("reserve_retry_lane", 1)),
            launch_stagger_seconds=float(capacity.get("launch_stagger_seconds", 1.5)),
            max_wave_size=int(capacity.get("max_wave_size", 4)),
        ),
        cluster=ClusterConfig(
            transport=_enum(Transport, cluster.get("transport"), Transport.SSH),
            nodes=nodes,
            probe_interval_seconds=float(cluster.get("probe_interval_seconds", 15.0)),
            placement_policy=str(cluster.get("placement_policy", "colocate-infer")),
            require_same_username=bool(cluster.get("require_same_username", True)),
            ssh_batch_mode=bool(cluster.get("ssh_batch_mode", True)),
            ssh_control_master=bool(cluster.get("ssh_control_master", True)),
            connect_timeout_seconds=int(cluster.get("connect_timeout_seconds", 8)),
            command_timeout_seconds=int(cluster.get("command_timeout_seconds", 60)),
        ),
        observe=ObserveConfig(
            watch_interval_seconds=float(observe.get("watch_interval_seconds", 2.0)),
            peek_lines=int(observe.get("peek_lines", 40)),
            activity_retention_days=int(observe.get("activity_retention_days", 14)),
            transcript_source=str(observe.get("transcript_source", "hermes-session")),
            redact_patterns=list(
                observe.get("redact_patterns")
                or ObserveConfig().redact_patterns
            ),
        ),
        retention=RetentionConfig(
            max_log_bytes_per_run=int(retention.get("max_log_bytes_per_run", 20_000_000)),
            activity_days=int(retention.get("activity_days", 14)),
            completed_run_log_ttl_days=int(retention.get("completed_run_log_ttl_days", 7)),
            worktree_retain_until=str(retention.get("worktree_retain_until", "terminal+clean")),
        ),
        profile_slots=dict(profiles.get("slots") or FleetConfig().profile_slots),
        delegation_max_children=int(delegation.get("max_concurrent_children", 0)),
        approvals_yolo=bool(approvals.get("yolo", False)),
        preset=str(data.get("preset", "") or fleet.get("preset", "") or ""),
    )


def config_shape(cfg: FleetConfig) -> dict[str, Any]:
    """Serialize a FleetConfig back into the TOML-shaped dict fleet_from_dict reads."""
    return {
        "preset": cfg.preset,
        "fleet": {
            "name": cfg.name,
            "board": cfg.board,
            "role": cfg.role.value,
            "tmux_socket": cfg.tmux_socket,
            "dispatch_interval_seconds": cfg.dispatch_interval_seconds,
            "warm_slots": cfg.warm_slots,
            "drain_policy": cfg.drain_policy,
            "state_dir": cfg.state_dir,
        },
        "backend": {
            "engine": cfg.backend.engine.value,
            "endpoint": cfg.backend.endpoint,
            "model": cfg.backend.model,
            "api_mode": cfg.backend.api_mode,
            "local_only": cfg.backend.local_only,
            "metrics_url": cfg.backend.metrics_url,
            "auxiliary_endpoint": cfg.backend.auxiliary_endpoint,
            "api_key_env": cfg.backend.api_key_env,
        },
        "capacity": {
            "max_top_level_runs": cfg.capacity.max_top_level_runs,
            "max_total_sequences": cfg.capacity.max_total_sequences,
            "memory_high": cfg.capacity.memory_high,
            "memory_low": cfg.capacity.memory_low,
            "disk_high": cfg.capacity.disk_high,
            "disk_low": cfg.capacity.disk_low,
            "disk_min_free_gb": cfg.capacity.disk_min_free_gb,
            "disk_resume_free_gb": cfg.capacity.disk_resume_free_gb,
            "disk_strict_percent": cfg.capacity.disk_strict_percent,
            "per_role_caps": cfg.capacity.per_role_caps,
            "reserve_retry_lane": cfg.capacity.reserve_retry_lane,
            "launch_stagger_seconds": cfg.capacity.launch_stagger_seconds,
            "max_wave_size": cfg.capacity.max_wave_size,
        },
        "cluster": {
            "transport": cfg.cluster.transport.value,
            "nodes": [
                {
                    "host": n.host,
                    "ssh_user": n.ssh_user,
                    "ssh_port": n.ssh_port,
                    "labels": n.labels,
                    "fabric_ip": n.fabric_ip,
                    "engine": n.engine.value,
                    "endpoint": n.endpoint,
                }
                for n in cfg.cluster.nodes
            ],
            "probe_interval_seconds": cfg.cluster.probe_interval_seconds,
            "placement_policy": cfg.cluster.placement_policy,
            "require_same_username": cfg.cluster.require_same_username,
            "ssh_batch_mode": cfg.cluster.ssh_batch_mode,
            "ssh_control_master": cfg.cluster.ssh_control_master,
            "connect_timeout_seconds": cfg.cluster.connect_timeout_seconds,
            "command_timeout_seconds": cfg.cluster.command_timeout_seconds,
        },
        "observe": {
            "watch_interval_seconds": cfg.observe.watch_interval_seconds,
            "peek_lines": cfg.observe.peek_lines,
            "activity_retention_days": cfg.observe.activity_retention_days,
            "transcript_source": cfg.observe.transcript_source,
            "redact_patterns": cfg.observe.redact_patterns,
        },
        "retention": {
            "max_log_bytes_per_run": cfg.retention.max_log_bytes_per_run,
            "activity_days": cfg.retention.activity_days,
            "completed_run_log_ttl_days": cfg.retention.completed_run_log_ttl_days,
            "worktree_retain_until": cfg.retention.worktree_retain_until,
        },
        "profiles": {"slots": cfg.profile_slots},
        "delegation": {"max_concurrent_children": cfg.delegation_max_children},
        "approvals": {"yolo": cfg.approvals_yolo},
    }


def persisted_config_shape(cfg: FleetConfig) -> dict[str, Any]:
    """Scheduling-only restart snapshot; never persist connection strings.

    Package preset endpoints are reconstructed from the installed preset. A
    custom endpoint is represented only by a runtime requirement and must be
    supplied to the next process through a config, flag, or environment.
    """
    shaped = config_shape(cfg)
    preset_backend: dict[str, Any] = {}
    if cfg.preset:
        try:
            preset_backend = dict(
                load_toml(resolve_preset_path(cfg.preset)).get("backend", {})
            )
        except (FileNotFoundError, OSError):
            preset_backend = {}
    default_backend = config_shape(fleet_from_dict({}))["backend"]
    expected = {**default_backend, **preset_backend}
    shaped["runtime"] = {
        "endpoint_required": cfg.backend.endpoint
        != str(expected.get("endpoint", "")),
        "metrics_url_required": cfg.backend.metrics_url
        != str(expected.get("metrics_url", "") or ""),
        "auxiliary_endpoint_required": cfg.backend.auxiliary_endpoint
        != str(expected.get("auxiliary_endpoint", "") or ""),
    }
    for key in ("endpoint", "metrics_url", "auxiliary_endpoint"):
        shaped["backend"].pop(key, None)
    shaped.pop("cluster", None)
    shaped["approvals"] = {"yolo": False}
    return shaped


def write_resolved_snapshot(cfg: FleetConfig) -> Path:
    """Atomically persist an owner-only, connection-free restart snapshot."""
    path = resolved_snapshot_path(cfg.state_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(persisted_config_shape(cfg), indent=2).encode("utf-8")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        path.chmod(0o600)
    finally:
        if temp.exists():
            temp.unlink()
    return path


def resolved_snapshot_path(state_dir: str = "") -> Path:
    return Path(os.path.expanduser(state_dir or "~/.hca")) / "fleet.resolved.json"


def load_fleet_config(
    *,
    preset: str = "",
    config_path: str = "",
    endpoint: str = "",
    model: str = "",
    engine: str = "",
    board: str = "",
    role: str = "",
    state_dir: str = "",
) -> FleetConfig:
    data: dict[str, Any] = {}
    snapshot_data: dict[str, Any] = {}
    snapshot_path: Optional[Path] = None
    if preset:
        data = load_toml(resolve_preset_path(preset))
        data["preset"] = preset
    if config_path:
        path = Path(config_path).expanduser()
        override = load_toml(path)
        data = _deep_merge(data, override)
    if not data:
        # No explicit preset/config: reuse only the connection-free scheduling
        # snapshot. Package-preset endpoints are reconstructed; custom values
        # must be supplied at runtime and are never stored in HCA state.
        snap = resolved_snapshot_path(state_dir)
        if snap.is_file():
            snapshot_path = snap
            snapshot_data = json.loads(snap.read_text(encoding="utf-8"))
            preset_name = str(snapshot_data.get("preset", "") or "")
            if preset_name:
                try:
                    data = load_toml(resolve_preset_path(preset_name))
                except FileNotFoundError:
                    data = {}
            data = _deep_merge(data, snapshot_data)
            runtime = snapshot_data.get("runtime", {})
            backend_data = data.setdefault("backend", {})
            if runtime.get("endpoint_required"):
                backend_data["endpoint"] = os.environ.get("HCA_BACKEND_ENDPOINT", "")
            if runtime.get("metrics_url_required"):
                backend_data["metrics_url"] = os.environ.get(
                    "HCA_BACKEND_METRICS_URL", ""
                )
            if runtime.get("auxiliary_endpoint_required"):
                backend_data["auxiliary_endpoint"] = os.environ.get(
                    "HCA_AUXILIARY_ENDPOINT", ""
                )
    cfg = fleet_from_dict(data)
    if endpoint:
        cfg.backend.endpoint = endpoint
    if model:
        cfg.backend.model = model
    if engine:
        cfg.backend.engine = Engine(engine)
        # Sensible port defaults if user only flips engine
        if engine == "sglang" and "8000" in cfg.backend.endpoint and not endpoint:
            cfg.backend.endpoint = "http://127.0.0.1:30000/v1"
        if engine == "vllm" and "30000" in cfg.backend.endpoint and not endpoint:
            cfg.backend.endpoint = "http://127.0.0.1:8000/v1"
    if board:
        cfg.board = board
    if role:
        cfg.role = FleetRole(role)
    if state_dir:
        cfg.state_dir = os.path.expanduser(state_dir)
    elif not cfg.state_dir:
        cfg.state_dir = os.path.expanduser(f"~/.hca/{cfg.name}")
    # Migrate legacy snapshots that serialized endpoint/cluster connection
    # details. They may configure this one invocation, then are replaced with
    # the scheduling-only form so sensitive values do not remain at rest.
    if snapshot_path is not None and (
        any(
            key in (snapshot_data.get("backend") or {})
            for key in ("endpoint", "metrics_url", "auxiliary_endpoint")
        )
        or "cluster" in snapshot_data
    ):
        write_resolved_snapshot(cfg)
    return cfg


def default_state_dir(fleet_name: str = "default") -> str:
    return os.path.expanduser(f"~/.hca/{fleet_name}")


def list_presets(preset_dir: Optional[Path] = None) -> list[str]:
    root = preset_dir or DEFAULT_PRESET_DIR
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.toml"))
