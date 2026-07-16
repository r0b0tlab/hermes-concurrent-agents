"""Device adapter surface: normalized host/accelerator signals.

Admission consumes *normalized* signals with an explicit known/unknown state.
Unknown accelerator/KV telemetry must invoke conservative configured caps — it
is never interpreted as zero pressure or infinite capacity. The generic
adapter is always available and imports no vendor libraries (CUDA/NVML/ROCm);
optimized adapters may probe vendor metrics but must degrade to generic
behavior when those libraries or devices are absent.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class DeviceSignals:
    adapter: str
    # host memory
    host_mem_total_mb: Optional[int] = None
    host_mem_available_mb: Optional[int] = None
    mem_pressure: Optional[float] = None  # 0..1 (1 = full); None = unknown
    # swap
    swap_total_mb: Optional[int] = None
    swap_used_mb: Optional[int] = None
    swap_active: Optional[bool] = None
    # disk (of the state/work path)
    disk_total_mb: Optional[int] = None
    disk_free_mb: Optional[int] = None
    disk_pressure: Optional[float] = None
    # accelerator (None = unknown → conservative)
    accel_util: Optional[float] = None
    accel_mem_pressure: Optional[float] = None
    # namespaced optional vendor metrics; unknown fields are absent, never faked
    vendor: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def mem_pressure_known(self) -> bool:
        return self.mem_pressure is not None

    @property
    def accel_known(self) -> bool:
        return self.accel_util is not None or self.accel_mem_pressure is not None


class DeviceAdapter:
    """Base adapter: generic, vendor-free host telemetry."""

    name = "base"
    optimized = False

    @classmethod
    def detect(cls) -> bool:  # pragma: no cover - overridden
        return False

    def probe(self, disk_path: Optional[str] = None) -> DeviceSignals:
        sig = DeviceSignals(adapter=self.name)
        self._read_meminfo(sig)
        self._read_disk(sig, disk_path)
        sig.detail = self._detail(sig)
        return sig

    # --- host telemetry (no vendor imports) ---

    def _read_meminfo(self, sig: DeviceSignals) -> None:
        path = "/proc/meminfo"
        if not os.path.exists(path):
            return
        info: dict[str, int] = {}
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) != 2:
                        continue
                    key = parts[0].strip()
                    val = parts[1].strip().split()
                    if val and val[0].isdigit():
                        info[key] = int(val[0])  # kB
        except OSError:
            return
        if "MemTotal" in info:
            sig.host_mem_total_mb = info["MemTotal"] // 1024
        avail = info.get("MemAvailable")
        if avail is not None:
            sig.host_mem_available_mb = avail // 1024
        if sig.host_mem_total_mb and sig.host_mem_available_mb is not None and sig.host_mem_total_mb > 0:
            used = sig.host_mem_total_mb - sig.host_mem_available_mb
            sig.mem_pressure = round(max(0.0, min(1.0, used / sig.host_mem_total_mb)), 4)
        if "SwapTotal" in info:
            sig.swap_total_mb = info["SwapTotal"] // 1024
            free = info.get("SwapFree", info["SwapTotal"])
            sig.swap_used_mb = (info["SwapTotal"] - free) // 1024
            sig.swap_active = sig.swap_used_mb > 0

    def _read_disk(self, sig: DeviceSignals, disk_path: Optional[str]) -> None:
        path = disk_path or os.path.expanduser("~")
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            return
        sig.disk_total_mb = usage.total // (1024 * 1024)
        sig.disk_free_mb = usage.free // (1024 * 1024)
        if usage.total > 0:
            sig.disk_pressure = round((usage.total - usage.free) / usage.total, 4)

    def _detail(self, sig: DeviceSignals) -> str:
        parts = [self.name]
        if sig.mem_pressure is not None:
            parts.append(f"mem={sig.mem_pressure:.0%}")
        else:
            parts.append("mem=unknown")
        if sig.accel_util is not None:
            parts.append(f"accel={sig.accel_util:.0%}")
        else:
            parts.append("accel=unknown")
        return " ".join(parts)
