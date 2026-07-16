"""HCA Hermes plugin tool handlers.

These are the agent-facing surface of the *same* typed service the CLI uses
(`hca.service.FleetService`). No separate lifecycle logic lives here — each
handler validates inputs, calls a service method, and returns its JSON result.

Handlers accept an optional ``service`` for deterministic testing; in the
plugin runtime the service is built from the resolved fleet config.
"""

from __future__ import annotations

from typing import Any, Optional

from hca.plugin_schemas import all_tool_schemas
from hca.service import EXIT_INVALID, FleetService, ServiceResult


def _service(service: Optional[FleetService] = None) -> FleetService:
    if service is not None:
        return service
    from hca.config import load_fleet_config

    cfg = load_fleet_config()
    return FleetService(cfg)


def _invalid(action: str, message: str, remediation: str = "") -> dict[str, Any]:
    return ServiceResult(
        False, EXIT_INVALID, action, "", "invalid", message, remediation
    ).to_dict()


def hca_team_run(
    goal: str = "",
    project: str = "",
    team: str = "default",
    concurrency: int = 1,
    idempotency_key: str = "",
    *,
    service: Optional[FleetService] = None,
) -> dict[str, Any]:
    if not goal or not str(goal).strip():
        return _invalid("run", "goal is required", "call with a non-empty goal")
    svc = _service(service)
    return svc.run(
        goal,
        project_root=project,
        team=team,
        concurrency=int(concurrency or 1),
        idempotency_key=idempotency_key,
    ).to_dict()


def hca_team_status(
    run_id: str = "", *, service: Optional[FleetService] = None
) -> dict[str, Any]:
    return _service(service).status(run_id).to_dict()


def hca_team_collect(
    run_id: str = "", *, service: Optional[FleetService] = None
) -> dict[str, Any]:
    if not run_id:
        return _invalid("collect", "run_id is required")
    return _service(service).collect(run_id).to_dict()


def hca_team_respond(
    run_id: str = "",
    question_id: str = "",
    response: str = "",
    *,
    service: Optional[FleetService] = None,
) -> dict[str, Any]:
    if not run_id or not question_id:
        return _invalid("respond", "run_id and question_id are required")
    return _service(service).respond(run_id, question_id, response).to_dict()


def hca_team_stop(
    run_id: str = "", *, service: Optional[FleetService] = None
) -> dict[str, Any]:
    # Approval-gating is enforced by the Hermes tool layer (schema.approval).
    if not run_id:
        return _invalid("stop", "run_id is required")
    return _service(service).stop(run_id).to_dict()


TOOL_HANDLERS = {
    "hca_team_run": hca_team_run,
    "hca_team_status": hca_team_status,
    "hca_team_collect": hca_team_collect,
    "hca_team_respond": hca_team_respond,
    "hca_team_stop": hca_team_stop,
}


def register_tools(ctx: Any) -> list[str]:
    """Register the five team tools with a Hermes plugin context.

    Supports a couple of common context shapes; a no-op (returns names) if the
    context does not expose a tool registrar, so discovery never crashes.
    """
    registered: list[str] = []
    schemas = {s["name"]: s for s in all_tool_schemas()}
    for name, handler in TOOL_HANDLERS.items():
        schema = schemas[name]
        if hasattr(ctx, "register_tool"):
            try:
                ctx.register_tool(name, handler, schema=schema)
                registered.append(name)
                continue
            except Exception:
                pass
        if hasattr(ctx, "add_tool"):
            try:
                ctx.add_tool(name, handler, schema)
                registered.append(name)
            except Exception:
                pass
    return registered
