"""
Event sink implementations.

The sink interface is deliberately sync and non-blocking. An async sink
MUST enqueue internally and drain on a background task — never await a
DB round-trip or a network I/O from ``publish()``, as that would pause
the detection loop and push back-pressure onto the MQTT callback thread.

Implementations:

    LoggingEventSink     — Phase 3b/c default; structlog INFO per event.
    DbEventSink          — Phase 3e; writes events + event_windows.
    MqttEventSink        — outbound publish to stm32/events/<dev>/<sid>/<TYPE>
                           (sibling sinks share the paho client owned by
                           IngestPipeline).
    MultiplexEventSink   — fan-out to N sinks; one slow sink can't break
                           the others, so an MQTT outage doesn't block DB
                           writes (or vice versa).
"""

from __future__ import annotations

from hermes.detection.types import DetectedEvent, EventSink
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


class MultiplexEventSink:
    """
    Fan-out sink: dispatch each event to every member.

    Exceptions in one member are caught + logged so an outage on one
    output doesn't disable the others. Order is preserved — sinks run
    in the order they were passed in — so callers can put the fastest /
    most-important first.
    """

    __slots__ = ("_sinks",)

    def __init__(self, sinks: list[EventSink]) -> None:
        self._sinks = list(sinks)

    def publish(self, event: DetectedEvent) -> None:
        for sink in self._sinks:
            try:
                sink.publish(event)
            except Exception:  # noqa: BLE001 — keep the others alive
                _log.exception(
                    "event_sink_publish_failed",
                    sink=type(sink).__name__,
                    event_type=event.event_type.value,
                    device_id=event.device_id,
                    sensor_id=event.sensor_id,
                )
