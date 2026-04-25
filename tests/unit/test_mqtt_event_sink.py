"""
Unit tests for ``MqttEventSink`` and ``MultiplexEventSink``.

Both are pure Python — we feed a fake paho client (just records what
``publish`` was called with) so the tests don't need a broker. Topic
shape and payload match the legacy contract verbatim
(see HARDWARE_INTERFACE.md §6.1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hermes.db.models import EventType
from hermes.detection.mqtt_sink import MqttEventSink
from hermes.detection.sink import LoggingEventSink, MultiplexEventSink
from hermes.detection.types import DetectedEvent


@dataclass
class _FakePublishInfo:
    rc: int = 0


class _FakeMqttClient:
    """Just enough surface to satisfy MqttEventSink.publish()."""

    def __init__(self, *, rc: int = 0) -> None:
        self.published: list[tuple[str, str, int]] = []
        self._rc = rc

    def publish(self, topic: str, payload: str, qos: int = 0) -> _FakePublishInfo:
        self.published.append((topic, payload, qos))
        return _FakePublishInfo(rc=self._rc)


def _make_event(
    *,
    event_type: EventType = EventType.A,
    device_id: int = 3,
    sensor_id: int = 7,
    triggered_at: float = 1_700_000_000.123,
    metadata: dict[str, Any] | None = None,
) -> DetectedEvent:
    return DetectedEvent(
        event_type=event_type,
        device_id=device_id,
        sensor_id=sensor_id,
        triggered_at=triggered_at,
        metadata=metadata or {"trigger_value": 42.5},
    )


def test_publish_uses_legacy_topic_shape() -> None:
    sink = MqttEventSink(base_topic="stm32/events")
    client = _FakeMqttClient()
    sink.attach_client(client)
    sink.publish(_make_event(event_type=EventType.A, device_id=3, sensor_id=7))
    assert len(client.published) == 1
    topic, _payload, qos = client.published[0]
    assert topic == "stm32/events/3/7/A"
    assert qos == 0


def test_publish_payload_has_timestamp_and_sensor_value() -> None:
    sink = MqttEventSink()
    client = _FakeMqttClient()
    sink.attach_client(client)
    sink.publish(
        _make_event(metadata={"trigger_value": 12.34, "average": 50.0}),
    )
    _, payload, _ = client.published[0]
    body = json.loads(payload)
    assert set(body.keys()) == {"timestamp", "sensor_value"}
    assert body["sensor_value"] == 12.34
    # Format: "YYYY-MM-DD HH:MM:SS.mmm"; truncated to 23 chars.
    assert len(body["timestamp"]) == 23
    assert body["timestamp"][4] == "-" and body["timestamp"][13] == ":"


def test_publish_serialises_missing_trigger_value_as_null() -> None:
    sink = MqttEventSink()
    client = _FakeMqttClient()
    sink.attach_client(client)
    sink.publish(_make_event(metadata={"average": 50.0}))  # no trigger_value
    _, payload, _ = client.published[0]
    assert json.loads(payload)["sensor_value"] is None


def test_publish_uppercases_event_type() -> None:
    sink = MqttEventSink()
    client = _FakeMqttClient()
    sink.attach_client(client)
    for et in (EventType.A, EventType.B, EventType.C, EventType.D, EventType.BREAK):
        sink.publish(_make_event(event_type=et))
    topics = [t for t, _, _ in client.published]
    assert topics[-1].endswith("/BREAK")
    assert topics[0].endswith("/A")


def test_publish_no_op_when_client_not_attached() -> None:
    """Pre-start fires (rare) must not crash; just warn-and-drop."""
    sink = MqttEventSink()
    sink.publish(_make_event())  # no attach_client called — should not raise


def test_detach_client_makes_subsequent_publishes_no_op() -> None:
    sink = MqttEventSink()
    client = _FakeMqttClient()
    sink.attach_client(client)
    sink.publish(_make_event())
    sink.detach_client()
    sink.publish(_make_event())
    # Only the first publish made it through.
    assert len(client.published) == 1


def test_custom_base_topic_strips_trailing_slash() -> None:
    sink = MqttEventSink(base_topic="prod/events/")
    client = _FakeMqttClient()
    sink.attach_client(client)
    sink.publish(_make_event(device_id=5, sensor_id=2))
    topic, _, _ = client.published[0]
    assert topic == "prod/events/5/2/A"


# ─── MultiplexEventSink ────────────────────────────────────────────


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[DetectedEvent] = []

    def publish(self, event: DetectedEvent) -> None:
        self.events.append(event)


class _BoomSink:
    """Fails on every publish — used to verify multiplex isolates faults."""

    def publish(self, _event: DetectedEvent) -> None:
        raise RuntimeError("intentional sink failure")


def test_multiplex_dispatches_to_every_member() -> None:
    a, b, c = _RecordingSink(), _RecordingSink(), _RecordingSink()
    multi = MultiplexEventSink([a, b, c])
    ev = _make_event()
    multi.publish(ev)
    assert a.events == b.events == c.events == [ev]


def test_multiplex_one_sink_failure_does_not_affect_others() -> None:
    good = _RecordingSink()
    multi = MultiplexEventSink([_BoomSink(), good])
    multi.publish(_make_event())
    # The good sink still received the event.
    assert len(good.events) == 1


def test_multiplex_preserves_order() -> None:
    seen: list[str] = []

    class _Tagged:
        def __init__(self, name: str) -> None:
            self.name = name

        def publish(self, _event: DetectedEvent) -> None:
            seen.append(self.name)

    multi = MultiplexEventSink([_Tagged("first"), _Tagged("second"), _Tagged("third")])
    multi.publish(_make_event())
    assert seen == ["first", "second", "third"]


def test_multiplex_with_logging_sink_does_not_blow_up() -> None:
    """Smoke test — logging sink + recording sink coexist."""
    rec = _RecordingSink()
    multi = MultiplexEventSink([LoggingEventSink(), rec])
    multi.publish(_make_event())
    assert len(rec.events) == 1
