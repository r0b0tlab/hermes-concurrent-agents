from hca.config import list_presets, load_fleet_config
from hca.models import Engine, FleetRole


def test_list_presets_includes_gb10():
    presets = list_presets()
    assert "gb10-vllm" in presets
    assert "gb10-sglang" in presets
    assert "gb10-cluster-vllm" in presets


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


def test_cluster_preset_role_control():
    cfg = load_fleet_config(preset="gb10-cluster-vllm")
    assert cfg.role == FleetRole.CONTROL
    assert cfg.cluster.require_same_username is True
    assert cfg.cluster.transport.value == "ssh"


def test_engine_override_flips_default_port():
    cfg = load_fleet_config(preset="gb10-vllm", engine="sglang")
    assert cfg.backend.engine == Engine.SGLANG
    assert "30000" in cfg.backend.endpoint
