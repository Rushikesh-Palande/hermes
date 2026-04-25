"""
Type C (range on avg_T3) detector invariants.

Fire rule: avg_T3 (rolling mean) leaves absolute bounds [lower, upper].
Thresholds are raw sensor units, not percentages.
"""

from __future__ import annotations

from hermes.db.models import EventType
from hermes.detection.config import TypeCConfig
from hermes.detection.type_c import TypeCDetector
from hermes.detection.types import Sample


def _warm_c(det: TypeCDetector, start_ts: float, n: int, value: float) -> float:
    ts = start_ts
    for _ in range(n):
        det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=value))
        ts += 0.01
    return ts


def test_disabled_detector_never_fires() -> None:
    det = TypeCDetector(
        TypeCConfig(
            enabled=False,
            T3=1.0,
            threshold_lower=40.0,
            threshold_upper=60.0,
            expected_sample_rate_hz=100.0,
        )
    )
    # Even extreme values don't fire when disabled.
    for i in range(150):
        assert det.feed(Sample(ts=i * 0.01, device_id=1, sensor_id=1, value=1000.0)) is None


def test_avg_within_bounds_never_fires() -> None:
    det = TypeCDetector(
        TypeCConfig(
            enabled=True,
            T3=1.0,
            threshold_lower=40.0,
            threshold_upper=60.0,
            expected_sample_rate_hz=100.0,
        )
    )
    for i in range(200):
        assert det.feed(Sample(ts=i * 0.01, device_id=1, sensor_id=1, value=50.0)) is None


def test_avg_above_upper_threshold_fires() -> None:
    det = TypeCDetector(
        TypeCConfig(
            enabled=True,
            T3=1.0,
            threshold_lower=40.0,
            threshold_upper=60.0,
            expected_sample_rate_hz=100.0,
        )
    )
    # Feed samples at 80 — avg_T3 will walk up to 80 past the upper threshold.
    events = []
    ts = 0.0
    for _ in range(300):
        ev = det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=80.0))
        if ev is not None:
            events.append(ev)
        ts += 0.01
    assert events, "expected at least one Type C event for avg above upper"
    assert events[0].event_type is EventType.C
    assert events[0].metadata["avg_T3"] > 60.0


def test_avg_below_lower_threshold_fires() -> None:
    det = TypeCDetector(
        TypeCConfig(
            enabled=True,
            T3=1.0,
            threshold_lower=40.0,
            threshold_upper=60.0,
            expected_sample_rate_hz=100.0,
        )
    )
    events = []
    ts = 0.0
    for _ in range(300):
        ev = det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=10.0))
        if ev is not None:
            events.append(ev)
        ts += 0.01
    assert events
    assert events[0].metadata["avg_T3"] < 40.0


def test_current_avg_tracks_rolling_mean() -> None:
    det = TypeCDetector(
        TypeCConfig(
            enabled=True,
            T3=1.0,
            threshold_lower=0.0,
            threshold_upper=100.0,
            expected_sample_rate_hz=100.0,
        )
    )
    assert det.current_avg is None
    _warm_c(det, 0.0, 150, 55.0)
    assert det.current_avg is not None
    assert abs(det.current_avg - 55.0) < 1e-6


def test_current_avg_updated_even_when_disabled() -> None:
    """
    Type D depends on Type C's current_avg; legacy keeps the window
    primed even with Type C disabled. Parity is enforced here.
    """
    det = TypeCDetector(
        TypeCConfig(
            enabled=False,
            T3=1.0,
            expected_sample_rate_hz=100.0,
        )
    )
    _warm_c(det, 0.0, 150, 73.0)
    assert det.current_avg is not None
    assert abs(det.current_avg - 73.0) < 1e-6


def test_debounce_delays_fire() -> None:
    debounce_s = 0.4
    det = TypeCDetector(
        TypeCConfig(
            enabled=True,
            T3=0.5,
            threshold_lower=40.0,
            threshold_upper=60.0,
            debounce_seconds=debounce_s,
            expected_sample_rate_hz=100.0,
        )
    )
    # Warm up at 50 — inside bounds.
    ts = _warm_c(det, 0.0, 100, 50.0)

    first_event = None
    fire_ts = None
    # Switch to 100 — avg will rise. Takes some samples for avg > 60.
    for _ in range(300):
        ev = det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=100.0))
        if ev is not None and first_event is None:
            first_event = ev
            fire_ts = ts
        ts += 0.01

    assert first_event is not None
    assert fire_ts is not None
    assert fire_ts - first_event.triggered_at >= debounce_s - 1e-9


def test_reset_clears_state() -> None:
    det = TypeCDetector(
        TypeCConfig(
            enabled=True,
            T3=1.0,
            threshold_lower=0.0,
            threshold_upper=100.0,
            expected_sample_rate_hz=100.0,
        )
    )
    _warm_c(det, 0.0, 150, 50.0)
    det.reset()
    assert det.current_avg is None
