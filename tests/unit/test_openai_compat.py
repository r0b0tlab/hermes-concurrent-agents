from __future__ import annotations

import urllib.error
from email.message import Message

from hca.backends import openai_compat as oai


def test_probe_chat_accepts_reasoning_only_openai_response(monkeypatch):
    monkeypatch.setattr(
        oai,
        "_http_json",
        lambda *_args, **_kwargs: {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning": "HCA_OK",
                    }
                }
            ]
        },
    )

    result = oai.probe_chat("http://127.0.0.1:8000/v1", "reasoning-model")

    assert result.ok
    assert "reasoning" in result.detail
    assert "HCA_OK" in result.detail


def test_probe_chat_rejects_success_shape_without_any_generated_text(monkeypatch):
    monkeypatch.setattr(
        oai,
        "_http_json",
        lambda *_args, **_kwargs: {"choices": [{"message": {"content": None}}]},
    )

    result = oai.probe_chat("http://127.0.0.1:8000/v1", "empty-model")

    assert not result.ok
    assert "no text or reasoning" in result.detail


def test_probe_diagnostic_redacts_connection_identifiers(monkeypatch):
    endpoint = "https://alice:sensitive@inference.example.invalid/v1"

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"could not connect to {endpoint}")

    monkeypatch.setattr(oai, "_http_json", fail)
    result = oai.probe_models(endpoint, "m")

    assert not result.ok
    assert "<endpoint>" in result.detail
    for forbidden in (endpoint, "alice", "sensitive", "inference.example.invalid"):
        assert forbidden not in result.detail


def test_endpoint_scope_uses_real_private_network_ranges():
    assert oai.endpoint_scope("http://172.16.1.2/v1") == "local"
    assert oai.endpoint_scope("http://172.200.1.2/v1") == "remote"


def test_probe_models_forwards_api_key_without_exposing_it(monkeypatch):
    seen = {}

    def models(_url, **kwargs):
        seen.update(kwargs)
        return {"data": [{"id": "m"}]}

    monkeypatch.setattr(oai, "_http_json", models)
    result = oai.probe_models("http://127.0.0.1:8000/v1", "m", api_key="top-secret")

    assert result.ok
    assert seen["api_key"] == "top-secret"
    assert "top-secret" not in result.detail


def test_probe_models_classifies_auth_failure_separately(monkeypatch):
    def unauthorized(url, **_kwargs):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", Message(), None)

    monkeypatch.setattr(oai, "_http_json", unauthorized)
    result = oai.probe_models(
        "https://inference.example.invalid/v1", "m", api_key="top-secret"
    )

    assert result.ok is False
    assert result.failure_kind == "authentication"
    assert result.detail == "models authentication failed (HTTP 401)"
    assert "top-secret" not in result.detail
