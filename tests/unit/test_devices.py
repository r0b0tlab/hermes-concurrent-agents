"""Device adapter selection + normalized signals (portable, vendor-free)."""

from __future__ import annotations

import sys

import pytest

from hca.devices import (
    GenericAdapter,
    probe_device,
    select_adapter,
)
from hca.devices.base import DeviceSignals
from hca.devices.macos import MacosAdapter
from hca.devices.nvidia import NvidiaAdapter


def test_generic_always_detects():
    assert GenericAdapter.detect() is True


def test_selection_priority_and_override(monkeypatch):
    monkeypatch.delenv("HCA_DEVICE_ADAPTER", raising=False)
    monkeypatch.setenv("HCA_FORCE_GB10", "0")  # not GB10 for this test
    adapter, reason = select_adapter()
    # on a non-GB10 host this is nvidia (if present), macos, or generic
    assert adapter.name in {"nvidia", "macos", "generic"}
    assert reason

    # explicit override honored
    a2, r2 = select_adapter("generic")
    assert a2.name == "generic" and "override" in r2

    # unknown override → generic (never raises / blocks admission)
    a3, r3 = select_adapter("teleporter")
    assert a3.name == "generic" and "unknown" in r3


def test_override_via_env(monkeypatch):
    monkeypatch.setenv("HCA_DEVICE_ADAPTER", "macos")
    adapter, _ = select_adapter()
    assert adapter.name == "macos"


def test_generic_probe_reads_host_signals(tmp_path):
    sig = GenericAdapter().probe(disk_path=str(tmp_path))
    assert isinstance(sig, DeviceSignals)
    # disk of a real path is always known
    assert sig.disk_total_mb and sig.disk_total_mb > 0
    assert sig.disk_pressure is not None
    # accelerator is unknown on the generic adapter (conservative, not zero)
    assert sig.accel_util is None
    assert sig.accel_known is False


@pytest.mark.skipif(sys.platform != "linux", reason="linux meminfo")
def test_linux_mem_pressure_known():
    sig = GenericAdapter().probe()
    assert sig.host_mem_total_mb and sig.host_mem_total_mb > 0
    assert sig.mem_pressure_known
    assert 0.0 <= sig.mem_pressure <= 1.0


def test_gb10_detection_forced(monkeypatch):
    monkeypatch.setenv("HCA_FORCE_GB10", "1")
    monkeypatch.delenv("HCA_DEVICE_ADAPTER", raising=False)
    adapter, reason = select_adapter()
    assert adapter.name == "gb10"
    # UMA marker present after probe
    sig = adapter.probe()
    assert sig.vendor.get("gb10", {}).get("uma") is True


def test_macos_adapter_not_detected_on_linux(monkeypatch):
    if sys.platform == "linux":
        assert MacosAdapter.detect() is False


def test_nvidia_detect_is_boolean():
    assert isinstance(NvidiaAdapter.detect(), bool)


def test_probe_device_returns_reason():
    sig, reason = probe_device("generic")
    assert sig.adapter == "generic"
    assert reason


def test_generic_and_base_import_no_vendor_libs():
    """The generic/base adapters must never import CUDA/NVML/ROCm."""
    import inspect

    import hca.devices.base as base
    import hca.devices.generic as generic

    for mod in (base, generic):
        src = inspect.getsource(mod).lower()
        for banned in ("pynvml", "import cuda", "pyrocm", "import nvml", "cupy"):
            assert banned not in src, f"{mod.__name__} references {banned}"
