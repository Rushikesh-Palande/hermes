"""
TTL gate — dedup + priority rules between detector fires and durable sinks.

Without this, a sustained out-of-band signal triggers an event on EVERY
sample — the event log fills with hundreds of duplicates per second
and the operator can't see anything useful.

The legacy system (EVENT_DETECTION_CONTRACT §8) holds each fired event
for ``ttl_seconds`` (default 5 s) and applies four rules:

    Rule 1 — Block lower priority.
        If a higher-priority event-type is already armed for the same
        (device, sensor), drop the new event.
    Rule 2 — Preempt lower priority.
        A new higher-priority event clears any armed lower-priority
        timers for the same (device, sensor) — they re-arm cleanly
        after the higher one resolves.
    Rule 3 — Merge same type.
        If the same event-type is already armed, swallow the duplicate.
    Rule 4 — Arm.
        Record ``(triggered_at, ttl)`` and forward NOTHING yet.

When ``ts - armed_at >= ttl_seconds`` for an armed timer, the held
event is forwarded to the child sink. From there the existing 9-second
post-window fence on ``DbEventSink`` adds the second phase
(triggered_at + post_window).

Priority order matches the legacy contract:

    A (1) < B (2) < C (3) < D (4)

BREAK events bypass all rules — they're an exceptional condition
(wire break / sensor disconnect) and need to be visible immediately.

Time advances on every ``publish`` call, using the incoming event's
``triggered_at`` as the clock. No background timer task: the legacy
behaviour was clock-driven by ``add_sensor_data`` and our hot path
fires events frequently enough that armed timers never languish.
"""

from __future__ import annotations

from dataclasses import dataclass

from hermes.db.models import EventType
from hermes.detection.types import DetectedEvent, EventSink
from hermes.logging import get_logger

_log = get_logger(__name__, component="detection")

DEFAULT_TTL_SECONDS: float = 5.0

# Higher number wins. BREAK is intentionally outside this scale —
# bypassed entirely. Same numbering as the legacy contract.
_PRIORITY: dict[EventType, int] = {
    EventType.A: 1,
    EventType.B: 2,
    EventType.C: 3,
    EventType.D: 4,
}


@dataclass(slots=True)
class _ArmedTimer:
    """One in-flight TTL timer holding an event."""

    triggered_at: float
    armed_at: float
    ttl_seconds: float
    event: DetectedEvent


class TtlGateSink:
    """
    Sink-wrapping decorator that dedupes + prioritises events before
    forwarding them to ``child``. Single-threaded; called only from the
    detection engine's path.

    Configure ``ttl_seconds`` per-deployment via the
    ``hermes.config.Settings.event_ttl_seconds`` setting. ``flush()``
    forces every armed timer to forward immediately and is called from
    the pipeline shutdown path.
    """

    __slots__ = ("_child", "_ttl", "_timers")

    def __init__(self, child: EventSink, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        self._child = child
        self._ttl = ttl_seconds
        # Keyed by (device_id, sensor_id, event_type).
        self._timers: dict[tuple[int, int, EventType], _ArmedTimer] = {}

    # ─── EventSink protocol ───────────────────────────────────────

    def publish(self, event: DetectedEvent) -> None:
        # BREAK is a system-level alarm — never gated.
        if event.event_type is EventType.BREAK:
            self._child.publish(event)
            return

        # Advance the clock and forward any timers that have aged out
        # before deciding what to do with this fresh event.
        self._expire_due(event.triggered_at)

        key = (event.device_id, event.sensor_id, event.event_type)
        sensor_key = (event.device_id, event.sensor_id)
        new_priority = _PRIORITY[event.event_type]

        # Rule 1: higher-priority armed for this sensor → drop.
        for armed_key in self._timers:
            if armed_key[:2] != sensor_key:
                continue
            if _PRIORITY[armed_key[2]] > new_priority:
                return

        # Rule 3: same type already armed → swallow.
        if key in self._timers:
            return

        # Rule 2: preempt any lower-priority armed timers for this sensor.
        for armed_key in list(self._timers):
            if armed_key[:2] != sensor_key:
                continue
            if _PRIORITY[armed_key[2]] < new_priority:
                del self._timers[armed_key]

        # Rule 4: arm.
        self._timers[key] = _ArmedTimer(
            triggered_at=event.triggered_at,
            armed_at=event.triggered_at,
            ttl_seconds=self._ttl,
            event=event,
        )

    # ─── Lifecycle ────────────────────────────────────────────────

    def flush(self) -> None:
        """
        Force-forward every armed timer to the child sink, regardless of
        elapsed time. Called on pipeline shutdown so we don't lose held
        events.
        """
        for entry in list(self._timers.values()):
            try:
                self._child.publish(entry.event)
            except Exception:  # noqa: BLE001 — best-effort drain
                _log.exception(
                    "ttl_flush_publish_failed",
                    event_type=entry.event.event_type.value,
                    device_id=entry.event.device_id,
                    sensor_id=entry.event.sensor_id,
                )
        self._timers.clear()

    # Used by the perf instrumentation to expose pending count.
    @property
    def pending_count(self) -> int:
        return len(self._timers)

    # ─── Internals ────────────────────────────────────────────────

    def _expire_due(self, now: float) -> None:
        """Forward any timers whose TTL has elapsed at the given clock."""
        # Two-pass: collect, forward outside the iteration so a child's
        # publish() exception can't desync our state. We delete BEFORE
        # publishing for the same reason.
        ready: list[_ArmedTimer] = []
        for key in list(self._timers):
            entry = self._timers[key]
            if now - entry.armed_at >= entry.ttl_seconds:
                ready.append(entry)
                del self._timers[key]
        for entry in ready:
            try:
                self._child.publish(entry.event)
            except Exception:  # noqa: BLE001
                _log.exception(
                    "ttl_expire_publish_failed",
                    event_type=entry.event.event_type.value,
                    device_id=entry.event.device_id,
                    sensor_id=entry.event.sensor_id,
                )
