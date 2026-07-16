"""hca init writes fleet.resolved.json; bare commands must reload that fleet."""

import json
import os
from pathlib import Path

from hca.config import (
    config_shape,
    load_fleet_config,
    persisted_config_shape,
    write_resolved_snapshot,
)
from hca.models import ClusterNode, Engine, FleetRole


def test_resolved_snapshot_roundtrip(tmp_path: Path):
    cfg = load_fleet_config(preset="gb10-sglang", model="my-model", state_dir=str(tmp_path))
    write_resolved_snapshot(cfg)

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
    write_resolved_snapshot(cfg)

    cfg2 = load_fleet_config(state_dir=str(tmp_path), model="m2", board="other")
    assert cfg2.backend.model == "m2"
    assert cfg2.board == "other"
    assert cfg2.name == "gb10"


def test_snapshot_omits_connection_strings_and_requires_custom_endpoint_at_runtime(
    monkeypatch, tmp_path: Path
):
    cfg = load_fleet_config(preset="generic-linux", model="m", state_dir=str(tmp_path))
    cfg.backend.endpoint = "https://operator:secret@example.invalid/v1"
    cfg.backend.metrics_url = "https://token@example.invalid/metrics"
    cfg.backend.auxiliary_endpoint = "https://aux.example.invalid/v1"
    cfg.cluster.nodes = [
        ClusterNode(host="private.example", ssh_user="alice", endpoint="http://private/v1")
    ]

    shaped = persisted_config_shape(cfg)
    rendered = json.dumps(shaped)
    assert "secret" not in rendered
    assert "example.invalid" not in rendered
    assert "private.example" not in rendered
    assert "endpoint" not in shaped["backend"]
    assert "cluster" not in shaped
    assert shaped["runtime"]["endpoint_required"] is True

    snap = write_resolved_snapshot(cfg)
    assert os.stat(snap).st_mode & 0o777 == 0o600
    reloaded = load_fleet_config(state_dir=str(tmp_path))
    assert reloaded.backend.endpoint == ""
    assert reloaded.backend.metrics_url == ""

    monkeypatch.setenv("HCA_BACKEND_ENDPOINT", "https://runtime.example.invalid/v1")
    monkeypatch.setenv("HCA_BACKEND_METRICS_URL", "https://runtime.example.invalid/metrics")
    from_env = load_fleet_config(state_dir=str(tmp_path))
    assert from_env.backend.endpoint == "https://runtime.example.invalid/v1"
    assert from_env.backend.metrics_url == "https://runtime.example.invalid/metrics"


def test_legacy_snapshot_is_sanitized_after_one_compatible_read(tmp_path: Path):
    cfg = load_fleet_config(preset="generic-linux", model="m", state_dir=str(tmp_path))
    cfg.backend.endpoint = "https://operator:secret@legacy.example.invalid/v1"
    cfg.backend.metrics_url = "https://legacy.example.invalid/metrics"
    cfg.cluster.nodes = [ClusterNode(host="legacy-private.example", ssh_user="alice")]
    snap = Path(cfg.state_dir) / "fleet.resolved.json"
    snap.write_text(json.dumps(config_shape(cfg)), encoding="utf-8")

    loaded = load_fleet_config(state_dir=str(tmp_path))
    assert loaded.backend.endpoint == "https://operator:secret@legacy.example.invalid/v1"

    sanitized = snap.read_text(encoding="utf-8")
    assert "secret" not in sanitized
    assert "legacy.example.invalid" not in sanitized
    assert "legacy-private.example" not in sanitized
    assert os.stat(snap).st_mode & 0o777 == 0o600
    payload = json.loads(sanitized)
    assert payload["runtime"]["endpoint_required"] is True
