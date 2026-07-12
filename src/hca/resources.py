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

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "credits": self.credits,
            "capacity": self.capacity.to_dict() if self.capacity else None,
        }


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
    task_class: str = "batch",
) -> AdmissionDecision:
    cap_cfg: CapacityConfig = cfg.capacity
    capacity = fetch_capacity(cfg)
    if not capacity.healthy:
        return AdmissionDecision(False, f"waiting: backend unhealthy ({capacity.detail})", credits, capacity)

    running = running_top_level
    if running is None:
        running = len(state.list_runs(status="running"))
    if running >= cap_cfg.max_top_level_runs:
        return AdmissionDecision(
            False,
            f"waiting: top-level run cap {running}/{cap_cfg.max_top_level_runs}",
            credits,
            capacity,
        )

    leased = state.active_lease_credits()
    if leased + credits > cap_cfg.max_total_sequences:
        return AdmissionDecision(
            False,
            f"waiting: sequence credit budget {leased + credits:.2f}/{cap_cfg.max_total_sequences}",
            credits,
            capacity,
        )

    if capacity.kv_cache_util is not None and capacity.kv_cache_util >= cap_cfg.memory_high:
        return AdmissionDecision(
            False,
            f"waiting: backend KV cache pressure {capacity.kv_cache_util:.0%}",
            credits,
            capacity,
        )

    if capacity.waiting and capacity.waiting > max(2.0, cap_cfg.max_total_sequences):
        return AdmissionDecision(
            False,
            f"waiting: backend queue depth {capacity.waiting:.0f}",
            credits,
            capacity,
        )

    return AdmissionDecision(True, "admitted", credits, capacity)
