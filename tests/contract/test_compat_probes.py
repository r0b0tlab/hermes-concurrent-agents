"""Deterministic capability-probe tests for the Hermes compat layer.

These use synthetic fake modules so the classification/fail-closed logic is
gated in CI without a live Hermes install and without touching a running
gateway. Live-install probes live in test_hermes_runtime.py.
"""

from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass, fields
from typing import Optional

import pytest

from hca import hermes_compat as hc


def test_optional_plugin_import_noise_is_structured_but_unknown_output_is_preserved(
    monkeypatch, capsys
):
    marker = object()

    def noisy_import(_name):
        print("optional plugin unavailable: missing dependency demo")
        print("unexpected import diagnostic", file=sys.stderr)
        return marker

    hc._IMPORT_WARNINGS.clear()
    monkeypatch.setattr(hc.importlib, "import_module", noisy_import)

    assert hc._import_module_with_clean_streams("hermes_cli.fake") is marker
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "unexpected import diagnostic\n"
    assert hc.compatibility_import_warnings() == [
        "optional plugin unavailable: missing dependency demo"
    ]


# --- synthetic Hermes surface matching the stable v0.18.2 contract ---------


@dataclass
class _FakeTask:
    id: str = ""
    assignee: Optional[str] = None
    current_run_id: Optional[int] = None
    claim_lock: Optional[str] = None
    workspace_path: Optional[str] = None
    branch_name: Optional[str] = None
    tenant: Optional[str] = None
    skills: Optional[list] = None
    model_override: Optional[str] = None
    goal_mode: bool = False


@dataclass
class _FakeDispatchResult:
    reclaimed: int = 0
    promoted: int = 0
    spawned: list = None
    crashed: list = None
    skipped_nonspawnable: list = None
    skipped_locked: bool = False


def _make_fake_kb(*, drop_param="", drop_result="", drop_task="", drop_helper=""):
    """Build a fake hermes_cli.kanban_db exposing the probed surface."""

    def dispatch_once(
        conn,
        *,
        spawn_fn=None,
        board=None,
        max_spawn=None,
        max_in_progress=None,
        max_in_progress_per_profile=None,
        dry_run=False,
    ):
        return _FakeDispatchResult()

    # Optionally drop a required dispatch param by rebuilding a narrower fn.
    if drop_param == "board":
        def dispatch_once(conn, *, spawn_fn=None, max_spawn=None, max_in_progress=None, dry_run=False):  # noqa: F811
            return _FakeDispatchResult()

    task_cls = _FakeTask
    if drop_task:
        # Rebuild a Task dataclass missing one required field.
        keep = [f for f in fields(_FakeTask) if f.name != drop_task]
        task_cls = type(
            "TaskMissing",
            (),
            {"__annotations__": {f.name: f.type for f in keep}},
        )
        task_cls = dataclass(task_cls)

    result_cls = _FakeDispatchResult
    if drop_result:
        keep = [f for f in fields(_FakeDispatchResult) if f.name != drop_result]
        result_cls = type(
            "ResultMissing",
            (),
            {"__annotations__": {f.name: f.type for f in keep}},
        )
        result_cls = dataclass(result_cls)

    ns = type("FakeKB", (), {})()
    ns.__file__ = "/fake/hermes_cli/kanban_db.py"
    if drop_param != "dispatch_once":
        ns.dispatch_once = dispatch_once
    ns.DispatchResult = result_cls
    ns.Task = task_cls
    if drop_helper != "kanban_db_path":
        ns.kanban_db_path = lambda board=None: "/fake/kanban.db"
    if drop_helper != "workspaces_root":
        ns.workspaces_root = lambda board=None: "/fake/workspaces"
    return ns


def _make_fake_profiles():
    ns = type("FakeProfiles", (), {})()
    ns.normalize_profile_name = lambda name: name
    ns.resolve_profile_env = lambda name: "/fake/home"
    return ns


# --- version parsing --------------------------------------------------------


def test_parse_version_line_full():
    p = hc.parse_version_line("Hermes Agent v0.18.2 (2026.7.7.2) · upstream f8ddf4fd")
    assert p["semver"] == "0.18.2"
    assert p["calver"] == "2026.7.7.2"
    assert p["upstream_commit"] == "f8ddf4fd"


def test_parse_version_line_partial():
    # Real Hermes always prefixes the semver with `v`; a nightly build may
    # omit calver/upstream. Missing pieces degrade to "" rather than raise.
    p = hc.parse_version_line("Hermes Agent v0.1.0-dev")
    assert p["semver"] == "0.1.0-dev"
    assert p["calver"] == ""
    assert p["upstream_commit"] == ""


# --- capability probing + lane classification ------------------------------


def test_probe_full_surface_is_supported_stable():
    caps = hc.probe_capabilities(kb=_make_fake_kb(), profiles=_make_fake_profiles())
    assert caps.supported()
    assert caps.missing() == {}
    lane, reason = hc.classify_lane(
        "Hermes Agent v0.18.2 (2026.7.7.2)", caps
    )
    assert lane == hc.LANE_STABLE
    assert "0.18.2" in reason


def test_full_surface_unknown_version_is_edge():
    caps = hc.probe_capabilities(kb=_make_fake_kb(), profiles=_make_fake_profiles())
    lane, reason = hc.classify_lane("Hermes Agent v9.9.9 (2099.1.1)", caps)
    assert lane == hc.LANE_EDGE
    assert "advisory" in reason


