"""
Outbound MQTT event sink.

Publishes every detected event to ``stm32/events/<device>/<sensor>/<TYPE>``
so external subscribers (PLCs, SCADA, downstream alerting) can listen
without polling the database. Topic shape and payload match the legacy
HARDWARE_INTERFACE.md §6 contract, so existing firmware / consumers
keep working.

Threading note:
    paho's ``publish()`` is thread-safe — the call enqueues onto paho's
    internal output queue, where its loop_start() worker thread does the
    socket write. We're calling from the asyncio detection task; that's
    fine. The 1-µs enqueue cost is well under our hot-path budget.

Lifecycle:
    The sink is constructed BEFORE the paho client connects (because
    the engine wants its sink at construction time). ``attach_client``
    binds the live client once IngestPipeline.start() has it. Until
    then, ``publish`` is a no-op + WARN log: better than crashing the
    detection loop on early-fire events.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from hermes import metrics as _m
from hermes.detection.types import DetectedEvent
from hermes.logging import get_logger

if TYPE_CHECKING:
    import paho.mqtt.client as mqtt

_log = get_logger(__name__, component="detection")

DEFAULT_BASE_TOPIC: str = "stm32/events"


class MqttEventSink:
    """Publishes events to ``<base_topic>/<device>/<sensor>/<TYPE>``."""

    __slots__ = ("_base_topic", "_client", "_qos")

    def __init__(self, base_topic: str = DEFAULT_BASE_TOPIC, qos: int = 0) -> None:
        self._base_topic = base_topic.rstrip("/")
        self._qos = qos
        self._client: mqtt.Client | None = None

    def attach_client(self, client: mqtt.Client) -> None:
        """Wire in a connected paho client. Idempotent."""
        self._client = client

    def detach_client(self) -> None:
        """Drop the client reference; subsequent publishes go to /dev/null."""
        self._client = None

    def publish(self, event: DetectedEvent) -> None:
        if self._client is None:
            # Pre-start fire (rare but possible). Don't crash the engine.
            _log.warning(
                "mqtt_sink_no_client",
                event_type=event.event_type.value,
                device_id=event.device_id,
                sensor_id=event.sensor_id,
            )
            return

        topic = f"{self._base_topic}/{event.device_id}/{event.sensor_id}/{event.event_type.value}"
        # Format mirrors the legacy contract:
        #   timestamp: "YYYY-MM-DD HH:MM:SS.mmm" (local-tz, ms-truncated)
        #   sensor_value: float | None
        ts = datetime.fromtimestamp(event.triggered_at)
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        trigger = event.metadata.get("trigger_value")
        sensor_value = float(trigger) if isinstance(trigger, (int, float)) else None
        payload = {"timestamp": ts_str, "sensor_value": sensor_value}

        # paho's publish is sync but non-blocking — enqueues onto its
        # output thread. retain=False matches the legacy default.
        info = self._client.publish(
            topic,
            json.dumps(payload, separators=(",", ":")),
            qos=self._qos,
        )
        # paho's MQTTMessageInfo carries an rc; non-zero means the local
        # queue is full or the client is disconnected.
        if info.rc != 0:
            _log.warning(
                "mqtt_publish_local_failure",
                topic=topic,
                rc=info.rc,
            )
            return
        _m.EVENTS_PUBLISHED_TOTAL.labels(event_type=event.event_type.value).inc()
