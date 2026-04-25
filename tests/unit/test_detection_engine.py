"""
DetectionEngine routing + sink dispatch.

Uses a fake sink that records events to a list so we can assert who
fired, in what order, and with which metadata. No hardware, no DB.
"""

from __future__ import annotations

from hermes.db.models import EventType
from hermes.detection.config import StaticConfigProvider, TypeAConfig
from hermes.detection.engine import DetectionEngine
from hermes.detection.types import DetectedEvent


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[DetectedEvent] = []

    def publish(self, event: DetectedEvent) -> None:
        self.events.append(event)


def _high_variance_snapshot(i: int) -> dict[int, float]:
    """Return a 12-sensor snapshot with alternating values for testing."""
    base = 60.0 if i % 2 == 0 else 40.0
    return {sid: base for sid in range(1, 13)}


def test_disabled_config_produces_no_events() -> None:
    sink = _RecordingSink()
    engine = DetectionEngine(StaticConfigProvider(TypeAConfig(enabled=False)), sink)
    for i in range(200):
        engine.feed_snapshot(device_id=1, ts=i * 0.01, values=_high_variance_snapshot(i))
    assert sink.events == []


def test_enabled_engine_fires_per_sensor() -> None:
    sink = _RecordingSink()
    engine = DetectionEngine(
        StaticConfigProvider(
            TypeAConfig(
                enabled=True,
                T1=1.0,
                threshold_cv=5.0,
                expected_sample_rate_hz=100.0,
            )
        ),
        sink,
    )
    for i in range(200):
        engine.feed_snapshot(device_id=1, ts=i * 0.01, values=_high_variance_snapshot(i))
    # Every one of the 12 sensors should have fired at least once.
    fired_sensors = {e.sensor_id for e in sink.events}
    assert fired_sensors == set(range(1, 13))
    assert all(e.event_type is EventType.A for e in sink.events)
    assert all(e.device_id == 1 for e in sink.events)


def test_reset_device_drops_detector_state() -> None:
    sink = _RecordingSink()
    engine = DetectionEngine(
        StaticConfigProvider(
            TypeAConfig(
                enabled=True,
                T1=1.0,
                threshold_cv=5.0,
                expected_sample_rate_hz=100.0,
            )
        ),
        sink,
    )
    # Warm up + fire.
    for i in range(200):
        engine.feed_snapshot(device_id=1, ts=i * 0.01, values=_high_variance_snapshot(i))
    fired_before = len(sink.events)
    assert fired_before > 0

    engine.reset_device(1)
    sink.events.clear()

    # Immediately after reset, a small burst should not re-fire (cold).
    for i in range(50):
        engine.feed_snapshot(device_id=1, ts=10.0 + i * 0.01, values=_high_variance_snapshot(i))
    assert sink.events == []


def test_multiple_devices_kept_separate() -> None:
    sink = _RecordingSink()
    engine = DetectionEngine(
        StaticConfigProvider(
            TypeAConfig(
                enabled=True,
                T1=1.0,
                threshold_cv=5.0,
                expected_sample_rate_hz=100.0,
            )
        ),
        sink,
    )

    # Device 1 has a high-variance signal; device 2 stays quiet.
    for i in range(200):
        engine.feed_snapshot(device_id=1, ts=i * 0.01, values=_high_variance_snapshot(i))
        engine.feed_snapshot(device_id=2, ts=i * 0.01, values={sid: 50.0 for sid in range(1, 13)})

    device_ids_fired = {e.device_id for e in sink.events}
    assert device_ids_fired == {1}
