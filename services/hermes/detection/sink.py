"""
Event sink implementations.

``LoggingEventSink`` is the Phase 3b/c default — every detected event is
emitted at INFO level via structlog, tagged with device/sensor/type so
downstream log aggregation can count and chart them. No DB wiring yet;
that arrives in Phase 3e via ``DbEventSink`` (to be added) which writes
the narrow ``events`` row plus the ±9 s ``event_windows`` BLOB.

The sink interface is deliberately sync and non-blocking. An async sink
MUST enqueue internally and drain on a background task — never await a
DB round-trip from ``publish()``, as that would pause the detection loop
and push back-pressure onto the MQTT callback thread.
"""

from __future__ import annotations

from hermes.detection.types import DetectedEvent
from hermes.logging import get_logger

_log = get_logger(__name__, component="detection")


class LoggingEventSink:
    """Structured-log sink. Single-line emit per event; zero I/O blocking."""

    def publish(self, event: DetectedEvent) -> None:
        _log.info(
            "event_detected",
            event_type=event.event_type.value,
            device_id=event.device_id,
            sensor_id=event.sensor_id,
            triggered_at=event.triggered_at,
            **event.metadata,
        )
