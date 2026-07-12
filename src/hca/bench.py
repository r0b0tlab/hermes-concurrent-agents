"""Concurrency benchmark harness for vLLM / SGLang (and generic OpenAI-compat)."""

from __future__ import annotations

import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from hca.backends import openai_compat as oai
from hca.models import Engine


@dataclass
class LevelResult:
    concurrency: int
    requests: int
    successes: int
    failures: int
    latency_p50: float
    latency_p95: float
    latency_mean: float
    throughput_rps: float
    error_rate: float
    notes: str = ""


@dataclass
class BenchReport:
    engine: str
    endpoint: str
    model: str
    levels: list[LevelResult] = field(default_factory=list)
    recommended_max_sequences: Optional[int] = None
    knee_reason: str = ""
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _chat_once(endpoint: str, model: str, timeout: float = 60.0) -> tuple[bool, float, str]:
    base = endpoint.rstrip("/")
    url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": "Reply with exactly: HCA_BENCH_OK",
            }
        ],
        "max_tokens": 16,
        "temperature": 0,
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "hca-bench/2"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
        dt = time.perf_counter() - t0
        payload = json.loads(raw) if raw else {}
        if "error" in payload:
            return False, dt, str(payload["error"])
        return True, dt, "ok"
    except Exception as exc:
        dt = time.perf_counter() - t0
        return False, dt, str(exc)


def _percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    k = int(round((p / 100.0) * (len(ys) - 1)))
    return ys[max(0, min(k, len(ys) - 1))]


def run_level(
    *,
    endpoint: str,
    model: str,
    concurrency: int,
    requests_per_worker: int = 3,
    timeout: float = 60.0,
) -> LevelResult:
    total = concurrency * requests_per_worker
    latencies: list[float] = []
    successes = 0
    failures = 0
    notes: list[str] = []

    def work(_i: int) -> tuple[bool, float, str]:
        return _chat_once(endpoint, model, timeout=timeout)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futs = [pool.submit(work, i) for i in range(total)]
        for fut in as_completed(futs):
            ok, dt, msg = fut.result()
            latencies.append(dt)
            if ok:
                successes += 1
            else:
                failures += 1
                if len(notes) < 3:
                    notes.append(msg[:120])
    wall = max(time.perf_counter() - t0, 1e-6)
    return LevelResult(
        concurrency=concurrency,
        requests=total,
        successes=successes,
        failures=failures,
        latency_p50=_percentile(latencies, 50),
        latency_p95=_percentile(latencies, 95),
        latency_mean=statistics.mean(latencies) if latencies else 0.0,
        throughput_rps=successes / wall,
        error_rate=(failures / total) if total else 1.0,
        notes="; ".join(notes),
    )


def detect_knee(levels: list[LevelResult]) -> tuple[Optional[int], str]:
    """Recommend max concurrency before error/latency collapse."""
    if not levels:
        return None, "no levels"
    best = levels[0]
    for prev, cur in zip(levels, levels[1:]):
        if cur.error_rate > 0.1:
            return prev.concurrency, f"error_rate>{cur.error_rate:.0%} at c={cur.concurrency}"
        if prev.latency_p95 > 0 and cur.latency_p95 > prev.latency_p95 * 2.5:
            return prev.concurrency, f"p95 latency jumped {prev.latency_p95:.2f}s → {cur.latency_p95:.2f}s"
        if cur.throughput_rps < prev.throughput_rps * 0.7 and cur.concurrency > prev.concurrency:
            return prev.concurrency, "throughput declined despite higher concurrency"
        best = cur
    return best.concurrency, "no collapse detected within tested range"


def run_bench(
    *,
    engine: str,
    endpoint: str,
    model: str,
    levels: list[int],
    requests_per_worker: int = 3,
    dry_run: bool = False,
    out_path: Optional[str] = None,
) -> BenchReport:
    report = BenchReport(
        engine=engine or "openai_compat",
        endpoint=endpoint,
        model=model,
        dry_run=dry_run,
    )
    if dry_run:
        for c in levels:
            report.levels.append(
                LevelResult(
                    concurrency=c,
                    requests=c * requests_per_worker,
                    successes=0,
                    failures=0,
                    latency_p50=0,
                    latency_p95=0,
                    latency_mean=0,
                    throughput_rps=0,
                    error_rate=0,
                    notes="dry-run",
                )
            )
        report.recommended_max_sequences = levels[0] if levels else None
        report.knee_reason = "dry-run (no traffic)"
        return report

    # Preflight
    pr = oai.probe_models(endpoint, model)
    if not pr.ok:
        report.knee_reason = f"preflight failed: {pr.detail}"
        return report

    for c in levels:
        report.levels.append(
            run_level(
                endpoint=endpoint,
                model=model,
                concurrency=c,
                requests_per_worker=requests_per_worker,
            )
        )
    rec, reason = detect_knee(report.levels)
    report.recommended_max_sequences = rec
    report.knee_reason = reason

    if out_path:
        path = Path(out_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report
