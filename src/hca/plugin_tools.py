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
    run_id: str = "",
    authorization: str = "",
    *,
    service: Optional[FleetService] = None,
) -> dict[str, Any]:
    # Honest, code-enforced authorization gate. Hermes does NOT enforce a
    # tool's ``approval`` schema flag, so a stop cannot rely on it. Instead HCA
    # requires the caller to explicitly restate the run id as ``authorization``
    # before a cancellation runs — a real confirmation that prevents an agent
    # (or a mis-fired retry) from cancelling the wrong run. Cancellation kills
    # worker process groups, so it must be deliberate.
    if not run_id:
        return _invalid("stop", "run_id is required")
    if str(authorization).strip() != str(run_id).strip():
        return ServiceResult(
            False,
            EXIT_INVALID,
            "stop",
            run_id,
            "authorization_required",
            "stop requires explicit authorization to cancel this run",
            f're-call hca_team_stop with authorization="{run_id}" to confirm',
            data={"authorization_required": True},
        ).to_dict()
    return _service(service).stop(run_id).to_dict()


TOOL_HANDLERS = {
    "hca_team_run": hca_team_run,
    "hca_team_status": hca_team_status,
    "hca_team_collect": hca_team_collect,
    "hca_team_respond": hca_team_respond,
    "hca_team_stop": hca_team_stop,
}


def _runtime_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the provider-safe function schema accepted by Hermes.

    HCA keeps version/result/approval metadata in ``plugin_schemas`` for its
    own typed contract, but OpenAI-compatible function definitions only allow
    name/description/parameters (plus provider-supported strict flags).  Do not
    leak HCA metadata into the model request.
    """
    return {
        "name": schema["name"],
        "description": schema["description"],
        "parameters": schema["parameters"],
    }


def register_tools(ctx: Any) -> list[str]:
    """Register the five team tools through the current Hermes context API.

    ``PluginContext.register_tool`` is keyword-only in practice even though
    Python does not enforce that: ``name, toolset, schema, handler``.  A prior
    positional call accidentally passed the handler as ``toolset`` and then
    swallowed the resulting ``TypeError``, so a plugin could appear loaded
    while exposing zero tools.  Registration now fails loudly.
    """
    schemas = {s["name"]: s for s in all_tool_schemas()}
    registered: list[str] = []
    registrar = getattr(ctx, "register_tool", None)
    if not callable(registrar):
        raise RuntimeError("Hermes PluginContext.register_tool is unavailable")
    for name, handler in TOOL_HANDLERS.items():
        schema = schemas[name]
        registrar(
            name=name,
            toolset="hca",
            schema=_runtime_schema(schema),
            handler=handler,
            description=schema["description"],
            emoji="🧭",
        )
        registered.append(name)
    return registered
