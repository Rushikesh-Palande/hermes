"""
Core data types for the detection subsystem.

Everything here is frozen + slotted for speed on the 123 Hz × 12-sensor
hot path. ``Sample`` and ``DetectedEvent`` are intentionally shallow —
side-effect-free dataclasses that can be cheaply constructed per sample
without allocating a dict per field.

Protocols (``SensorDetector`` and ``EventSink``) define contracts;
concrete implementations live alongside in this package. Using
``typing.Protocol`` rather than ABC lets tests drop in ad-hoc fakes
without inheriting a base class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from hermes.db.models import EventType


@dataclass(frozen=True, slots=True)
class Sample:
    """
    One reading from one sensor on one device at one instant.

    ``ts`` is wall-clock seconds (Unix epoch) as returned by
    ``ClockRegistry.anchor``; ``value`` is already offset-corrected by
    ``OffsetCache.apply`` in the ingest pipeline.
    """

    ts: float
    device_id: int
    sensor_id: int
    value: float


@dataclass(frozen=True, slots=True)
class DetectedEvent:
    """
    One triggered event.

    ``triggered_at`` is the original threshold-crossing timestamp (NOT
    the fire time) — legacy parity. Debouncing delays fire but preserves
    the moment of the first boundary cross in this field.

    ``metadata`` is a small dict of derived values (CV%, window average,
    bounds). It becomes the ``events.metadata`` JSONB column verbatim
    when Phase 3e wires persistence.
    """

    event_type: EventType
    device_id: int
    sensor_id: int
    triggered_at: float
    metadata: dict[str, Any] = field(default_factory=dict)


class SensorDetector(Protocol):
    """
    Stateful detector for one (device, sensor, event_type) triple.

    Implementations MUST be:
        * single-threaded (the engine serialises calls per sensor),
        * allocation-light (no per-sample dict construction unless firing),
        * side-effect-free except for their own internal state.
    """

    def feed(self, sample: Sample) -> DetectedEvent | None:
        """Consume one sample; return an event iff this sample triggered one."""
        ...

    def reset(self) -> None:
        """Clear all state (config change, data gap, device re-init)."""
        ...


class EventSink(Protocol):
    """
    Receives detected events for publication / persistence.

    ``publish`` is sync by design — it runs inside the ingest consumer
    task, which is single-threaded asyncio. A slow sink should enqueue
    to its own async queue and drain on a background task, not block
    here. See ``sink.py::LoggingEventSink`` for a non-blocking example.
    """

    def publish(self, event: DetectedEvent) -> None: ...
