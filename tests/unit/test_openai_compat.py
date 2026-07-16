from __future__ import annotations

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
