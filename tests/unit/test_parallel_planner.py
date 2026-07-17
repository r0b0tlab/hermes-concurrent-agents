from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from hca.config import load_fleet_config
from hca.evidence import ExecutionEvidence
from hca.kanban_orchestrator import KanbanOrchestrator, default_planner
from hca.run import RunBudgets, RunSpec, RunStore
from hca.state import RunRecord, StateDB


def _spec(
    *, concurrency=1, acceptance=(), independent=False, review="never", max_tasks=20
):
    return RunSpec(
        run_id="run-parallel",
        goal="Produce independently verifiable outputs and combine them",
        acceptance_criteria=tuple(acceptance),
        independent_criteria=independent,
        concurrency=concurrency,
        review_policy=review,
        budgets=RunBudgets(max_tasks=max_tasks),
        board="hca-test",
        created_at=time.time(),
    )


def test_one_step_goal_remains_one_execution_task():
    nodes = default_planner(
        load_fleet_config(model="m"),
        _spec(concurrency=4),
        "hca-default-orchestrator-01",
        "hca-default-coder-01",
    )
    assert [node.kind for node in nodes] == ["work", "final"]


def test_acceptance_criteria_create_bounded_parallel_fanout_and_fanin():
    criteria = ("produce alpha result", "produce beta result", "produce gamma result")
    nodes = default_planner(
        load_fleet_config(model="m"),
        _spec(concurrency=2, acceptance=criteria, independent=True),
        "hca-default-orchestrator-01",
        "hca-default-coder-01",
    )
    work = [node for node in nodes if node.kind == "work"]
    assert len(work) == 3
    assert {criterion for node in work for criterion in node.acceptance_criteria} == set(criteria)
    integration = next(node for node in nodes if node.kind == "integration")
    assert integration.depends_on == tuple(node.id for node in work)
    assert set(integration.acceptance_criteria) == set(criteria)
    final = next(node for node in nodes if node.kind == "final")
    assert final.depends_on == (integration.id,)


def test_parallel_fanout_reserves_task_budget_for_fanin_and_review():
    criteria = tuple(f"criterion {index}" for index in range(8))
    nodes = default_planner(
        load_fleet_config(model="m"),
        _spec(
            concurrency=8,
            acceptance=criteria,
            independent=True,
            review="always",
            max_tasks=6,
        ),
        "hca-default-orchestrator-01",
        "hca-default-coder-01",
    )
    assert len(nodes) <= 6
    assert len([node for node in nodes if node.kind == "work"]) == 2
    assert [node.kind for node in nodes][-4:] == ["integration", "review", "gate", "final"]
    assert {criterion for node in nodes if node.kind == "work" for criterion in node.acceptance_criteria} == set(criteria)


def test_parallel_work_is_round_robined_over_concrete_worker_profiles(tmp_path):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path / "state"))
    cfg.name = "parallel"
    cfg.profile_slots = {"orchestrator": 1, "coder": 2, "qa": 1}
    spec = _spec(
        concurrency=2, acceptance=("alpha", "beta"), independent=True
    )
    nodes = default_planner(cfg, spec, "hca-parallel-orchestrator-01", "hca-parallel-coder-01")
    orch = KanbanOrchestrator.__new__(KanbanOrchestrator)
    orch.cfg = cfg
    children = orch._nodes_to_children(
        nodes,
        "hca-parallel-orchestrator-01",
        "hca-parallel-coder-01",
    )
    work_assignees = [
        child["assignee"]
        for node, child in zip(nodes, children)
        if node.kind == "work"
    ]
    assert work_assignees == ["hca-parallel-coder-01", "hca-parallel-coder-02"]
    work_bodies = [
        child["body"]
        for node, child in zip(nodes, children)
        if node.kind == "work"
    ]
    assert all("HCA_RESULT_COMMIT: <40-hex-commit>" in body for body in work_bodies)
    assert all("kanban_complete" in body and "git rev-parse HEAD" in body for body in work_bodies)
    final_body = next(
        child["body"]
        for node, child in zip(nodes, children)
        if node.kind == "final"
    )
    assert "HCA_RESULT_COMMIT" not in final_body


def test_multiple_criteria_do_not_imply_independence():
    nodes = default_planner(
        load_fleet_config(model="m"),
        _spec(concurrency=4, acceptance=("alpha", "beta")),
        "hca-default-orchestrator-01",
        "hca-default-coder-01",
    )
    assert [node.kind for node in nodes] == ["work", "final"]
    assert nodes[0].acceptance_criteria == ("alpha", "beta")


