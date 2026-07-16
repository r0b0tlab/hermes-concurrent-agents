"""Live Hermes profile creation/config mutation contract."""

from __future__ import annotations

import os
import shutil
import stat

import pytest

from hca.config import load_fleet_config
from hca.profiles import init_profiles

pytestmark = pytest.mark.skipif(not shutil.which("hermes"), reason="hermes CLI missing")


def test_init_clones_real_profile_then_applies_scoped_overrides(monkeypatch, tmp_path):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    # A deliberately recognizable provider/model and credential sentinel prove
    # Hermes clone semantics are used while no secret enters HCA's generated
    # command/config diagnostics.
    (home / "config.yaml").write_text(
        "_config_version: 33\n"
        "model:\n"
        "  provider: openrouter\n"
        "  default: example/source-model\n"
        "plugins:\n"
        "  enabled:\n"
        "    - petdex\n"
        "approvals:\n"
        "  mode: manual\n"
        "hooks_auto_accept: false\n",
        encoding="utf-8",
    )
    secret = "OPENROUTER_API_KEY=profile-test-secret-sentinel\n"
    (home / ".env").write_text(secret, encoding="utf-8")
    os.chmod(home / ".env", 0o600)

    cfg = load_fleet_config(model="ignored", state_dir=str(tmp_path / "state"))
    cfg.name = "live"
    cfg.profile_slots = {"coder": 1}
    cfg.delegation_max_children = 0

    created = init_profiles(cfg, source_profile="default")
    assert created == ["hca-live-coder-01"]

    dst = home / "profiles" / "hca-live-coder-01"
    config = (dst / "config.yaml").read_text(encoding="utf-8")
    assert "provider: openrouter" in config
    assert "default: example/source-model" in config
    assert "petdex" in config and "hca" in config
    assert "dispatch_in_gateway: false" in config
    assert "subagent_auto_approve: false" in config
    assert "hooks_auto_accept: false" in config
    assert "profile-test-secret-sentinel" not in config
    assert (dst / ".env").read_text(encoding="utf-8") == secret
    assert stat.S_IMODE((dst / ".env").stat().st_mode) == 0o600
    assert stat.S_IMODE((dst / "config.yaml").stat().st_mode) == 0o600
    assert stat.S_IMODE(dst.stat().st_mode) == 0o700
