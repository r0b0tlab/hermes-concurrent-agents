"""SGLang capacity adapter (normalized)."""

from __future__ import annotations

import time
from urllib.parse import urlparse

from hca.backends import openai_compat as oai
from hca.models import CapacitySnapshot


def _root(endpoint: str) -> str:
    p = urlparse(endpoint)
    host = p.netloc or "127.0.0.1:30000"
    scheme = p.scheme or "http"
    return f"{scheme}://{host}"


def _apply_progress_delta(snap: CapacitySnapshot, previous: CapacitySnapshot | None) -> None:
    if (
        previous is None
        or previous.sampled_at is None
        or snap.sampled_at is None
        or previous.generation_tokens_total is None
        or snap.generation_tokens_total is None
        or snap.active_sequences <= 0
    ):
        return
    snap.sample_window_seconds = max(0.0, snap.sampled_at - previous.sampled_at)
    delta = snap.generation_tokens_total - previous.generation_tokens_total
    snap.probable_no_progress = True if delta == 0 else (False if delta > 0 else None)


def fetch_capacity(
    endpoint: str,
    metrics_url: str = "",
    timeout: float = 5.0,
    api_key: str = "",
    previous: CapacitySnapshot | None = None,
) -> CapacitySnapshot:
    snap = CapacitySnapshot(engine="sglang", healthy=True, sampled_at=time.time())
    models = oai.probe_models(endpoint, timeout=timeout, api_key=api_key)
    root = _root(endpoint)

    health_ok = False
    try:
        oai.fetch_text(f"{root}/health", timeout=timeout)
        health_ok = True
    except Exception:
        pass

    if not models.ok and not health_ok:
        snap.reachable = models.failure_kind != "reachability"
        snap.healthy = False
        snap.detail = models.detail
        return snap
    snap.reachable = True

    metrics: dict[str, float] = {}
    for url in [metrics_url, f"{root}/metrics"]:
        if not url:
            continue
        try:
            metrics = oai.parse_prometheus(oai.fetch_text(url, timeout=timeout))
            break
        except Exception:
            continue

    if metrics:
        snap.kv_cache_util = oai.first_metric(metrics, ["sglang:token_usage", "token_usage"])
        snap.active_sequences = (
            oai.first_metric(metrics, ["sglang:num_running_reqs", "num_running_reqs"])
            or 0.0
        )
        snap.waiting = (
            oai.first_metric(metrics, ["sglang:num_queue_reqs", "num_queue_reqs"])
            or 0.0
        )
        snap.prefix_hit_rate = oai.first_metric(
            metrics, ["sglang:cache_hit_rate", "cache_hit_rate"]
        )
        snap.generation_tokens_total = oai.first_metric(
            metrics,
            [
                "sglang:generation_tokens_total",
                "sglang:gen_tokens_total",
                "generation_tokens_total",
            ],
        )
        snap.capacity_pressure = bool(
            snap.waiting > 0
            or (snap.kv_cache_util is not None and snap.kv_cache_util >= 0.90)
        )

    _apply_progress_delta(snap, previous)
    if not models.ok:
        snap.detail = f"sglang health ok; models soft-fail: {models.detail}"
    else:
        snap.detail = "sglang ok" + (" (metrics)" if metrics else " (no metrics)")
    if snap.probable_no_progress:
        snap.detail += "; probable no-progress stall (advisory)"
    return snap
