"""
In-memory ring buffer for live sensor snapshots.

The MQTT ingest pipeline pushes samples here at ~123 Hz; the SSE endpoint
reads the tail to stream data to browser clients.

Concurrency model:
    * paho's background thread puts raw data on an asyncio.Queue using
      ``loop.call_soon_threadsafe``.
    * A single asyncio consumer task drains that queue and calls
      ``LiveDataHub.push()`` — all writes happen on the event-loop thread.
    * SSE reader coroutines call ``LiveDataHub.since()`` — also on the
      event-loop thread.
    * No locks required: single-threaded asyncio access for all mutations.

The ``maxlen`` default matches ``LIVE_BUFFER_MAX_SAMPLES`` from the legacy
system (2000 samples ≈ 16 seconds at 123 Hz).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class SensorSnapshot:
    """One timestamped reading for all sensors of a single device."""

    ts: float
    values: dict[int, float] = field(default_factory=dict)


class LiveDataHub:
    """
    Per-device ring buffers of recent sensor snapshots.

    Buffers are created lazily on the first push for a device. maxlen is
    enforced by ``collections.deque`` — O(1) push and automatic eviction
    of the oldest sample.
    """

    def __init__(self, maxlen: int = 2000) -> None:
        self._maxlen = maxlen
        # device_id (int) → deque of SensorSnapshot
        self._buffers: dict[int, deque[SensorSnapshot]] = {}

    def push(self, device_id: int, ts: float, values: dict[int, float]) -> None:
        """
        Append a snapshot to the ring buffer for ``device_id``.

        Must be called from the asyncio event-loop thread only.
        """
        if device_id not in self._buffers:
            self._buffers[device_id] = deque(maxlen=self._maxlen)
        self._buffers[device_id].append(SensorSnapshot(ts=ts, values=values))

    def since(self, device_id: int, after_ts: float | None = None) -> list[SensorSnapshot]:
        """
        Return snapshots for ``device_id`` newer than ``after_ts``.

        ``after_ts=None`` returns all buffered snapshots (up to ``maxlen``).
        Must be called from the asyncio event-loop thread only.
        """
        buf = self._buffers.get(device_id)
        if not buf:
            return []
        if after_ts is None:
            return list(buf)
        return [s for s in buf if s.ts > after_ts]

    def latest_ts(self, device_id: int) -> float | None:
        """Timestamp of the most recent snapshot, or None if no data yet."""
        buf = self._buffers.get(device_id)
        if not buf:
            return None
        return buf[-1].ts

    def devices(self) -> list[int]:
        """IDs of all devices with at least one buffered snapshot."""
        return list(self._buffers.keys())