def test_concurrency_changes_wave_not_the_declared_independent_dag():
    cfg = load_fleet_config(model="m")
    criteria = ("alpha", "beta", "gamma")
    c1 = default_planner(
        cfg,
        _spec(concurrency=1, acceptance=criteria, independent=True),
        "planner",
        "worker",
    )
    c3 = default_planner(
        cfg,
        _spec(concurrency=3, acceptance=criteria, independent=True),
        "planner",
        "worker",
    )
    assert [(n.id, n.kind, n.depends_on) for n in c1] == [
        (n.id, n.kind, n.depends_on) for n in c3
    ]


def test_dispatch_uses_configured_tmux_socket(monkeypatch, tmp_path):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path / "state"))
    cfg.tmux_socket = "configured-socket"
    seen = {}

    class RecordingTmux:
        def __init__(self, socket):
            seen["socket"] = socket

    def fake_dispatch(_cfg, _state, tmux, **kwargs):
        seen["tmux"] = tmux
        seen["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr("hca.tmux.TmuxManager", RecordingTmux)
    monkeypatch.setattr("hca.kanban.dispatch_tick", fake_dispatch)
    orchestrator = KanbanOrchestrator(cfg, enforce_sole_dispatcher=False)

    assert orchestrator._dispatch_tick(
        1, ["t-allowed"], max_in_progress=2
    ) == {"ok": True}
    assert seen["socket"] == "configured-socket"
    assert seen["kwargs"]["allowed_task_ids"] == {"t-allowed"}
    assert seen["kwargs"]["max_spawn"] == 1
    assert seen["kwargs"]["max_in_progress"] == 2


def test_recovery_tick_admits_only_remaining_requested_concurrency(
    monkeypatch, tmp_path
):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path / "state"))
    orchestrator = KanbanOrchestrator(cfg, enforce_sole_dispatcher=False)
    spec = _spec(concurrency=2, acceptance=("a", "b", "c"), independent=True)
    mapping = {
        "root_task_id": "root",
        "child_task_ids": ["live", "ready-a", "ready-b"],
        "node_kinds": {"live": "work", "ready-a": "work", "ready-b": "work"},
        "reviewer_profile": "",
    }
    dispatched = []

    store = RunStore(tmp_path / "runs.sqlite")

    monkeypatch.setattr(orchestrator, "advance", lambda _spec, _store: mapping)
    monkeypatch.setattr(
        orchestrator, "_quarantine_worker_graph_expansion", lambda *_args: []
    )
    monkeypatch.setattr(
        orchestrator,
        "_statuses",
        lambda _ids: {"live": "running", "ready-a": "ready", "ready-b": "ready"},
    )
    monkeypatch.setattr(orchestrator, "_reconcile_leases", lambda *_args: None)
    monkeypatch.setattr(orchestrator, "_active_wave_count", lambda *_args: 1)
    monkeypatch.setattr(
        orchestrator,
        "_dispatch_tick",
        lambda max_spawn, ids, **kwargs: dispatched.append((max_spawn, ids, kwargs)) or {},
    )
    monkeypatch.setattr(orchestrator, "_maybe_close_root", lambda *_args: None)
    monkeypatch.setattr(
        orchestrator,
        "_build_evidence",
        lambda *_args: ExecutionEvidence(root_task_id="root"),
    )
    monkeypatch.setattr(orchestrator, "_sync_questions_from_evidence", lambda *_args: 0)

    orchestrator.tick(spec, store, dispatch=True)

    assert len(dispatched) == 1
    max_spawn, task_ids, kwargs = dispatched[0]
    assert max_spawn == 1
    assert task_ids == ["live", "ready-a", "ready-b"]
    assert kwargs["max_in_progress"] == 2
    assert kwargs["owner_run_id"] == spec.run_id
    assert kwargs["max_supervisor_replacements"] == 2
    assert kwargs["requested_disk_mb"] == spec.budgets.max_disk_mb
    assert 1 <= kwargs["remaining_wall_seconds"] <= spec.budgets.wall_seconds


def test_late_task_runtime_is_clamped_to_remaining_run_deadline(monkeypatch, tmp_path):
    db_path = tmp_path / "kanban.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, max_runtime_seconds INTEGER)"
    )
    conn.execute("INSERT INTO tasks VALUES ('late', 'ready', 3600)")
    conn.commit()
    conn.close()
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path / "state"))
    orchestrator = KanbanOrchestrator(cfg, enforce_sole_dispatcher=False)
    monkeypatch.setattr(orchestrator, "_conn", lambda: sqlite3.connect(db_path))

    orchestrator._clamp_task_runtimes(["late"], 17)

    verify = sqlite3.connect(db_path)
    try:
        value = verify.execute(
            "SELECT max_runtime_seconds FROM tasks WHERE id = 'late'"
        ).fetchone()[0]
    finally:
        verify.close()
    assert value == 17


