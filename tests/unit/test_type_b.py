"""
Type B (post-window deviation) detector invariants.

Fire rule: latest sample outside ``avg_T2 ± tolerance_pct``.
With REF_VALUE=100 the band is ``avg_T2 ± tolerance_pct`` directly.
"""

from __future__ import annotations

from hermes.db.models import EventType
from hermes.detection.config import TypeBConfig
from hermes.detection.type_b import TypeBDetector
from hermes.detection.types import Sample


def _warm_b(det: TypeBDetector, start_ts: float, n: int, value: float) -> float:
    ts = start_ts
    for _ in range(n):
        det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=value))
        ts += 0.01
    return ts


def test_disabled_detector_never_fires() -> None:
    det = TypeBDetector(TypeBConfig(enabled=False, T2=1.0, lower_threshold_pct=5.0))
    for i in range(150):
        v = 1000.0 if i == 50 else 50.0
        assert det.feed(Sample(ts=i * 0.01, device_id=1, sensor_id=1, value=v)) is None


def test_sample_inside_band_never_fires() -> None:
    det = TypeBDetector(
        TypeBConfig(
            enabled=True,
            T2=1.0,
            lower_threshold_pct=10.0,
            upper_threshold_pct=10.0,
            expected_sample_rate_hz=100.0,
        )
    )
    # avg_T2 = 50, band = 50 ± 10 = [40, 60]. All samples at 50 — never fires.
    for i in range(200):
        assert det.feed(Sample(ts=i * 0.01, device_id=1, sensor_id=1, value=50.0)) is None


def test_sample_above_upper_bound_fires() -> None:
    det = TypeBDetector(
        TypeBConfig(
            enabled=True,
            T2=1.0,
            lower_threshold_pct=5.0,
            upper_threshold_pct=5.0,
            expected_sample_rate_hz=100.0,
        )
    )
    ts = _warm_b(det, 0.0, 150, 50.0)

    # Spike to 200 — well above band upper ≈ 55.
    event = det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=200.0))
    assert event is not None
    assert event.event_type is EventType.B
    md = event.metadata
    assert md["trigger_value"] == 200.0
    assert md["upper_bound"] < 200.0
    assert md["lower_bound"] < md["upper_bound"]


def test_sample_below_lower_bound_fires() -> None:
    det = TypeBDetector(
        TypeBConfig(
            enabled=True,
            T2=1.0,
            lower_threshold_pct=5.0,
            upper_threshold_pct=5.0,
            expected_sample_rate_hz=100.0,
        )
    )
    ts = _warm_b(det, 0.0, 150, 50.0)
    event = det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=-100.0))
    assert event is not None
    assert event.metadata["trigger_value"] == -100.0


def test_current_avg_is_exposed_after_warmup() -> None:
    det = TypeBDetector(TypeBConfig(enabled=True, T2=1.0, expected_sample_rate_hz=100.0))
    assert det.current_avg is None
    _warm_b(det, 0.0, 150, 42.0)
    assert det.current_avg is not None
    assert abs(det.current_avg - 42.0) < 1e-6


def test_debounce_delays_fire() -> None:
    debounce_s = 0.3
    det = TypeBDetector(
        TypeBConfig(
            enabled=True,
            T2=1.0,
            lower_threshold_pct=5.0,
            upper_threshold_pct=5.0,
            debounce_seconds=debounce_s,
            expected_sample_rate_hz=100.0,
        )
    )
    ts = _warm_b(det, 0.0, 150, 50.0)

    fire_ts = None
    first_event = None
    # Sustained out-of-range signal
    for _ in range(200):
        event = det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=200.0))
        if event is not None and first_event is None:
            first_event = event
            fire_ts = ts
        ts += 0.01

    assert first_event is not None
    assert fire_ts is not None
    assert fire_ts - first_event.triggered_at >= debounce_s - 1e-9


def test_brief_spike_shorter_than_debounce_suppresses_fire() -> None:
    debounce_s = 0.5
    det = TypeBDetector(
        TypeBConfig(
            enabled=True,
            T2=1.0,
            lower_threshold_pct=5.0,
            upper_threshold_pct=5.0,
            debounce_seconds=debounce_s,
            expected_sample_rate_hz=100.0,
        )
    )
    ts = _warm_b(det, 0.0, 150, 50.0)

    # One outlier above bound.
    assert det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=200.0)) is None
    ts += 0.01

    # Back in band — debounce resets, no fire ever.
    for _ in range(int(debounce_s * 100) + 50):
        assert det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=50.0)) is None
        ts += 0.01


def test_reset_clears_state() -> None:
    det = TypeBDetector(TypeBConfig(enabled=True, T2=1.0, expected_sample_rate_hz=100.0))
    _warm_b(det, 0.0, 150, 50.0)
    det.reset()
    assert det.current_avg is None
    # After reset, cold again; a few samples won't cross warmup.
    ts = 10.0
    for _ in range(10):
        assert det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=50.0)) is None
        ts += 0.01
