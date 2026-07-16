"""GB10 / DGX Spark adapter (unified memory architecture).

On GB10 the CPU and GPU share one physical memory pool (UMA), so host
MemAvailable is the load-bearing admission signal and there is no separate
discrete-VRAM pressure to track. Detection reads the device-tree/DMI model
strings; NVML (if present) enriches accelerator utilization. No vendor
library is imported at module load.
"""

from __future__ import annotations

import os
from typing import Optional

from hca.devices.nvidia import NvidiaAdapter
from hca.devices.base import DeviceSignals

_GB10_MARKERS = ("gb10", "dgx spark", "nvidia gb10", "spark")


def _model_strings() -> list[str]:
    out: list[str] = []
    for path in (
        "/proc/device-tree/model",
        "/sys/devices/virtual/dmi/id/product_name",
        "/sys/devices/virtual/dmi/id/board_name",
    ):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                out.append(f.read().strip().lower().replace("\x00", ""))
        except OSError:
            continue
    return out


def _is_gb10() -> bool:
    env = os.environ.get("HCA_FORCE_GB10")
    if env == "1":
        return True
    if env == "0":
        return False
    models = _model_strings()
    return any(any(m in s for m in _GB10_MARKERS) for s in models)


class GB10Adapter(NvidiaAdapter):
    name = "gb10"
    optimized = True

    @classmethod
    def detect(cls) -> bool:
        return _is_gb10()

    def probe(self, disk_path: Optional[str] = None) -> DeviceSignals:
        sig = super().probe(disk_path)
        # UMA: unified pool. Mirror host memory pressure into the accelerator
        # memory-pressure slot when NVML did not supply a discrete reading,
        # so admission has *some* known accelerator-side pressure signal.
        if sig.accel_mem_pressure is None and sig.mem_pressure is not None:
            sig.accel_mem_pressure = sig.mem_pressure
            sig.vendor.setdefault("gb10", {})["uma"] = True
        else:
            sig.vendor.setdefault("gb10", {})["uma"] = True
        sig.detail = self._detail(sig) + " uma"
        return sig