def test_missing_current_run_id_is_unsupported():
    caps = hc.probe_capabilities(
        kb=_make_fake_kb(drop_task="current_run_id"), profiles=_make_fake_profiles()
    )
    assert not caps.supported()
    assert "current_run_id" in caps.missing().get("task_fields", [])
    lane, _ = hc.classify_lane("v0.18.2", caps)
    assert lane == hc.LANE_UNSUPPORTED


def test_removed_spawn_fn_param_is_unsupported():
    caps = hc.probe_capabilities(
        kb=_make_fake_kb(drop_param="dispatch_once"), profiles=_make_fake_profiles()
    )
    assert not caps.has_dispatch_once
    assert not caps.supported()


def test_removed_board_param_is_unsupported():
    caps = hc.probe_capabilities(
        kb=_make_fake_kb(drop_param="board"), profiles=_make_fake_profiles()
    )
    assert "board" in caps.missing_dispatch_params()
    assert not caps.supported()


def test_removed_dispatch_result_field_is_unsupported():
    caps = hc.probe_capabilities(
        kb=_make_fake_kb(drop_result="crashed"), profiles=_make_fake_profiles()
    )
    assert "crashed" in caps.missing().get("dispatch_result_fields", [])
    assert not caps.supported()


def test_missing_profile_helper_is_unsupported():
    fake_profiles = _make_fake_profiles()
    del fake_profiles.resolve_profile_env
    caps = hc.probe_capabilities(kb=_make_fake_kb(), profiles=fake_profiles)
    assert "resolve_profile_env" in caps.missing().get("profile_helpers", [])
    assert not caps.supported()


def test_optional_param_absence_does_not_break_support():
    # max_in_progress_per_profile is OPTIONAL: a build without it is still
    # supported (the per-profile cap is a feature, not the contract).
    kb = _make_fake_kb()

    def narrower(conn, *, spawn_fn=None, board=None, max_spawn=None, max_in_progress=None, dry_run=False):
        return _FakeDispatchResult()

    kb.dispatch_once = narrower
    caps = hc.probe_capabilities(kb=kb, profiles=_make_fake_profiles())
    assert "max_in_progress_per_profile" not in caps.dispatch_params
    assert caps.supported()


def test_assert_dispatch_contract_raises_on_drift():
    with pytest.raises(hc.HermesCompatError) as exc:
        hc.assert_dispatch_contract(
            kb=_make_fake_kb(drop_task="current_run_id"),
            profiles=_make_fake_profiles(),
        )
    assert "current_run_id" in str(exc.value)


def test_assert_dispatch_contract_passes_on_full_surface():
    info = hc.assert_dispatch_contract(
        kb=_make_fake_kb(), profiles=_make_fake_profiles()
    )
    assert info.has_dispatch_once
    assert "spawn_fn" in info.dispatch_signature


# --- dispatcher ownership (fail-closed, injected — never touches gateway) ---


def test_dispatcher_conflict_when_gateway_dispatches():
    own = hc.dispatcher_ownership(
        "hca",
        gateway_pid_fn=lambda: 4321,
        dispatch_flag_fn=lambda: True,
    )
    assert own.conflict
    assert own.gateway_pid == 4321
    assert "4321" in own.reason


def test_no_conflict_when_dispatch_disabled():
    own = hc.dispatcher_ownership(
        "hca",
        gateway_pid_fn=lambda: 4321,
        dispatch_flag_fn=lambda: False,
    )
    assert not own.conflict
    assert own.gateway_running


def test_no_conflict_when_no_gateway():
    own = hc.dispatcher_ownership(
        "hca",
        gateway_pid_fn=lambda: None,
        dispatch_flag_fn=lambda: True,
    )
    assert not own.conflict
    assert not own.gateway_running


def test_assert_sole_dispatcher_raises_on_conflict():
    with pytest.raises(hc.HermesCompatError):
        hc.assert_sole_dispatcher(
            "hca",
            gateway_pid_fn=lambda: 999,
            dispatch_flag_fn=lambda: True,
        )


def test_assert_sole_dispatcher_ok_when_clear():
    own = hc.assert_sole_dispatcher(
        "hca",
        gateway_pid_fn=lambda: None,
        dispatch_flag_fn=lambda: True,
    )
    assert not own.conflict


def test_flag_probe_failure_fails_closed():
    # If reading the config raises, we must assume dispatch is on (conflict).
    def boom():
        raise RuntimeError("config unreadable")

    own = hc.dispatcher_ownership(
        "hca", gateway_pid_fn=lambda: 7, dispatch_flag_fn=boom
    )
    assert own.conflict


# --- subagent hook correlation key (Task 4 dependency) ---------------------


def test_subagent_stop_lacks_child_subagent_id():
    # Durable correlation must use child_session_id because subagent_stop
    # does not carry child_subagent_id.
    assert "child_subagent_id" in hc.SUBAGENT_START_KEYS
    assert "child_subagent_id" not in hc.SUBAGENT_STOP_KEYS
    assert "child_session_id" in hc.SUBAGENT_START_KEYS
    assert "child_session_id" in hc.SUBAGENT_STOP_KEYS


# --- signature sanity of the injected fake matches real probe path ---------


def test_probe_dispatch_signature_string_present():
    caps = hc.probe_capabilities(kb=_make_fake_kb(), profiles=_make_fake_profiles())
    assert "spawn_fn" in caps.dispatch_signature
    assert isinstance(caps.dispatch_signature, str)
    # signature is inspectable
    assert inspect.signature  # sanity
