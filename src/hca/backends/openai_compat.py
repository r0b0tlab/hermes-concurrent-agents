"""OpenAI-compatible health probes shared by engines."""

from __future__ import annotations

import json
import ipaddress
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


def parse_prometheus(text: str) -> dict[str, float]:
    """Parse simple metric-value lines from Prometheus exposition text."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0].split("{", 1)[0]
        try:
            out[name] = float(parts[-1])
        except ValueError:
            continue
    return out


def first_metric(metrics: dict[str, float], keys: list[str]) -> Optional[float]:
    """First present value among keys; 0.0 is a valid reading, not a miss."""
    for k in keys:
        v = metrics.get(k)
        if v is not None:
            return float(v)
    return None


def fetch_text(url: str, timeout: float = 5.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "hca/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(errors="replace")


def is_local_endpoint(endpoint: str) -> bool:
    host = urlparse(endpoint).hostname or ""
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_unspecified


def endpoint_scope(endpoint: str) -> str:
    if not endpoint:
        return "unset"
    return "local" if is_local_endpoint(endpoint) else "remote"


def safe_error_detail(error: object, endpoint: str, *, limit: int = 300) -> str:
    """Return useful error text with all connection identifiers removed."""
    detail = f"{type(error).__name__}: {error}"
    parsed = urlparse(endpoint)
    tokens = {
        endpoint,
        endpoint.rstrip("/"),
        parsed.netloc,
        parsed.hostname or "",
        parsed.username or "",
        parsed.password or "",
    }
    for token in sorted((item for item in tokens if item), key=len, reverse=True):
        detail = detail.replace(token, "<endpoint>")
    return detail[:limit]


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
        return ProbeResult(False, f"models probe failed: {safe_error_detail(exc, endpoint)}")


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
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return ProbeResult(False, "chat response has no choices", data)
        message = choices[0].get("message") or {}
        if not isinstance(message, dict):
            return ProbeResult(False, "chat response has no message object", data)
        text_field = ""
        content = ""
        for candidate in ("content", "reasoning", "reasoning_content"):
            value = message.get(candidate)
            if isinstance(value, str) and value.strip():
                text_field = candidate
                content = value
                break
        if not content:
            return ProbeResult(False, "chat response has no text or reasoning content", data)
        return ProbeResult(True, f"chat ok ({text_field}): {content[:80]!r}", data)
    except Exception as exc:
        return ProbeResult(False, f"chat probe failed: {safe_error_detail(exc, endpoint)}")


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
        return ProbeResult(False, f"tools probe failed: {safe_error_detail(exc, endpoint)}")
