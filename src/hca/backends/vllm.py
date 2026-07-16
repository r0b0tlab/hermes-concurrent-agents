"""vLLM capacity adapter (normalized)."""

from __future__ import annotations

import json
from urllib.parse import urlparse, urlunparse

from hca.backends import openai_compat as oai
from hca.models import CapacitySnapshot


def _metrics_candidates(endpoint: str, metrics_url: str = "") -> list[str]:
    if metrics_url:
        return [metrics_url]
    p = urlparse(endpoint)
    # Common: API on :8000/v1, metrics on :8000/metrics
    root = urlunparse((p.scheme, p.netloc, "", "", "", ""))
    return [f"{root}/metrics", f"{root}/v1/metrics"]


def fetch_capacity(endpoint: str, metrics_url: str = "", timeout: float = 5.0) -> CapacitySnapshot:
    snap = CapacitySnapshot(engine="vllm", healthy=True)
    # health via models
    models = oai.probe_models(endpoint, timeout=timeout)
    if not models.ok:
        snap.healthy = False
        snap.detail = models.detail
        return snap

    metrics: dict[str, float] = {}
    last_err = ""
    for url in _metrics_candidates(endpoint, metrics_url):
        try:
            text = oai.fetch_text(url, timeout=timeout)
            if text.lstrip().startswith("{"):
                data = json.loads(text)
                # JSON metrics if ever exposed
                for k, v in data.items():
                    if isinstance(v, (int, float)):
                        metrics[str(k)] = float(v)
            else:
                metrics = oai.parse_prometheus(text)
            last_err = ""
            break
        except Exception as exc:
            last_err = oai.safe_error_detail(exc, url)
            continue

    if not metrics and last_err:
        snap.detail = f"vllm healthy but metrics unavailable ({last_err})"
        return snap

    # Best-effort mapping across vLLM versions; 0.0 readings are valid
    snap.kv_cache_util = oai.first_metric(
        metrics,
        ["vllm:gpu_cache_usage_perc", "vllm:kv_cache_usage_perc", "gpu_cache_usage_perc"],
    )
    snap.active_sequences = (
        oai.first_metric(
            metrics,
            ["vllm:num_requests_running", "vllm:num_running_sys", "num_requests_running"],
        )
        or 0.0
    )
    snap.waiting = (
        oai.first_metric(metrics, ["vllm:num_requests_waiting", "num_requests_waiting"]) or 0.0
    )
    hits = oai.first_metric(metrics, ["vllm:prefix_cache_hits_total"])
    queries = oai.first_metric(metrics, ["vllm:prefix_cache_queries_total"])
    if hits is not None and queries:
        snap.prefix_hit_rate = hits / queries
    snap.detail = "vllm metrics ok" if metrics else "vllm no metrics"
    return snap
