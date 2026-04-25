"""
Per-sensor raw-value offset correction.

Hardware sensors have individual zero-point biases. The operator calibrates
each sensor by recording a known reference value and storing the difference
as an offset in the ``sensor_offsets`` database table.

Correction formula (preserved from legacy ``web_server.py``):
    corrected = raw - offset

An offset of 0.0 is a no-op. Devices with no non-zero offsets are stored
without an entry in the cache to keep the hot path allocation-free.

This module is pure-Python with no I/O — tests can exercise it directly.
"""

from __future__ import annotations


class OffsetCache:
    """
    In-memory cache of per-sensor offsets for all active devices.

    Load offsets at startup from DB, then refresh per-device on config
    change (``load()`` replaces the device entry atomically).
    """

    def __init__(self) -> None:
        # device_id (int) → {sensor_id (int) → offset (float)}
        # Devices with all-zero offsets are absent from the dict.
        self._cache: dict[int, dict[int, float]] = {}

    def load(self, device_id: int, offsets: dict[int, float]) -> None:
        """
        Replace the offset table for ``device_id``.

        ``offsets`` maps sensor_id (1-12) → offset value. Entries that are
        exactly 0.0 are dropped to keep the hot-path branch-free.
        """
        non_zero = {sid: v for sid, v in offsets.items() if v != 0.0}
        if non_zero:
            self._cache[device_id] = non_zero
        else:
            self._cache.pop(device_id, None)

    def apply(self, device_id: int, sensor_values: dict[int, float]) -> dict[int, float]:
        """
        Return ``{sensor_id: corrected_value}`` after subtracting stored offsets.

        Returns the original dict unchanged when no offsets are configured
        for ``device_id`` — avoids an allocation on the 123 Hz hot path.
        """
        device_offsets = self._cache.get(device_id)
        if not device_offsets:
            return sensor_values
        return {sid: val - device_offsets.get(sid, 0.0) for sid, val in sensor_values.items()}

    def has_offsets(self, device_id: int) -> bool:
        """True if any non-zero offsets are configured for ``device_id``."""
        return device_id in self._cache
