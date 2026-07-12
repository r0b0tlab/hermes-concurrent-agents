"""SGLang capacity adapter (normalized)."""

from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlparse, urlunparse

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
    health_ok = False
    root = _root(endpoint)
    # /health is documented in NVIDIA SGLang playbook
    for url in [metrics_url, f"{root}/health", f"{root}/metrics"] if metrics_url else [f"{root}/health", f"{root}/metrics"]:
        if not url:
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hca/2.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode(errors="replace")
            health_ok = True
            if "cache" in body.lower() or body.lstrip().startswith("{"):
                try:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        snap.kv_cache_util = _first_float(
                            data, ["token_usage", "cache_hit_rate", "kv_cache_usage"]
                        )
                        snap.active_sequences = _first_float(
                            data, ["num_running", "running", "active"]
                        ) or 0.0
                        snap.waiting = _first_float(data, ["num_waiting", "waiting"]) or 0.0
                except json.JSONDecodeError:
                    pass
            break
        except Exception:
            continue

    if not models.ok and not health_ok:
        snap.healthy = False
        snap.detail = models.detail
        return snap

    if not models.ok:
        snap.detail = f"sglang health ok; models soft-fail: {models.detail}"
    else:
        snap.detail = "sglang ok"
    return snap


def _first_float(data: dict, keys: list[str]):
    for k in keys:
        if k in data and isinstance(data[k], (int, float)):
            return float(data[k])
    return None
