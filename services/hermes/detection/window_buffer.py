"""
Per-sensor ring buffer of (ts, value) pairs for event-window capture.

When an event fires, the DB sink captures a ±N s window around the
trigger timestamp. The legacy default is ±9 s; debounce can push the
fire time well after the trigger, so the buffer must hold at least
``pre_seconds + max_debounce + headroom`` worth of samples for every
sensor on every device.

Used by ``DbEventSink`` only — ``LiveDataHub`` is a separate, shorter
buffer used by the SSE feed. Keeping them distinct lets us tune their
sizes independently (live = ~16 s for charts, detection = ~30 s for
event windows).

This is a single-threaded, asyncio-only data structure: writes come
from the ingest consumer task, reads come from the DbEventSink writer
task on the same event loop.
"""

from __future__ import annotations

from collections import deque

# Default buffer span — generous enough to cover ±9 s pre/post plus
# debounce delays up to ~12 s. Adjustable per IngestPipeline.
DEFAULT_BUFFER_SECONDS: float = 30.0
DEFAULT_EXPECTED_RATE_HZ: float = 123.0


class EventWindowBuffer:
    """Lazily-allocated per-(device, sensor) deque of recent samples."""

    __slots__ = ("_buffers", "_maxlen")

    def __init__(
        self,
        buffer_seconds: float = DEFAULT_BUFFER_SECONDS,
        expected_rate_hz: float = DEFAULT_EXPECTED_RATE_HZ,
    ) -> None:
        # 1.5× headroom over expected sample count so a hot link
        # doesn't truncate the head.
        self._maxlen = max(2, int(buffer_seconds * expected_rate_hz * 1.5))
        self._buffers: dict[tuple[int, int], deque[tuple[float, float]]] = {}

    def push_snapshot(self, device_id: int, ts: float, values: dict[int, float]) -> None:
        """Append the same timestamp's reading for every sensor in ``values``."""
        for sensor_id, value in values.items():
            key = (device_id, sensor_id)
            buf = self._buffers.get(key)
            if buf is None:
                buf = deque(maxlen=self._maxlen)
                self._buffers[key] = buf
            buf.append((ts, value))

    def slice(
        self, device_id: int, sensor_id: int, start_ts: float, end_ts: float
    ) -> list[tuple[float, float]]:
        """
        Return all samples for ``(device_id, sensor_id)`` with
        ``start_ts <= ts <= end_ts``. Linear scan over the deque —
        windows are short (~few hundred samples) so this is cheap.
        """
        buf = self._buffers.get((device_id, sensor_id))
        if not buf:
            return []
        return [(ts, v) for ts, v in buf if start_ts <= ts <= end_ts]

    def clear_device(self, device_id: int) -> None:
        """Drop all sensor buffers for ``device_id`` (used on config reload)."""
        to_drop = [key for key in self._buffers if key[0] == device_id]
        for key in to_drop:
            del self._buffers[key]
