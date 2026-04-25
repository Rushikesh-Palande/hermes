"""
EventWindowBuffer push/slice/clear invariants.
"""

from __future__ import annotations

from hermes.detection.window_buffer import EventWindowBuffer


def test_slice_returns_only_in_range_samples() -> None:
    buf = EventWindowBuffer(buffer_seconds=10.0, expected_rate_hz=100.0)
    for i in range(100):
        buf.push_snapshot(device_id=1, ts=i * 0.1, values={1: float(i)})
    # Slice [2.0, 5.0]: ts=2.0..5.0 inclusive on a 0.1 step → 31 samples.
    samples = buf.slice(device_id=1, sensor_id=1, start_ts=2.0, end_ts=5.0)
    assert len(samples) == 31
    assert samples[0][0] == 2.0
    assert samples[-1][0] == 5.0


def test_unknown_device_or_sensor_returns_empty() -> None:
    buf = EventWindowBuffer()
    assert buf.slice(device_id=99, sensor_id=1, start_ts=0.0, end_ts=10.0) == []
    buf.push_snapshot(device_id=1, ts=1.0, values={1: 10.0})
    assert buf.slice(device_id=1, sensor_id=2, start_ts=0.0, end_ts=10.0) == []


def test_per_sensor_isolation() -> None:
    buf = EventWindowBuffer()
    buf.push_snapshot(device_id=1, ts=1.0, values={1: 10.0, 2: 20.0})
    s1 = buf.slice(1, 1, 0.0, 10.0)
    s2 = buf.slice(1, 2, 0.0, 10.0)
    assert s1 == [(1.0, 10.0)]
    assert s2 == [(1.0, 20.0)]


def test_ringbuffer_evicts_oldest_at_capacity() -> None:
    # 1 s × 100 Hz × 1.5 headroom = 150 maxlen per sensor.
    buf = EventWindowBuffer(buffer_seconds=1.0, expected_rate_hz=100.0)
    for i in range(300):
        buf.push_snapshot(device_id=1, ts=i * 0.001, values={1: float(i)})
    # Only the newest 150 remain.
    samples = buf.slice(1, 1, 0.0, 1.0)
    assert len(samples) == 150
    assert samples[0][1] == 150.0  # oldest retained
    assert samples[-1][1] == 299.0


def test_clear_device_drops_all_sensors_for_that_device() -> None:
    buf = EventWindowBuffer()
    buf.push_snapshot(1, 1.0, {1: 10.0, 2: 20.0})
    buf.push_snapshot(2, 1.0, {1: 30.0})
    buf.clear_device(1)
    assert buf.slice(1, 1, 0.0, 10.0) == []
    assert buf.slice(1, 2, 0.0, 10.0) == []
    assert buf.slice(2, 1, 0.0, 10.0) == [(1.0, 30.0)]
