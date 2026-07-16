"""Subagent lease correlation via child_session_id + default-off delegation."""

from __future__ import annotations

import importlib

import pytest

from hca.state import StateDB


@pytest.fixture
def plugin_env(tmp_path, monkeypatch):
    db_path = tmp_path / "hca.sqlite"
    StateDB(db_path)  # create schema
    monkeypatch.setenv("HCA_STATE_DB", str(db_path))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_parent")
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "7")
    plugin = importlib.import_module("hca.plugin")
    return {"db": StateDB(db_path), "plugin": plugin}


def _subagent_credits(db):
    return db.active_lease_credits(kind="subagent")


def test_delegation_blocked_by_default(plugin_env, monkeypatch):
    monkeypatch.delenv("HCA_MAX_SUBAGENT_CREDITS", raising=False)  # default 0
    plugin = plugin_env["plugin"]
    out = plugin.on_pre_tool_call("delegate_task", {"tasks": [{"goal": "x"}]})
    assert out and out.get("action") == "block"
    assert "budget exceeded" in out["message"]


def test_optin_reserve_start_stop_exact_child(plugin_env, monkeypatch):
    monkeypatch.setenv("HCA_MAX_SUBAGENT_CREDITS", "2")
    plugin, db = plugin_env["plugin"], plugin_env["db"]

    # reserve 2 provisional leases
    assert plugin.on_pre_tool_call("delegate_task", {"tasks": [1, 2]}) is None
    assert _subagent_credits(db) == 2

    # two children start → provisional reservations become exact leases
    plugin.on_subagent_start(child_subagent_id="sa-1", child_session_id="sess-1")
    plugin.on_subagent_start(child_subagent_id="sa-2", child_session_id="sess-2")
    assert _subagent_credits(db) == 2  # still 2 (converted, not added)

    # out-of-order stop releases the EXACT child by session id
    plugin.on_subagent_stop(child_session_id="sess-1")
    assert _subagent_credits(db) == 1
    # the surviving lease is sess-2
    with db.connection() as conn:
        rows = [r["lease_id"] for r in conn.execute(
            "SELECT lease_id FROM leases WHERE kind='subagent'"
        ).fetchall()]
    assert rows == ["subagent-sess-2"]

    plugin.on_subagent_stop(child_session_id="sess-2")
    assert _subagent_credits(db) == 0


def test_budget_ceiling_not_exceeded_by_parent_and_child(plugin_env, monkeypatch):
    monkeypatch.setenv("HCA_MAX_SUBAGENT_CREDITS", "2")
    plugin = plugin_env["plugin"]
    assert plugin.on_pre_tool_call("delegate_task", {"tasks": [1, 2]}) is None
    # a further delegation would push past the ceiling → blocked
    out = plugin.on_pre_tool_call("delegate_task", {"tasks": [1]})
    assert out and out["action"] == "block"


def test_long_running_child_stays_counted(plugin_env, monkeypatch):
    monkeypatch.setenv("HCA_MAX_SUBAGENT_CREDITS", "2")
    plugin, db = plugin_env["plugin"], plugin_env["db"]
    plugin.on_pre_tool_call("delegate_task", {"tasks": [1]})
    plugin.on_subagent_start(child_subagent_id="sa-9", child_session_id="sess-9")
    # no fixed expiry: the exact lease has no expires_at and is never reaped
    with db.connection() as conn:
        row = conn.execute(
            "SELECT expires_at FROM leases WHERE lease_id='subagent-sess-9'"
        ).fetchone()
    assert row is not None and row["expires_at"] is None
    assert _subagent_credits(db) == 1  # still counted


def test_session_end_reconciles_orphans(plugin_env, monkeypatch):
    monkeypatch.setenv("HCA_MAX_SUBAGENT_CREDITS", "3")
    plugin, db = plugin_env["plugin"], plugin_env["db"]
    # reserve some, then start one; a reservation never becomes a start (orphan)
    plugin.on_pre_tool_call("delegate_task", {"tasks": [1, 2]})
    plugin.on_subagent_start(child_subagent_id="sa", child_session_id="sess-x")
    assert _subagent_credits(db) >= 1
    plugin.on_session_end()
    # parent's leases (including the orphan reservation) are released
    assert _subagent_credits(db) == 0
