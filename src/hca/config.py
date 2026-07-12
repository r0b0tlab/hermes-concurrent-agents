"""Fleet configuration loading (TOML presets + overrides)."""

from __future__ import annotations

import os
import sys
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

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRESET_DIR = PACKAGE_ROOT / "config" / "presets"


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
        ),
        capacity=CapacityConfig(
            max_top_level_runs=int(capacity.get("max_top_level_runs", 3)),
            max_total_sequences=float(capacity.get("max_total_sequences", 4.0)),
            memory_high=float(capacity.get("memory_high", 0.90)),
            memory_low=float(capacity.get("memory_low", 0.75)),
            disk_high=float(capacity.get("disk_high", 0.90)),
            disk_low=float(capacity.get("disk_low", 0.80)),
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
        delegation_max_children=int(delegation.get("max_concurrent_children", 2)),
        approvals_yolo=bool(approvals.get("yolo", False)),
        preset=str(data.get("preset", "") or fleet.get("preset", "") or ""),
    )


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
    if preset:
        data = load_toml(resolve_preset_path(preset))
        data["preset"] = preset
    if config_path:
        path = Path(config_path).expanduser()
        override = load_toml(path)
        data = _deep_merge(data, override)
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
    return cfg


def default_state_dir(fleet_name: str = "default") -> str:
    return os.path.expanduser(f"~/.hca/{fleet_name}")


def list_presets(preset_dir: Optional[Path] = None) -> list[str]:
    root = preset_dir or DEFAULT_PRESET_DIR
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.toml"))