def test_exact_recovery_idempotent_replay_does_not_consume_budget(monkeypatch, tmp_path):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path / "state"))
    orchestrator = KanbanOrchestrator(cfg, enforce_sole_dispatcher=False)
    spec = _spec()
    store = RunStore(tmp_path / "runs.sqlite")
    monkeypatch.setattr(
        orchestrator,
        "_mapping",
        lambda _store, _run_id: {"child_task_ids": ["task-1"]},
    )
    digest = hashlib.sha256(
        f"{spec.run_id}\0task-1\0stable".encode()
    ).hexdigest()
    original = {
        "task_id": "task-1",
        "replacement_number": 1,
        "idempotent_replay": False,
    }
    orchestrator.state.set_meta(
        f"recovery_result:{digest}", json.dumps(original, sort_keys=True)
    )
    orchestrator.state.set_meta(
        f"supervisor_replacements:{spec.run_id}", "1"
    )

    replay = orchestrator.recover(
        spec,
        store,
        "task-1",
        idempotency_key="stable",
    )

    assert replay == {**original, "idempotent_replay": True}
    assert orchestrator.state.get_meta(
        f"supervisor_replacements:{spec.run_id}"
    ) == "1"


def test_completed_task_worker_still_consumes_wave_until_reaped(
    monkeypatch, tmp_path
):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path / "state"))
    orchestrator = KanbanOrchestrator(cfg, enforce_sole_dispatcher=False)
    now = time.time()
    orchestrator.state.upsert_run(
        RunRecord(
            board=orchestrator.board,
            task_id="done-but-live",
            run_id="7",
            slot="hca-default-coder-01",
            node="local",
            tmux_session="hca-default-coder-01",
            pid=4321,
            pid_start_ticks=99,
            hermes_session_id="run-x",
            workspace="/tmp/work",
            status="running",
            started_at=now,
            updated_at=now,
            last_activity="spawned",
            error=None,
        )
    )
    monkeypatch.setattr(
        "hca.kanban_orchestrator.process_identity_matches",
        lambda pid, ticks: (pid, ticks) == (4321, 99),
    )

    assert orchestrator._active_wave_count(
        ["done-but-live", "ready-next"],
        {"done-but-live": "done", "ready-next": "ready"},
    ) == 1


def test_cold_fleet_retires_terminal_tmux_slot_after_exact_reap(monkeypatch, tmp_path):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path / "state"))
    cfg.warm_slots = False
    state = StateDB(Path(cfg.state_dir) / "hca.sqlite")
    now = time.time()
    state.upsert_run(
        RunRecord(
            board=cfg.board,
            task_id="t-terminal",
            run_id="12",
            slot="hca-default-general-01",
            node="local",
            tmux_session="hca-default-general-01",
            pid=4321,
            hermes_session_id="run-parent",
            workspace=None,
            status="running",
            started_at=now,
            updated_at=now,
            last_activity="spawned",
            error=None,
            pid_start_ticks=99,
        )
    )

    class RecordingTmux:
        def __init__(self):
            self.killed = []

        def kill_session(self, name):
            self.killed.append(name)

    tmux = RecordingTmux()
    orchestrator = KanbanOrchestrator(cfg, state=state, tmux=tmux)
    monkeypatch.setattr(orchestrator, "_run_record_is_live", lambda _rec: False)

    orchestrator._reconcile_leases(["t-terminal"], {"t-terminal": "done"})

    assert tmux.killed == ["hca-default-general-01"]
    record = state.latest_run_for_task(cfg.board, "t-terminal")
    assert record is not None
    assert record.status == "completed"


def test_run_wall_budget_is_authoritative_unless_explicitly_shortened(tmp_path):
    cfg = load_fleet_config(model="m", state_dir=str(tmp_path / "state"))
    spec = _spec(concurrency=1, acceptance=("one",))
    production = KanbanOrchestrator(cfg, enforce_sole_dispatcher=False)
    shortened = KanbanOrchestrator(
        cfg, max_wall_seconds=45, enforce_sole_dispatcher=False
    )
    larger_constructor_cap = KanbanOrchestrator(
        cfg, max_wall_seconds=5000, enforce_sole_dispatcher=False
    )

    run_budget = float(spec.budgets.wall_seconds)
    assert production._observation_window_seconds(spec) == run_budget
    assert shortened._observation_window_seconds(spec) == 45
    assert larger_constructor_cap._observation_window_seconds(spec) == run_budget
