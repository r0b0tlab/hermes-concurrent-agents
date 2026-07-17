"""Resource admission / backpressure governor."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional

from hca.backends import openai_compat as oai
from hca.backends import sglang as sglang_adapter
from hca.backends import vllm as vllm_adapter
from hca.models import CapacityConfig, CapacitySnapshot, Engine, FleetConfig
from hca.state import StateDB


@dataclass
class AdmissionDecision:
    allowed: bool
    reason: str
    credits: float = 1.0
    capacity: Optional[CapacitySnapshot] = None
    device: Optional[dict] = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "credits": self.credits,
            "capacity": self.capacity.to_dict() if self.capacity else None,
            "device": self.device,
            "warnings": list(self.warnings),
        }


def _mem_hysteresis_gate(
    state: StateDB, pressure: Optional[float], high: float, low: float
) -> tuple[bool, str]:
    """Return ``(blocked, reason)`` with hysteresis to avoid oscillation.

    Once host memory pressure crosses ``high`` the gate stays closed until it
    falls back below ``low`` (not merely below ``high``), so admission does
    not flap open/closed on every poll. If telemetry becomes unknown while the
    gate is closed, it remains closed until a low-watermark reading proves
    recovery.
    """
    key = "admission_mem_gate"
    gate_closed = state.get_meta(key, "0") == "1"
    if pressure is None:
        if gate_closed:
            return (
                True,
                "host memory pressure unknown while gate is closed "
                f"(requires low watermark <= {low:.0%})",
            )
        return False, ""
    if gate_closed:
        if pressure <= low:
            state.set_meta(key, "0")
            return False, ""
        return (
            True,
            f"host memory pressure {pressure:.0%} (low watermark {low:.0%} required)",
        )
    if pressure >= high:
        state.set_meta(key, "1")
        return True, f"host memory pressure {pressure:.0%} >= high {high:.0%}"
    return False, ""


def _disk_floor_gate(
    state: StateDB,
    free_mb: Optional[int],
    minimum_gb: float,
    resume_gb: float,
) -> tuple[bool, str]:
    """Absolute free-space floor with a separate reopen watermark."""
    key = "admission_disk_floor_gate"
    closed = state.get_meta(key, "0") == "1"
    if free_mb is None:
        if closed:
            return True, "disk free space unknown while absolute floor gate is closed"
        return False, ""
    free_gb = free_mb / 1024.0
    reopen_gb = max(minimum_gb, resume_gb)
    if closed:
        if free_gb >= reopen_gb:
            state.set_meta(key, "0")
            return False, ""
        return True, f"disk free {free_gb:.1f} GiB; resume requires {reopen_gb:.1f} GiB"
    if free_gb < minimum_gb:
        state.set_meta(key, "1")
        return True, f"disk free {free_gb:.1f} GiB < minimum reserve {minimum_gb:.1f} GiB"
    return False, ""


def estimate_task_credits(
    *,
    task_class: str = "batch",
    may_spawn_subagents: int = 0,
    long_context: bool = False,
) -> float:
    base = {
        "llm-heavy": 1.5,
        "tool-heavy": 0.8,
        "memory-heavy": 1.8,
        "latency-sensitive": 1.0,
        "batch": 1.0,
    }.get(task_class, 1.0)
    if long_context:
        base += 0.75
    base += 0.5 * max(0, may_spawn_subagents)
    return base


def fetch_capacity(
    cfg: FleetConfig, previous: Optional[CapacitySnapshot] = None
) -> CapacitySnapshot:
    eng = cfg.backend.engine
    api_key = os.environ.get(cfg.backend.api_key_env, "") if cfg.backend.api_key_env else ""
    if eng == Engine.VLLM:
        return vllm_adapter.fetch_capacity(
            cfg.backend.endpoint,
            cfg.backend.metrics_url,
            api_key=api_key,
            previous=previous,
        )
    if eng == Engine.SGLANG:
        return sglang_adapter.fetch_capacity(
            cfg.backend.endpoint,
            cfg.backend.metrics_url,
            api_key=api_key,
            previous=previous,
        )
    # generic: models probe only
    pr = oai.probe_models(cfg.backend.endpoint, cfg.backend.model, api_key=api_key)
    return CapacitySnapshot(
        engine=eng.value,
        healthy=pr.ok,
        detail=pr.detail,
        reachable=pr.failure_kind != "reachability",
    )


def diagnose_capacity_progress(
    cfg: FleetConfig, *, sample_interval_seconds: float = 0.25
) -> CapacitySnapshot:
    """Take a bounded second sample only when progress telemetry can answer."""
    first = fetch_capacity(cfg)
    if (
        first.reachable is not True
        or first.active_sequences <= 0
        or first.generation_tokens_total is None
    ):
        return first
    time.sleep(max(0.0, min(2.0, sample_interval_seconds)))
    return fetch_capacity(cfg, previous=first)


def admit(
    cfg: FleetConfig,
    state: StateDB,
    *,
    credits: float = 1.0,
    running_top_level: Optional[int] = None,
    enforce_top_level_cap: bool = True,
    task_class: str = "batch",
    device_signals=None,
    capacity: Optional[CapacitySnapshot] = None,
    probe_backend: bool = False,
    requested_disk_mb: int = 0,
) -> AdmissionDecision:
    cap_cfg: CapacityConfig = cfg.capacity
    # Hermes profiles own provider/model connectivity. Core HCA admission must
    # not depend on an HCA-managed endpoint probe. Optional diagnostics may pass
    # a snapshot (or explicitly request a probe); otherwise configured static
    # caps plus host/device pressure are the conservative authority.
    if capacity is None and probe_backend:
        capacity = fetch_capacity(cfg)

    # Device admission: host memory/swap/disk from the selected adapter. Runs
    # before backend checks so a memory-pressured host never launches a wave
    # even if the endpoint looks idle. Unknown accel telemetry stays
    # conservative and never reads as spare capacity.
    dev = device_signals
    if dev is None:
        try:
            from hca.devices import probe_device

            dev, _reason = probe_device(disk_path=cfg.state_dir or None)
        except Exception:
            dev = None
    dev_dict = dev.to_dict() if dev is not None else None
    warnings: list[str] = []
    if dev is not None:
        blocked, reason = _mem_hysteresis_gate(
            state, dev.mem_pressure, cap_cfg.memory_high, cap_cfg.memory_low
        )
        if blocked:
            return AdmissionDecision(False, f"waiting: {reason}", credits, capacity, dev_dict)
        blocked, reason = _disk_floor_gate(
            state,
            dev.disk_free_mb,
            cap_cfg.disk_min_free_gb,
            cap_cfg.disk_resume_free_gb,
        )
        if blocked:
            return AdmissionDecision(False, f"waiting: {reason}", credits, capacity, dev_dict)
        reserve_mb = int(max(0.0, cap_cfg.disk_min_free_gb) * 1024)
        if (
            requested_disk_mb > 0
            and dev.disk_free_mb is not None
            and requested_disk_mb > max(0, dev.disk_free_mb - reserve_mb)
        ):
            return AdmissionDecision(
                False,
                "waiting: run disk budget does not fit above absolute reserve "
                f"({requested_disk_mb} MiB requested, {dev.disk_free_mb} MiB free, "
                f"{reserve_mb} MiB reserved)",
                credits,
                capacity,
                dev_dict,
            )
        if dev.disk_pressure is not None and dev.disk_pressure >= cap_cfg.disk_high:
            detail = (
                f"disk pressure {dev.disk_pressure:.1%} >= high {cap_cfg.disk_high:.1%}; "
                f"free={((dev.disk_free_mb or 0) / 1024.0):.1f} GiB"
            )
            if cap_cfg.disk_strict_percent:
                return AdmissionDecision(
                    False,
                    f"waiting: {detail}",
                    credits,
                    capacity,
                    dev_dict,
                )
            warnings.append(detail)

    if capacity is not None and not capacity.healthy:
        return AdmissionDecision(
            False, f"waiting: backend unhealthy ({capacity.detail})", credits, capacity, dev_dict
        )

    if enforce_top_level_cap:
        running = running_top_level
        if running is None:
            running = len(state.list_runs(status="running"))
        if running >= cap_cfg.max_top_level_runs:
            return AdmissionDecision(
                False,
                f"waiting: top-level run cap {running}/{cap_cfg.max_top_level_runs}",
                credits,
                capacity,
                dev_dict,
            )

    leased = state.active_lease_credits()
    if leased + credits > cap_cfg.max_total_sequences:
        return AdmissionDecision(
            False,
            f"waiting: sequence credit budget {leased + credits:.2f}/{cap_cfg.max_total_sequences}",
            credits,
            capacity,
            dev_dict,
        )

    if (
        capacity is not None
        and capacity.kv_cache_util is not None
        and capacity.kv_cache_util >= cap_cfg.memory_high
    ):
        return AdmissionDecision(
            False,
            f"waiting: backend KV cache pressure {capacity.kv_cache_util:.0%}",
            credits,
            capacity,
            dev_dict,
        )

    if (
        capacity is not None
        and capacity.waiting
        and capacity.waiting > max(2.0, cap_cfg.max_total_sequences)
    ):
        return AdmissionDecision(
            False,
            f"waiting: backend queue depth {capacity.waiting:.0f}",
            credits,
            capacity,
            dev_dict,
        )

    reason = "admitted" if not warnings else "admitted with warning: " + "; ".join(warnings)
    return AdmissionDecision(True, reason, credits, capacity, dev_dict, warnings)
