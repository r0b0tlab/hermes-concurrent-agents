"""Generic NVIDIA CUDA adapter.

Detection is filesystem/PATH-based (no import of CUDA/NVML at module load).
GPU utilization is read via NVML *only if* pynvml is importable and a device
is present; otherwise accel telemetry stays unknown and admission falls back
to conservative host-only signals.
"""

from __future__ import annotations

import os
import shutil
from typing import Optional

from hca.devices.base import DeviceAdapter, DeviceSignals


def _has_nvidia() -> bool:
    if shutil.which("nvidia-smi"):
        return True
    if os.path.exists("/proc/driver/nvidia/version"):
        return True
    if os.path.exists("/dev/nvidia0"):
        return True
    return False


class NvidiaAdapter(DeviceAdapter):
    name = "nvidia"
    optimized = True

    @classmethod
    def detect(cls) -> bool:
        return _has_nvidia()

    def probe(self, disk_path: Optional[str] = None) -> DeviceSignals:
        sig = super().probe(disk_path)
        self._read_nvml(sig)
        sig.detail = self._detail(sig)
        return sig

    def _read_nvml(self, sig: DeviceSignals) -> None:
        # Import lazily and defensively — never a hard dependency.
        try:
            import pynvml  # type: ignore
        except Exception:
            return
        try:
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            if count <= 0:
                return
            utils = []
            mem_press = []
            for i in range(count):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                u = pynvml.nvmlDeviceGetUtilizationRates(h)
                m = pynvml.nvmlDeviceGetMemoryInfo(h)
                utils.append(u.gpu / 100.0)
                if m.total:
                    mem_press.append(m.used / m.total)
            if utils:
                sig.accel_util = round(sum(utils) / len(utils), 4)
            if mem_press:
                sig.accel_mem_pressure = round(sum(mem_press) / len(mem_press), 4)
            sig.vendor["nvidia"] = {"device_count": count}
        except Exception:
            return
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
