"""hca init writes fleet.resolved.json; bare commands must reload that fleet."""

import json
from pathlib import Path

from hca.config import config_shape, load_fleet_config
from hca.models import Engine, FleetRole


def test_resolved_snapshot_roundtrip(tmp_path: Path):
    cfg = load_fleet_config(preset="gb10-sglang", model="my-model", state_dir=str(tmp_path))
    snap = Path(cfg.state_dir) / "fleet.resolved.json"
    snap.write_text(json.dumps(config_shape(cfg)), encoding="utf-8")

    # No preset/config on the second invocation — the snapshot must win.
    cfg2 = load_fleet_config(state_dir=str(tmp_path))
    assert cfg2.name == cfg.name == "gb10"
    assert cfg2.tmux_socket == cfg.tmux_socket == "hca-gb10"
    assert cfg2.backend.model == "my-model"
    assert cfg2.backend.engine == Engine.SGLANG
    assert "30000" in cfg2.backend.endpoint
    assert cfg2.preset == "gb10-sglang"
    assert cfg2.role == FleetRole.SINGLE
    assert cfg2.profile_slots == cfg.profile_slots


def test_cli_flags_still_override_snapshot(tmp_path: Path):
    cfg = load_fleet_config(preset="gb10-vllm", model="m1", state_dir=str(tmp_path))
    snap = Path(cfg.state_dir) / "fleet.resolved.json"
    snap.write_text(json.dumps(config_shape(cfg)), encoding="utf-8")

    cfg2 = load_fleet_config(state_dir=str(tmp_path), model="m2", board="other")
    assert cfg2.backend.model == "m2"
    assert cfg2.board == "other"
    assert cfg2.name == "gb10"
