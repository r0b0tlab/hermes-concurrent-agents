"""Stable, versioned JSON schemas for the HCA Hermes plugin toolset.

Exactly five team tools are registered — no unrestricted command passthrough.
Schemas are JSON-native and idempotency-aware; every tool result includes a
``remediation`` string suitable for agent reasoning. Mutating tools honor
Hermes approvals (``hca_team_stop`` is approval-gated).
"""

from __future__ import annotations

from typing import Any

TOOLSET_VERSION = "1"

# The five team tools. Keep this list closed.
TEAM_TOOL_NAMES = (
    "hca_team_run",
    "hca_team_status",
    "hca_team_collect",
    "hca_team_respond",
    "hca_team_stop",
)


def _result_shape() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "code": {"type": "integer", "description": "0 ok, 2 invalid, 3 preflight, 4 blocked/needs-input, 5 runtime"},
            "action": {"type": "string"},
            "run_id": {"type": "string"},
            "state": {"type": "string"},
            "message": {"type": "string"},
            "remediation": {"type": "string"},
            "data": {"type": "object"},
        },
        "required": ["ok", "code", "action", "run_id", "state"],
    }


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "hca_team_run": {
        "name": "hca_team_run",
        "version": TOOLSET_VERSION,
        "description": (
            "Turn one desired outcome into a supervised concurrent Hermes team "
            "run. Returns a durable run_id handle; pass idempotency_key to make "
            "the call safe to retry."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "what to build/research/ship"},
                "project": {"type": "string", "description": "optional project root path"},
                "team": {"type": "string", "enum": ["default", "small", "reviewed"], "default": "default"},
                "concurrency": {"type": "integer", "minimum": 1, "default": 1},
                "idempotency_key": {"type": "string", "description": "stable key; identical key returns the same run"},
            },
            "required": ["goal"],
        },
        "returns": _result_shape(),
        "mutating": True,
        "approval": False,
    },
    "hca_team_status": {
        "name": "hca_team_status",
        "version": TOOLSET_VERSION,
        "description": "Report a run's state, active agents, blockers, needed input, and outputs. Omit run_id to list recent runs.",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": [],
        },
        "returns": _result_shape(),
        "mutating": False,
        "approval": False,
    },
    "hca_team_collect": {
        "name": "hca_team_collect",
        "version": TOOLSET_VERSION,
        "description": "Return the deterministic result manifest: outcome, evidence, artifacts, unresolved blockers, cleanup. Never reports cancelled/blocked work as success.",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
        "returns": _result_shape(),
        "mutating": False,
        "approval": False,
    },
    "hca_team_respond": {
        "name": "hca_team_respond",
        "version": TOOLSET_VERSION,
        "description": "Answer a run's structured needs_input question. Validates run/question identity and resumes only the blocked branch.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "question_id": {"type": "string"},
                "response": {"type": "string"},
            },
            "required": ["run_id", "question_id", "response"],
        },
        "returns": _result_shape(),
        "mutating": True,
        "approval": False,
    },
    "hca_team_stop": {
        "name": "hca_team_stop",
        "version": TOOLSET_VERSION,
        "description": "Cancel a run (approval-gated). Marks stopping→cancelled, preserves partial work; never turns cancellation into completion.",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
        "returns": _result_shape(),
        "mutating": True,
        "approval": True,
    },
}


def tool_schema(name: str) -> dict[str, Any]:
    return TOOL_SCHEMAS[name]


def all_tool_schemas() -> list[dict[str, Any]]:
    return [TOOL_SCHEMAS[n] for n in TEAM_TOOL_NAMES]
