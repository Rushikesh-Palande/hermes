"""
Unit tests for ``hermes.metrics``.

Counters / gauges / sampled histograms — no DB or broker. Tests
assert *deltas* via the public ``.collect()`` API (read in
``hermes.metrics.counter_value`` etc.) so cross-test increments don't
poison the assertions.
"""

from __future__ import annotations

from hermes import metrics as m
from hermes.db.models import EventType


def test_msgs_received_counter_increments_per_device() -> None:
    before_3 = m.counter_value(m.MSGS_RECEIVED_TOTAL, device_id="3")
    before_7 = m.counter_value(m.MSGS_RECEIVED_TOTAL, device_id="7")
    m.MSGS_RECEIVED_TOTAL.labels(device_id="3").inc()
    m.MSGS_RECEIVED_TOTAL.labels(device_id="3").inc()
    m.MSGS_RECEIVED_TOTAL.labels(device_id="7").inc()
    assert m.counter_value(m.MSGS_RECEIVED_TOTAL, device_id="3") - before_3 == 2
    assert m.counter_value(m.MSGS_RECEIVED_TOTAL, device_id="7") - before_7 == 1


def test_invalid_msgs_counter_no_label() -> None:
    before = m.counter_value(m.MSGS_INVALID_TOTAL)
    m.MSGS_INVALID_TOTAL.inc()
    m.MSGS_INVALID_TOTAL.inc()
    assert m.counter_value(m.MSGS_INVALID_TOTAL) - before == 2


def test_events_detected_counter_breaks_down_by_type_and_device() -> None:
    a1_before = m.counter_value(m.EVENTS_DETECTED_TOTAL, event_type="A", device_id="1")
    b1_before = m.counter_value(m.EVENTS_DETECTED_TOTAL, event_type="B", device_id="1")
    a2_before = m.counter_value(m.EVENTS_DETECTED_TOTAL, event_type="A", device_id="2")
    m.EVENTS_DETECTED_TOTAL.labels(event_type=EventType.A.value, device_id="1").inc()
    m.EVENTS_DETECTED_TOTAL.labels(event_type=EventType.B.value, device_id="1").inc()
    m.EVENTS_DETECTED_TOTAL.labels(event_type=EventType.A.value, device_id="2").inc()
    assert m.counter_value(m.EVENTS_DETECTED_TOTAL, event_type="A", device_id="1") - a1_before == 1
    assert m.counter_value(m.EVENTS_DETECTED_TOTAL, event_type="B", device_id="1") - b1_before == 1
    assert m.counter_value(m.EVENTS_DETECTED_TOTAL, event_type="A", device_id="2") - a2_before == 1


def test_events_persisted_and_published_counters() -> None:
    before_persist = m.counter_value(m.EVENTS_PERSISTED_TOTAL, event_type="A")
    before_pub = m.counter_value(m.EVENTS_PUBLISHED_TOTAL, event_type="C")
    m.EVENTS_PERSISTED_TOTAL.labels(event_type="A").inc(3)
    m.EVENTS_PUBLISHED_TOTAL.labels(event_type="C").inc()
    assert m.counter_value(m.EVENTS_PERSISTED_TOTAL, event_type="A") - before_persist == 3
    assert m.counter_value(m.EVENTS_PUBLISHED_TOTAL, event_type="C") - before_pub == 1


def test_consume_queue_depth_gauge() -> None:
    m.CONSUME_QUEUE_DEPTH.set(42)
    assert m.gauge_value(m.CONSUME_QUEUE_DEPTH) == 42
    m.CONSUME_QUEUE_DEPTH.set(0)
    assert m.gauge_value(m.CONSUME_QUEUE_DEPTH) == 0


def test_db_writer_pending_gauge() -> None:
    m.DB_WRITER_PENDING.set(5)
    assert m.gauge_value(m.DB_WRITER_PENDING) == 5


def test_mqtt_connected_gauge_is_boolean_shaped() -> None:
    m.MQTT_CONNECTED.set(1)
    assert m.gauge_value(m.MQTT_CONNECTED) == 1
    m.MQTT_CONNECTED.set(0)
    assert m.gauge_value(m.MQTT_CONNECTED) == 0


def test_time_stage_observes_only_every_nth_call() -> None:
    """Sampling cuts overhead at 24 k samples/s."""
    sample_every = 100
    before = m.histogram_count(m.STAGE_DURATION, stage="parse_test")
    for _ in range(sample_every * 3):
        with m.time_stage("parse_test"):
            pass
    after = m.histogram_count(m.STAGE_DURATION, stage="parse_test")
    # We expect ~3 observations for 300 calls; allow tolerance because
    # the global sample counter is shared with other tests in the
    # session, so the sampling boundary may not align exactly.
    delta = after - before
    assert 1 <= delta <= 5, f"expected ~3 observations, got {delta}"
