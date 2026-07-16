"""Stable-release guard for unsupported remote agent placement."""

from __future__ import annotations

import json
from pathlib import Path

from hca.cli import main
from hca.config import load_fleet_config
from hca.service import FleetService


def test_cluster_nodes_up_fails_before_ssh(monkeypatch, tmp_path: Path, capsys):
    calls: list[tuple] = []

    def forbidden_ssh(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("remote startup must fail before SSH")

    monkeypatch.setattr("hca.cli.run_ssh", forbidden_ssh, raising=False)
    state = tmp_path / "cluster"
    rc = main(
        [
            "cluster",
            "nodes",
            "up",
            "--state-dir",
            str(state),
            "--json",
        ]
    )

    assert rc == 3
    assert calls == []
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["code"] == 3
    assert payload["state"] == "unsupported"
    assert "remote agent placement" in payload["message"]
    assert "remote model endpoint" in payload["remediation"]
    assert not state.exists()


def test_cluster_role_init_fails_before_state_or_profiles(tmp_path: Path, capsys):
    state = tmp_path / "control"
    rc = main(
        [
            "init",
            "--role",
            "control",
            "--state-dir",
            str(state),
            "--model",
            "dummy",
            "--json",
        ]
    )

    assert rc == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "unsupported"
    assert not state.exists()


def test_cluster_role_up_fails_before_supervisor(monkeypatch, tmp_path: Path, capsys):
    constructed = []

    class ForbiddenSupervisor:
        def __init__(self, *_args, **_kwargs):
            constructed.append(True)
            raise AssertionError("unsupported role must fail before supervisor construction")

    monkeypatch.setattr("hca.cli.Supervisor", ForbiddenSupervisor)
    rc = main(
        [
            "up",
            "--role",
            "node",
            "--state-dir",
            str(tmp_path / "node"),
            "--json",
        ]
    )

    assert rc == 3
    assert constructed == []
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "unsupported"


def test_shared_service_rejects_remote_placement_without_planning(tmp_path: Path):
    planned: list[str] = []

    class ForbiddenOrchestrator:
        def plan(self, spec, store):
            planned.append(spec.run_id)
            raise AssertionError("unsupported remote placement must not plan")

        def execute(self, spec, store):
            raise AssertionError("unsupported remote placement must not execute")

    cfg = load_fleet_config(role="control", state_dir=str(tmp_path / "service"))
    svc = FleetService(cfg, orchestrator=ForbiddenOrchestrator(), launch_controller=False)
    result = svc.run("do remote work")

    assert result.ok is False
    assert result.code == 3
    assert result.state == "unsupported"
    assert "remote agent placement" in result.message
    assert planned == []
    assert svc.store.list_runs() == []
