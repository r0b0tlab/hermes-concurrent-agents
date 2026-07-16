"""CLI ↔ plugin-tool parity: both surfaces call the same typed service and
produce equivalent state transitions and semantically equal result schemas.
"""

from __future__ import annotations

from hca.config import load_fleet_config
from hca.plugin import on_pre_tool_call
from hca.plugin_schemas import TEAM_TOOL_NAMES, all_tool_schemas
from hca.plugin_tools import (
    hca_team_collect,
    hca_team_respond,
    hca_team_run,
    hca_team_status,
    hca_team_stop,
)
from hca.evidence import ExecutionEvidence, TaskEvidence
from hca.run import RunState
from hca.service import FleetService, ServiceResult


def _done_evidence() -> ExecutionEvidence:
    return ExecutionEvidence(
        root_task_id="t1",
        tasks=[
            TaskEvidence(
                task_id="t1",
                assignee="hca-f-coder-01",
                terminal_status="done",
                run_id=1,
                pid=999,
                result="done",
                is_root=True,
            )
        ],
    )


class Completing:
    def plan(self, spec, store):
        return RunState.PLANNING

    def execute(self, spec, store):
        return _done_evidence()


class NeedsInput:
    def plan(self, spec, store):
        store.add_question(spec.run_id, "which db?")
        return RunState.NEEDS_INPUT

    def execute(self, spec, store):  # pragma: no cover
        return _done_evidence()


def _svc(tmp_path, orch):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path))
    return FleetService(cfg, orchestrator=orch)


def test_only_five_team_tools_registered():
    schemas = all_tool_schemas()
    assert [s["name"] for s in schemas] == list(TEAM_TOOL_NAMES)
    assert len(TEAM_TOOL_NAMES) == 5
    # exactly the stop tool is approval-gated
    approvals = {s["name"]: s["approval"] for s in schemas}
    assert approvals["hca_team_stop"] is True
    assert approvals["hca_team_run"] is False


def test_run_parity_cli_vs_tool(tmp_path):
    svc_cli = _svc(tmp_path / "cli", Completing())
    svc_tool = _svc(tmp_path / "tool", Completing())

    cli = svc_cli.run("build a widget").to_dict()
    tool = hca_team_run("build a widget", service=svc_tool)

    # same terminal state, code, outcome schema
    assert cli["state"] == tool["state"] == "completed"
    assert cli["code"] == tool["code"] == 0

    cli_manifest = svc_cli.collect(cli["run_id"]).to_dict()["data"]["result"]
    tool_manifest = hca_team_collect(tool["run_id"], service=svc_tool)["data"]["result"]
    # identical schema keys and outcome; run ids differ but shape matches
    assert set(cli_manifest) == set(tool_manifest)
    assert cli_manifest["outcome"] == tool_manifest["outcome"] == "success"


def test_needs_input_respond_parity(tmp_path):
    svc = _svc(tmp_path, NeedsInput())
    started = hca_team_run("ambiguous", service=svc)
    assert started["state"] == "needs_input"
    assert started["code"] == 4
    q = svc.store.open_questions(started["run_id"])[0]
    # stale/wrong question id rejected through the tool
    bad = hca_team_respond(started["run_id"], "q-wrong", "x", service=svc)
    assert not bad["ok"]
    ok = hca_team_respond(started["run_id"], q.question_id, "postgres", service=svc)
    assert ok["state"] == "running"


def test_stop_tool_preserves_cancel_semantics(tmp_path):
    svc = _svc(tmp_path, NeedsInput())
    started = hca_team_run("goal", service=svc)
    rid = started["run_id"]
    stopped = hca_team_stop(rid, authorization=rid, service=svc)
    assert stopped["state"] == "cancelled"
    collected = hca_team_collect(rid, service=svc)
    assert collected["data"]["result"]["outcome"] == "cancelled"


def test_stop_tool_requires_explicit_authorization(tmp_path):
    # The handler gate is retained after Hermes' real pre-tool human approval:
    # without authorization=run_id the run is NOT cancelled.
    svc = _svc(tmp_path, NeedsInput())
    started = hca_team_run("goal", service=svc)
    rid = started["run_id"]
    ungated = hca_team_stop(rid, service=svc)  # no authorization
    assert not ungated["ok"]
    assert ungated["data"].get("authorization_required") is True
    # the run was left running, not cancelled
    assert svc.status(rid).state != "cancelled"
    # wrong authorization is also refused
    assert not hca_team_stop(rid, authorization="nope", service=svc)["ok"]
    # correct authorization cancels
    assert hca_team_stop(rid, authorization=rid, service=svc)["state"] == "cancelled"


def test_stop_hook_requests_real_hermes_approval_directive():
    directive = on_pre_tool_call(
        "hca_team_stop",
        {"run_id": "run-123", "authorization": "run-123"},
    )
    assert directive == {
        "action": "approve",
        "message": "Cancel HCA run run-123 and terminate its owned workers?",
        "rule_key": "hca_team_stop:run-123",
    }
    assert on_pre_tool_call("hca_team_stop", {})["action"] == "block"


def test_status_tool_lists_and_targets(tmp_path):
    svc = _svc(tmp_path, Completing())
    r = hca_team_run("g1", service=svc)
    listing = hca_team_status(service=svc)
    assert any(x["run_id"] == r["run_id"] for x in listing["data"]["runs"])
    one = hca_team_status(r["run_id"], service=svc)
    assert one["run_id"] == r["run_id"]


def test_tool_missing_required_args(tmp_path):
    svc = _svc(tmp_path, Completing())
    assert hca_team_run("", service=svc)["code"] == 2
    assert hca_team_collect("", service=svc)["code"] == 2
    assert hca_team_respond("", "", "", service=svc)["code"] == 2
    assert hca_team_stop("", service=svc)["code"] == 2


def test_idempotency_key_parity(tmp_path):
    svc = _svc(tmp_path, Completing())
    a = hca_team_run("g", idempotency_key="stable-1", service=svc)
    b = hca_team_run("g", idempotency_key="stable-1", service=svc)
    assert a["run_id"] == b["run_id"]  # agent-safe retry


def test_run_tool_forwards_the_full_shared_contract():
    class RecordingService(FleetService):
        def __init__(self):
            self.kwargs = {}

        def run(self, goal, **kwargs):
            self.goal = goal
            self.kwargs = kwargs
            return ServiceResult(True, 0, "run", "r1", "running", "ok")

    svc = RecordingService()
    result = hca_team_run(
        "ship it",
        project="/tmp/project",
        team="reviewed",
        concurrency=2,
        review_policy="always",
        constraints=["offline"],
        acceptance_criteria=["tests pass", "docs complete"],
        independent_criteria=True,
        source_profiles=["default"],
        budgets={"max_tasks": 3, "wall_seconds": 60},
        idempotency_key="stable",
        detach=True,
        service=svc,
    )
    assert result["run_id"] == "r1"
    assert svc.goal == "ship it"
    assert svc.kwargs == {
        "project_root": "/tmp/project",
        "constraints": ["offline"],
        "acceptance_criteria": ["tests pass", "docs complete"],
        "independent_criteria": True,
        "source_profiles": ["default"],
        "team": "reviewed",
        "concurrency": 2,
        "review_policy": "always",
        "budgets": {"max_tasks": 3, "wall_seconds": 60},
        "idempotency_key": "stable",
        "detach": True,
    }
    run_properties = {
        s["name"]: s for s in all_tool_schemas()
    }["hca_team_run"]["parameters"]["properties"]
    assert set(svc.kwargs) - {"project_root"} <= set(run_properties)
