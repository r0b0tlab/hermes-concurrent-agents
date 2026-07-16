"""Apple Silicon / macOS adapter.

macOS has no /proc/meminfo, so host memory comes from ``sysctl`` +
``vm_stat`` (no vendor libraries). Accelerator telemetry stays unknown
(conservative) — Metal utilization is not read here.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from typing import Optional

from hca.devices.base import DeviceAdapter, DeviceSignals


class MacosAdapter(DeviceAdapter):
    name = "macos"
    optimized = True

    @classmethod
    def detect(cls) -> bool:
        return platform.system() == "Darwin"

    def probe(self, disk_path: Optional[str] = None) -> DeviceSignals:
        sig = DeviceSignals(adapter=self.name)
        self._read_sysctl_mem(sig)
        self._read_disk(sig, disk_path)
        sig.detail = self._detail(sig)
        return sig

    def _read_sysctl_mem(self, sig: DeviceSignals) -> None:
        total = self._sysctl_int("hw.memsize")
        if total:
            sig.host_mem_total_mb = total // (1024 * 1024)
        page_size = self._sysctl_int("hw.pagesize") or 4096
        free_pages = self._vm_stat_free_pages()
        if free_pages is not None and sig.host_mem_total_mb:
            avail_mb = (free_pages * page_size) // (1024 * 1024)
            sig.host_mem_available_mb = avail_mb
            used = sig.host_mem_total_mb - avail_mb
            if sig.host_mem_total_mb > 0:
                sig.mem_pressure = round(max(0.0, min(1.0, used / sig.host_mem_total_mb)), 4)

    @staticmethod
    def _sysctl_int(key: str) -> Optional[int]:
        if not shutil.which("sysctl"):
            return None
        try:
            out = subprocess.run(
                ["sysctl", "-n", key], capture_output=True, text=True, timeout=5
            )
            return int(out.stdout.strip())
        except Exception:
            return None

    @staticmethod
    def _vm_stat_free_pages() -> Optional[int]:
        if not shutil.which("vm_stat"):
            return None
        try:
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
        except Exception:
            return None
        free = 0
        found = False
        for line in out.stdout.splitlines():
            m = re.match(r"Pages (free|inactive|speculative):\s+(\d+)", line)
            if m:
                free += int(m.group(2))
                found = True
        return free if found else None
