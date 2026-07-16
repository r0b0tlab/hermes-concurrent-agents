import pytest

from hca.config import list_presets, load_fleet_config
from hca.models import Engine, FleetRole


def test_list_presets_includes_single_node_gb10_only():
    presets = list_presets()
    assert "gb10-vllm" in presets
    assert "gb10-sglang" in presets
    assert all("cluster" not in preset for preset in presets)


def test_load_gb10_vllm_preset():
    cfg = load_fleet_config(preset="gb10-vllm", model="test-model")
    assert cfg.backend.engine == Engine.VLLM
    assert "8000" in cfg.backend.endpoint
    assert cfg.backend.model == "test-model"
    assert cfg.role == FleetRole.SINGLE
    assert cfg.profile_slots["coder"] == 2


def test_load_gb10_sglang_preset():
    cfg = load_fleet_config(preset="gb10-sglang")
    assert cfg.backend.engine == Engine.SGLANG
    assert "30000" in cfg.backend.endpoint


def test_cluster_preset_is_not_a_stable_product_preset():
    with pytest.raises(FileNotFoundError, match="preset not found"):
        load_fleet_config(preset="gb10-cluster-vllm")


def test_engine_override_flips_default_port():
    cfg = load_fleet_config(preset="gb10-vllm", engine="sglang")
    assert cfg.backend.engine == Engine.SGLANG
    assert "30000" in cfg.backend.endpoint
