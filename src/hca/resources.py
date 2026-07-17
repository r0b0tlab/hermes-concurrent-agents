"""Resource admission / backpressure governor."""

from __future__ import annotations

from dataclasses import dataclass
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

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "credits": self.credits,
            "capacity": self.capacity.to_dict() if self.capacity else None,
            "device": self.device,
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


def fetch_capacity(cfg: FleetConfig) -> CapacitySnapshot:
    eng = cfg.backend.engine
    if eng == Engine.VLLM:
        return vllm_adapter.fetch_capacity(cfg.backend.endpoint, cfg.backend.metrics_url)
    if eng == Engine.SGLANG:
        return sglang_adapter.fetch_capacity(cfg.backend.endpoint, cfg.backend.metrics_url)
    # generic: models probe only
    pr = oai.probe_models(cfg.backend.endpoint, cfg.backend.model)
    return CapacitySnapshot(
        engine=eng.value,
        healthy=pr.ok,
        detail=pr.detail,
    )


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
    if dev is not None:
        blocked, reason = _mem_hysteresis_gate(
            state, dev.mem_pressure, cap_cfg.memory_high, cap_cfg.memory_low
        )
        if blocked:
            return AdmissionDecision(False, f"waiting: {reason}", credits, capacity, dev_dict)
        if dev.disk_pressure is not None and dev.disk_pressure >= cap_cfg.disk_high:
            return AdmissionDecision(
                False,
                f"waiting: disk pressure {dev.disk_pressure:.0%} >= high "
                f"{cap_cfg.disk_high:.0%}",
                credits, capacity, dev_dict,
            )

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

    return AdmissionDecision(True, "admitted", credits, capacity, dev_dict)
