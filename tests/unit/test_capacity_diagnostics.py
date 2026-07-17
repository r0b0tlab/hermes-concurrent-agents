from __future__ import annotations

from hca.backends import openai_compat as oai
from hca.backends import vllm


STATIC_STALLED_METRICS = """
# HELP vllm:num_requests_running active requests
vllm:num_requests_running 2
vllm:num_requests_waiting 0
vllm:gpu_cache_usage_perc 0.42
vllm:generation_tokens_total 100
"""


def _models_ok(*_args, **_kwargs):
    return oai.ProbeResult(True, "/models ok", {"data": [{"id": "m"}]})


def test_two_vllm_samples_with_active_requests_and_zero_token_delta_warn(monkeypatch):
    samples = iter([100.0, 101.0])
    monkeypatch.setattr(vllm.time, "time", lambda: next(samples))
    monkeypatch.setattr(vllm.oai, "probe_models", _models_ok)
    monkeypatch.setattr(vllm.oai, "fetch_text", lambda *_args, **_kwargs: STATIC_STALLED_METRICS)

    first = vllm.fetch_capacity("http://127.0.0.1:8000/v1")
    second = vllm.fetch_capacity("http://127.0.0.1:8000/v1", previous=first)

    assert second.reachable is True
    assert second.active_sequences == 2
    assert second.generation_tokens_total == 100
    assert second.sample_window_seconds == 1.0
    assert second.probable_no_progress is True
    assert "advisory" in second.detail


def test_two_vllm_samples_with_token_delta_report_progress(monkeypatch):
    samples = iter([100.0, 101.0])
    metrics = iter(
        [
            STATIC_STALLED_METRICS,
            STATIC_STALLED_METRICS.replace(
                "vllm:generation_tokens_total 100",
                "vllm:generation_tokens_total 101",
            ),
        ]
    )
    monkeypatch.setattr(vllm.time, "time", lambda: next(samples))
    monkeypatch.setattr(vllm.oai, "probe_models", _models_ok)
    monkeypatch.setattr(vllm.oai, "fetch_text", lambda *_args, **_kwargs: next(metrics))

    first = vllm.fetch_capacity("http://127.0.0.1:8000/v1")
    second = vllm.fetch_capacity("http://127.0.0.1:8000/v1", previous=first)

    assert second.probable_no_progress is False


def test_missing_vllm_metrics_keeps_progress_unknown(monkeypatch):
    monkeypatch.setattr(vllm.oai, "probe_models", _models_ok)

    def unavailable(*_args, **_kwargs):
        raise OSError("metrics disabled")

    monkeypatch.setattr(vllm.oai, "fetch_text", unavailable)
    snapshot = vllm.fetch_capacity("http://127.0.0.1:8000/v1")

    assert snapshot.reachable is True
    assert snapshot.capacity_pressure is None
    assert snapshot.probable_no_progress is None
    assert "metrics unavailable" in snapshot.detail
