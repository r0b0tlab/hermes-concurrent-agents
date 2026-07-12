"""OpenAI-compatible health probes shared by engines."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse


@dataclass
class ProbeResult:
    ok: bool
    detail: str
    data: Optional[dict[str, Any]] = None


def _http_json(url: str, *, method: str = "GET", body: Optional[dict] = None, timeout: float = 10.0) -> Any:
    data = None
    headers = {"Content-Type": "application/json", "User-Agent": "hca/2.0"}
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def is_local_endpoint(endpoint: str) -> bool:
    host = urlparse(endpoint).hostname or ""
    return host in {"127.0.0.1", "localhost", "0.0.0.0", "::1"} or host.startswith("10.") or host.startswith("192.168.") or host.startswith("172.")


def probe_models(endpoint: str, expected_model: str = "", timeout: float = 10.0) -> ProbeResult:
    base = endpoint.rstrip("/")
    url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    try:
        data = _http_json(url, timeout=timeout)
        ids = [m.get("id", "") for m in data.get("data", [])]
        if expected_model and expected_model not in ids:
            return ProbeResult(False, f"model {expected_model!r} not in /models ids={ids}", data)
        return ProbeResult(True, f"/models ok ids={ids[:8]}", data)
    except Exception as exc:
        return ProbeResult(False, f"models probe failed: {exc}")


def probe_chat(endpoint: str, model: str, timeout: float = 30.0) -> ProbeResult:
    base = endpoint.rstrip("/")
    url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Say HCA_OK and nothing else."}],
        "max_tokens": 16,
        "temperature": 0,
    }
    try:
        data = _http_json(url, method="POST", body=body, timeout=timeout)
        if "error" in data:
            return ProbeResult(False, f"chat error: {data['error']}", data)
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return ProbeResult(True, f"chat ok: {content[:80]!r}", data)
    except Exception as exc:
        return ProbeResult(False, f"chat probe failed: {exc}")


def probe_tools(endpoint: str, model: str, timeout: float = 45.0) -> ProbeResult:
    """Best-effort tool-calling probe; some local servers may not support tools."""
    base = endpoint.rstrip("/")
    url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Call the echo tool with text=ping"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "Echo text",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                },
            }
        ],
        "tool_choice": "auto",
        "max_tokens": 64,
        "temperature": 0,
    }
    try:
        data = _http_json(url, method="POST", body=body, timeout=timeout)
        if "error" in data:
            return ProbeResult(False, f"tools error: {data['error']}", data)
        msg = data.get("choices", [{}])[0].get("message", {})
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            return ProbeResult(True, f"tools ok: {len(tool_calls)} call(s)", data)
        # Not all models honor tools; treat missing as soft fail
        return ProbeResult(False, "tools: no tool_calls in response (soft fail)", data)
    except Exception as exc:
        return ProbeResult(False, f"tools probe failed: {exc}")
