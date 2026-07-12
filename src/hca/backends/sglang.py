"""SGLang capacity adapter (normalized)."""

from __future__ import annotations

from urllib.parse import urlparse

from hca.backends import openai_compat as oai
from hca.models import CapacitySnapshot


def _root(endpoint: str) -> str:
    p = urlparse(endpoint)
    # endpoint often http://127.0.0.1:30000/v1
    host = p.netloc or "127.0.0.1:30000"
    scheme = p.scheme or "http"
    return f"{scheme}://{host}"


def fetch_capacity(endpoint: str, metrics_url: str = "", timeout: float = 5.0) -> CapacitySnapshot:
    snap = CapacitySnapshot(engine="sglang", healthy=True)
    models = oai.probe_models(endpoint, timeout=timeout)
    root = _root(endpoint)

    # /health is documented in the NVIDIA SGLang playbook
    health_ok = False
    try:
        oai.fetch_text(f"{root}/health", timeout=timeout)
        health_ok = True
    except Exception:
        pass

    if not models.ok and not health_ok:
        snap.healthy = False
        snap.detail = models.detail
        return snap

    # Prometheus metrics (present when the server runs with --enable-metrics)
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
        # token_usage is the KV-token pool utilization fraction
        snap.kv_cache_util = oai.first_metric(metrics, ["sglang:token_usage", "token_usage"])
        snap.active_sequences = (
            oai.first_metric(metrics, ["sglang:num_running_reqs", "num_running_reqs"]) or 0.0
        )
        snap.waiting = (
            oai.first_metric(metrics, ["sglang:num_queue_reqs", "num_queue_reqs"]) or 0.0
        )
        snap.prefix_hit_rate = oai.first_metric(
            metrics, ["sglang:cache_hit_rate", "cache_hit_rate"]
        )

    if not models.ok:
        snap.detail = f"sglang health ok; models soft-fail: {models.detail}"
    else:
        snap.detail = "sglang ok" + (" (metrics)" if metrics else " (no metrics)")
    return snap
