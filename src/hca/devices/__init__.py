"""Device adapter selection (capability-driven, with explicit override).

Priority: gb10 → generic NVIDIA → Apple Silicon → generic host. The generic
adapter is always available, so selection never fails. Operators may override
with ``HCA_DEVICE_ADAPTER`` (or the ``override`` arg); ``hca doctor --json``
reports which adapter was selected and why.
"""

from __future__ import annotations

import os
from typing import Optional

from hca.devices.base import DeviceAdapter, DeviceSignals
from hca.devices.gb10 import GB10Adapter
from hca.devices.generic import GenericAdapter
from hca.devices.macos import MacosAdapter
from hca.devices.nvidia import NvidiaAdapter

# Highest-priority first. Generic is always last and always detects.
_PRIORITY: list[type[DeviceAdapter]] = [
    GB10Adapter,
    NvidiaAdapter,
    MacosAdapter,
    GenericAdapter,
]

_BY_NAME = {c.name: c for c in _PRIORITY}


def select_adapter(override: str = "") -> tuple[DeviceAdapter, str]:
    """Return ``(adapter, reason)``.

    An explicit override that names an unknown adapter falls back to generic
    with a reason rather than raising — admission must never be blocked by a
    bad adapter name.
    """
    override = override or os.environ.get("HCA_DEVICE_ADAPTER", "")
    if override:
        cls = _BY_NAME.get(override)
        if cls is not None:
            return cls(), f"override: {override}"
        return GenericAdapter(), f"override {override!r} unknown; using generic"
    for cls in _PRIORITY:
        try:
            if cls.detect():
                kind = "optimized" if cls.optimized else "fallback"
                return cls(), f"detected {cls.name} ({kind})"
        except Exception:
            continue
    return GenericAdapter(), "default generic"


def probe_device(
    override: str = "", disk_path: Optional[str] = None
) -> tuple[DeviceSignals, str]:
    adapter, reason = select_adapter(override)
    return adapter.probe(disk_path), reason


__all__ = [
    "DeviceAdapter",
    "DeviceSignals",
    "GB10Adapter",
    "GenericAdapter",
    "MacosAdapter",
    "NvidiaAdapter",
    "select_adapter",
    "probe_device",
]
