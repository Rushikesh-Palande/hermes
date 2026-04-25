"""
Per-device STM32 wall-clock anchoring.

The STM32 reports timestamps in milliseconds since its own boot (or since
an arbitrary counter start). We anchor these to Unix wall time using:

    offset = server_receive_time - device_time_sec   (initialised once)
    wall_ts  = device_time_sec + offset

When the STM32 resets or its counter wraps, the computed wall_ts will
diverge from server_receive_time by more than drift_threshold_s. We
detect this and immediately re-anchor, preventing the live graph from
jumping to the distant past or future.

This module is pure-Python with no I/O — the full anchoring logic lives
here and is exercised by unit tests without any infrastructure.
"""

from __future__ import annotations

# Default re-anchor threshold (seconds). Matches the legacy constant.
DRIFT_THRESHOLD_S: float = 5.0


class DeviceClock:
    """
    Single-device anchor: converts device-side ms timestamps to wall time.

    Not thread-safe by itself — the ingest pipeline processes one device's
    messages sequentially on the asyncio consumer task, so no locking is
    needed in practice.
    """

    __slots__ = ("_offset", "_threshold")

    def __init__(self, drift_threshold_s: float = DRIFT_THRESHOLD_S) -> None:
        self._offset: float | None = None
        self._threshold = drift_threshold_s

    def anchor(self, receive_ts: float, dev_ts_sec: float) -> float:
        """
        Return a wall-clock timestamp for this sample.

        ``receive_ts``  — ``time.time()`` captured when the MQTT callback fired.
        ``dev_ts_sec``  — device timestamp converted from milliseconds to seconds.
        """
        if self._offset is None:
            self._offset = receive_ts - dev_ts_sec

        ts = dev_ts_sec + self._offset

        # Guard against STM32 counter reset or wrap: re-anchor immediately.
        if abs(ts - receive_ts) > self._threshold:
            self._offset = receive_ts - dev_ts_sec
            ts = receive_ts

        return ts

    @property
    def offset(self) -> float | None:
        """Current anchor offset (seconds); None before the first sample."""
        return self._offset


class ClockRegistry:
    """
    Registry of per-device DeviceClock instances.

    Lives for the lifetime of the ingest process; devices are registered
    lazily on first message arrival.
    """

    def __init__(self, drift_threshold_s: float = DRIFT_THRESHOLD_S) -> None:
        self._drift_threshold = drift_threshold_s
        self._clocks: dict[int, DeviceClock] = {}

    def anchor(self, device_id: int, receive_ts: float, dev_ts_sec: float) -> float:
        """Anchor ``dev_ts_sec`` to wall time for ``device_id``."""
        if device_id not in self._clocks:
            self._clocks[device_id] = DeviceClock(self._drift_threshold)
        return self._clocks[device_id].anchor(receive_ts, dev_ts_sec)

    def offset_for(self, device_id: int) -> float | None:
        """Current anchor offset for ``device_id``, or None if not yet seen."""
        clock = self._clocks.get(device_id)
        return clock.offset if clock is not None else None
