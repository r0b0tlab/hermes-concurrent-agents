"""Hermes plugin registration must match the live PluginContext contract."""

from __future__ import annotations

import importlib
import inspect
import tomllib
from pathlib import Path

import pytest

from hca.plugin import on_pre_tool_call, register
from hca.plugin_schemas import TEAM_TOOL_NAMES
from hca.plugin_tools import TOOL_HANDLERS


class ExactHermesContext:
    """Signature-compatible stand-in for hermes_cli.plugins.PluginContext."""

    def __init__(self):
        self.hooks = {}
        self.tools = {}

    def register_hook(self, hook_name, callback):
        self.hooks[hook_name] = callback

    def register_tool(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler,
        check_fn=None,
        requires_env=None,
        is_async: bool = False,
        description: str = "",
        emoji: str = "",
        override: bool = False,
    ):
        self.tools[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            "description": description,
        }


def test_pip_entrypoint_loads_a_module_with_register():
    project = Path(__file__).resolve().parents[2]
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    reference = metadata["project"]["entry-points"]["hermes_agent.plugins"]["hca"]
    # Hermes PluginManager calls ep.load(), then getattr(loaded, "register").
    assert ":" not in reference
    loaded = importlib.import_module(reference)
    assert callable(getattr(loaded, "register", None))


def test_register_exposes_exact_five_tools_with_provider_safe_schemas():
    ctx = ExactHermesContext()
    register(ctx)
    assert tuple(ctx.tools) == TEAM_TOOL_NAMES
    assert set(ctx.hooks) == {
        "pre_tool_call",
        "subagent_start",
        "subagent_stop",
        "on_session_end",
    }
    for name, entry in ctx.tools.items():
        assert entry["toolset"] == "hca"
        assert entry["handler"] is TOOL_HANDLERS[name]
        assert set(entry["schema"]) == {"name", "description", "parameters"}
        assert entry["schema"]["name"] == name
        assert entry["description"]


def test_register_fails_loudly_without_hermes_registrar():
    class HooksOnly:
        def register_hook(self, *_args, **_kwargs):
            pass

    with pytest.raises(RuntimeError, match="register_tool"):
        register(HooksOnly())


def _live_module(monkeypatch, name):
    try:
        return importlib.import_module(name)
    except ImportError:
        candidate = Path.home() / ".hermes" / "hermes-agent"
        if candidate.is_dir():
            monkeypatch.syspath_prepend(str(candidate))
        return pytest.importorskip(name)


def test_live_plugin_context_signature_is_compatible_when_installed(monkeypatch):
    plugins = _live_module(monkeypatch, "hermes_cli.plugins")
    params = inspect.signature(plugins.PluginContext.register_tool).parameters
    assert {"name", "toolset", "schema", "handler"} <= set(params)


def test_live_hermes_dispatcher_enforces_stop_approval_directive(monkeypatch):
    plugins = _live_module(monkeypatch, "hermes_cli.plugins")
    approval = _live_module(monkeypatch, "tools.approval")
    directive = on_pre_tool_call(
        "hca_team_stop",
        {"run_id": "run-live-gate", "authorization": "run-live-gate"},
    )
    monkeypatch.setattr(plugins, "invoke_hook", lambda *args, **kwargs: [directive])
    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *args, **kwargs: {"approved": False, "message": "human denied"},
    )
    assert plugins.resolve_pre_tool_block("hca_team_stop", {}) == "human denied"
    monkeypatch.setattr(
        approval,
        "request_tool_approval",
        lambda *args, **kwargs: {"approved": True},
    )
    assert plugins.resolve_pre_tool_block("hca_team_stop", {}) is None
