"""Hermes plugin registration must match the live PluginContext contract."""

from __future__ import annotations

import inspect

import pytest

from hca.plugin import register
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


def test_live_plugin_context_signature_is_compatible_when_installed():
    plugins = pytest.importorskip("hermes_cli.plugins")
    params = inspect.signature(plugins.PluginContext.register_tool).parameters
    assert {"name", "toolset", "schema", "handler"} <= set(params)
