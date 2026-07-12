"""Shared dataclasses and enums for the HCA control plane."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class FleetRole(str, Enum):
    SINGLE = "single"
    CONTROL = "control"
    NODE = "node"


class Engine(str, Enum):
    VLLM = "vllm"
    SGLANG = "sglang"
    OPENAI_COMPAT = "openai_compat"


class Transport(str, Enum):
    SSH = "ssh"
    HTTP = "http"
    LOCAL = "local"


@dataclass
class BackendConfig:
    engine: Engine = Engine.VLLM
    endpoint: str = "http://127.0.0.1:8000/v1"
    model: str = ""
    api_mode: str = "openai"
    local_only: bool = True
    metrics_url: str = ""
    auxiliary_endpoint: str = ""

    def base_url(self) -> str:
        return self.endpoint.rstrip("/")


@dataclass
class CapacityConfig:
    max_top_level_runs: int = 3
    max_total_sequences: float = 4.0
    memory_high: float = 0.90
    memory_low: float = 0.75
    disk_high: float = 0.90
    disk_low: float = 0.80
    per_role_caps: dict[str, int] = field(default_factory=dict)
    reserve_retry_lane: int = 1
    launch_stagger_seconds: float = 1.5
    max_wave_size: int = 4


@dataclass
class ClusterNode:
    host: str
    ssh_user: str = ""
    ssh_port: int = 22
    labels: dict[str, str] = field(default_factory=dict)
    fabric_ip: str = ""
    engine: Engine = Engine.VLLM
    endpoint: str = ""


@dataclass
class ClusterConfig:
    transport: Transport = Transport.SSH
    nodes: list[ClusterNode] = field(default_factory=list)
    probe_interval_seconds: float = 15.0
    placement_policy: str = "colocate-infer"
    require_same_username: bool = True
    ssh_batch_mode: bool = True
    ssh_control_master: bool = True
    connect_timeout_seconds: int = 8
    command_timeout_seconds: int = 60


@dataclass
class ObserveConfig:
    watch_interval_seconds: float = 2.0
    peek_lines: int = 40
    activity_retention_days: int = 14
    transcript_source: str = "hermes-session"
    redact_patterns: list[str] = field(
        default_factory=lambda: [
            r"(?i)api[_-]?key\s*[:=]\s*\S+",
            r"(?i)authorization:\s*bearer\s+\S+",
            r"(?i)password\s*[:=]\s*\S+",
        ]
    )


@dataclass
class RetentionConfig:
    max_log_bytes_per_run: int = 20_000_000
    activity_days: int = 14
    completed_run_log_ttl_days: int = 7
    worktree_retain_until: str = "terminal+clean"


@dataclass
class FleetConfig:
    name: str = "default"
    board: str = "hca"
    role: FleetRole = FleetRole.SINGLE
    tmux_socket: str = "hca-default"
    dispatch_interval_seconds: float = 5.0
    warm_slots: bool = True
    drain_policy: str = "graceful"
    state_dir: str = ""
    backend: BackendConfig = field(default_factory=BackendConfig)
    capacity: CapacityConfig = field(default_factory=CapacityConfig)
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    observe: ObserveConfig = field(default_factory=ObserveConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    profile_slots: dict[str, int] = field(
        default_factory=lambda: {
            "orchestrator": 1,
            "coder": 2,
            "research": 2,
            "qa": 1,
            "creative": 1,
        }
    )
    delegation_max_children: int = 2
    approvals_yolo: bool = False
    preset: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CapacitySnapshot:
    active_sequences: float = 0.0
    waiting: float = 0.0
    kv_cache_util: Optional[float] = None
    prefix_hit_rate: Optional[float] = None
    mem_pressure: Optional[float] = None
    error_rate: Optional[float] = None
    ttft_p95: Optional[float] = None
    engine: str = ""
    healthy: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NodeStatus:
    host: str
    reachable: bool
    username: str = ""
    engine: str = ""
    endpoint: str = ""
    slots_free: int = 0
    slots_total: int = 0
    capacity: Optional[CapacitySnapshot] = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class ActivityEvent:
    ts: float
    kind: str
    board: str = ""
    task_id: str = ""
    run_id: str = ""
    slot: str = ""
    node: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
