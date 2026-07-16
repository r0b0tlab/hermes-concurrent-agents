"""Generic host adapter — always available, zero vendor imports."""

from __future__ import annotations

from hca.devices.base import DeviceAdapter


class GenericAdapter(DeviceAdapter):
    name = "generic"
    optimized = False

    @classmethod
    def detect(cls) -> bool:
        # Always available: the portable fallback.
        return True
