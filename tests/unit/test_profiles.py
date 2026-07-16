"""Profile derivation and least-privilege provisioning tests."""

from __future__ import annotations

import stat
import subprocess

import pytest

from hca.config import load_fleet_config
from hca.profiles import (
    ProfileDerivationError,
    _nested_string_list,
    hermes_profiles_root,
    init_profiles,
    source_defines_yolo,
)


def test_profiles_root_tracks_hermes_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "custom"))
    assert hermes_profiles_root() == tmp_path / "custom" / "profiles"


def test_nested_plugin_list_supports_block_and_inline_forms():
    assert _nested_string_list(
        "plugins:\n  enabled:\n    - petdex\n    - spotify\nother: 1\n",
        "plugins",
        "enabled",
    ) == ["petdex", "spotify"]
    assert _nested_string_list(
        'plugins:\n  enabled: ["petdex", "spotify"]\n', "plugins", "enabled"
    ) == ["petdex", "spotify"]


@pytest.mark.parametrize(
    "text",
    [
        "approvals_yolo: true\n",
        "approvals:\n  mode: off\n",
        "hooks_auto_accept: yes\n",
    ],
)
def test_approval_bypass_sources_are_rejected(text):
    assert source_defines_yolo(text)


def test_failed_profile_tightening_restores_original_config(monkeypatch, tmp_path):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    home.mkdir()
    source = home / "config.yaml"
    original = (
        "_config_version: 33\n"
        "model:\n  provider: openrouter\n  default: example/model\n"
        "plugins:\n  enabled:\n    - petdex\n"
        "approvals:\n  mode: manual\n"
    )
    source.write_text(original)
    cfg = load_fleet_config(model="unused", state_dir=str(tmp_path / "state"))
    cfg.name = "rollback"
    cfg.profile_slots = {"coder": 1}
    seen: list[tuple[str, ...]] = []

    def fake_runner(*args: str):
        seen.append(tuple(args))
        profile = "hca-rollback-coder-01"
        dst = home / "profiles" / profile
        if args[:2] == ("profile", "create"):
            dst.mkdir(parents=True)
            (dst / "config.yaml").write_text(original)
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:4] == ("-p", profile, "config", "set"):
            with (dst / "config.yaml").open("a") as fh:
                fh.write(f"# touched {args[4]}\n")
            if args[4] == "delegation.max_concurrent_children":
                return subprocess.CompletedProcess(args, 7, "", "simulated failure")
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    with pytest.raises(ProfileDerivationError, match="simulated failure"):
        init_profiles(cfg, runner=fake_runner)

    target = home / "profiles" / "hca-rollback-coder-01" / "config.yaml"
    assert target.read_text() == original
    backups = list(target.parent.glob("config.yaml.hca-bak.*"))
    assert len(backups) == 1
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    # No credential or model value is passed through command arguments.
    flattened = " ".join(" ".join(call) for call in seen)
    assert "example/model" not in flattened
