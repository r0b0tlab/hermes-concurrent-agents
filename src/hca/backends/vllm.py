"""vLLM capacity adapter (normalized)."""

from __future__ import annotations

import json
import urllib.request
from typing import Optional
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


def _parse_prometheus(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # simple metric value lines
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0].split("{", 1)[0]
        try:
            out[name] = float(parts[-1])
        except ValueError:
            continue
    return out


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
            req = urllib.request.Request(url, headers={"User-Agent": "hca/2.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode(errors="replace")
            if text.lstrip().startswith("{"):
                data = json.loads(text)
                # JSON metrics if ever exposed
                for k, v in data.items():
                    if isinstance(v, (int, float)):
                        metrics[str(k)] = float(v)
            else:
                metrics = _parse_prometheus(text)
            last_err = ""
            break
        except Exception as exc:
            last_err = str(exc)
            continue

    if not metrics and last_err:
        snap.detail = f"vllm healthy but metrics unavailable ({last_err})"
        return snap

    # Best-effort mapping across vLLM versions
    snap.kv_cache_util = (
        metrics.get("vllm:gpu_cache_usage_perc")
        or metrics.get("vllm:kv_cache_usage_perc")
        or metrics.get("gpu_cache_usage_perc")
    )
    snap.active_sequences = (
        metrics.get("vllm:num_requests_running")
        or metrics.get("vllm:num_running_sys")
        or metrics.get("num_requests_running")
        or 0.0
    )
    snap.waiting = (
        metrics.get("vllm:num_requests_waiting")
        or metrics.get("num_requests_waiting")
        or 0.0
    )
    snap.prefix_hit_rate = metrics.get("vllm:prefix_cache_hits_total")
    snap.detail = "vllm metrics ok" if metrics else "vllm no metrics"
    return snap
