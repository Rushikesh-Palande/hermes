"""
Type A (variance / CV%) detector invariants.

Tests are deterministic and hardware-free: samples are synthesised in
memory and fed to the detector. Each test pins one facet of the legacy
behaviour spec (EVENT_DETECTION_CONTRACT §3).
"""

from __future__ import annotations

from hermes.db.models import EventType
from hermes.detection.config import TypeAConfig
from hermes.detection.type_a import TypeADetector
from hermes.detection.types import Sample


def _steady(detector: TypeADetector, ts: float, value: float) -> None:
    """Feed a single constant sample; assert it does not fire."""
    event = detector.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=value))
    assert event is None


def _warm(
    detector: TypeADetector,
    start_ts: float,
    rate_hz: float,
    n: int,
    value: float,
) -> float:
    """Feed ``n`` constant samples at ``rate_hz``; return the last ts."""
    dt = 1.0 / rate_hz
    ts = start_ts
    for _ in range(n):
        _steady(detector, ts, value)
        ts += dt
    return ts


def test_disabled_detector_never_fires() -> None:
    det = TypeADetector(TypeAConfig(enabled=False, T1=1.0, threshold_cv=0.01))
    # Feed a wildly varying signal; disabled flag must dominate.
    for i, v in enumerate((1.0, 100.0, 1.0, 100.0)):
        assert det.feed(Sample(ts=i * 0.01, device_id=1, sensor_id=1, value=v)) is None


def test_constant_signal_never_fires() -> None:
    det = TypeADetector(
        TypeAConfig(enabled=True, T1=1.0, threshold_cv=1.0, expected_sample_rate_hz=100.0)
    )
    _warm(det, start_ts=0.0, rate_hz=100.0, n=150, value=50.0)
    # CV% on a truly constant signal is 0 — never fires.


def test_high_variance_triggers_after_warmup() -> None:
    det = TypeADetector(
        TypeAConfig(enabled=True, T1=1.0, threshold_cv=5.0, expected_sample_rate_hz=100.0)
    )
    # 100 samples alternating between 40 and 60 around mean 50. CV% = 20.
    events = []
    ts = 0.0
    for i in range(150):
        v = 60.0 if i % 2 == 0 else 40.0
        event = det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=v))
        if event is not None:
            events.append(event)
        ts += 0.01
    # At least one event fired.
    assert events, "expected at least one Type A event"
    assert events[0].event_type is EventType.A
    assert events[0].device_id == 1
    assert events[0].sensor_id == 1
    # metadata carries derived stats.
    md = events[0].metadata
    assert md["cv_percent"] > 5.0
    assert abs(md["average"] - 50.0) < 1e-6
    assert md["window_seconds"] == 1.0


def test_warmup_suppresses_events_until_init_fill_reached() -> None:
    det = TypeADetector(
        TypeAConfig(
            enabled=True,
            T1=1.0,
            threshold_cv=1.0,
            init_fill_ratio=0.9,
            expected_sample_rate_hz=100.0,
        )
    )
    # init_threshold = int(1.0 * 100 * 0.9) = 90.
    # Feed 50 high-variance samples — should NOT fire (still cold).
    ts = 0.0
    for i in range(50):
        v = 60.0 if i % 2 == 0 else 40.0
        event = det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=v))
        assert event is None, f"sample {i} fired before warmup"
        ts += 0.01


def test_debounce_delays_fire_by_at_least_debounce_seconds() -> None:
    debounce_s = 0.5
    det = TypeADetector(
        TypeAConfig(
            enabled=True,
            T1=1.0,
            threshold_cv=5.0,
            debounce_seconds=debounce_s,
            expected_sample_rate_hz=100.0,
        )
    )
    # Warm up on constant signal.
    ts = _warm(det, start_ts=0.0, rate_hz=100.0, n=100, value=50.0)

    # Flip to high variance; record the fire timestamp separately from
    # the event's triggered_at (which is the crossing moment).
    fire_ts: float | None = None
    first_event = None
    for i in range(300):
        v = 60.0 if i % 2 == 0 else 40.0
        event = det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=v))
        if event is not None and first_event is None:
            first_event = event
            fire_ts = ts
        ts += 0.01

    assert first_event is not None, "expected a fire after debounce elapsed"
    assert fire_ts is not None
    # Fire came strictly after the crossing, by at least debounce_seconds.
    # Allow a small FP epsilon.
    assert fire_ts - first_event.triggered_at >= debounce_s - 1e-9
    # triggered_at is earlier than fire_ts (debounce delayed the fire).
    assert first_event.triggered_at < fire_ts


def test_debounce_zero_fires_immediately_on_crossing() -> None:
    det = TypeADetector(
        TypeAConfig(
            enabled=True,
            T1=1.0,
            threshold_cv=5.0,
            debounce_seconds=0.0,
            expected_sample_rate_hz=100.0,
        )
    )
    ts = _warm(det, start_ts=0.0, rate_hz=100.0, n=100, value=50.0)

    first_event = None
    fire_ts = None
    for i in range(300):
        v = 60.0 if i % 2 == 0 else 40.0
        event = det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=v))
        if event is not None and first_event is None:
            first_event = event
            fire_ts = ts
        ts += 0.01

    assert first_event is not None
    assert fire_ts is not None
    # With zero debounce, fire_ts is the same sample as the crossing.
    assert first_event.triggered_at == fire_ts


def test_brief_spike_shorter_than_debounce_does_not_fire() -> None:
    """
    CV% spike that resolves within the T1 window before ``debounce_seconds``
    elapses must silently reset the debounce timer — no event.
    """
    # Short T1 + short debounce so the spike clears before debounce deadline.
    det = TypeADetector(
        TypeAConfig(
            enabled=True,
            T1=0.05,  # 5 samples at 100 Hz
            threshold_cv=5.0,
            debounce_seconds=0.2,  # 20 samples at 100 Hz
            init_fill_ratio=0.9,
            expected_sample_rate_hz=100.0,
        )
    )
    # Warm up (init threshold = 4 samples).
    ts = _warm(det, start_ts=0.0, rate_hz=100.0, n=30, value=50.0)

    # Two variance samples (ts, ts+0.01).
    for v in (100.0, 0.0):
        assert det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=v)) is None
        ts += 0.01

    # Quiet samples: spike clears from the 0.05 s window before 0.2 s
    # debounce elapses → detector must NOT fire.
    for _ in range(100):
        assert det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=50.0)) is None
        ts += 0.01


def test_data_gap_resets_window_state() -> None:
    det = TypeADetector(
        TypeAConfig(enabled=True, T1=1.0, threshold_cv=1.0, expected_sample_rate_hz=100.0)
    )
    ts = _warm(det, start_ts=0.0, rate_hz=100.0, n=100, value=50.0)

    # Simulate a 5 s data gap (> 2 s threshold) — window should clear.
    ts += 5.0
    # First sample after the gap must restart warmup.
    # Feed only a few samples — still cold, no fire.
    for i in range(10):
        v = 60.0 if i % 2 == 0 else 40.0
        assert det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=v)) is None
        ts += 0.01


def test_reset_clears_all_state() -> None:
    det = TypeADetector(
        TypeAConfig(enabled=True, T1=1.0, threshold_cv=1.0, expected_sample_rate_hz=100.0)
    )
    _warm(det, start_ts=0.0, rate_hz=100.0, n=150, value=50.0)

    det.reset()

    # After reset: warmup-cold again. Feed 10 high-variance samples;
    # none should fire because we're below the init threshold.
    ts = 10.0
    for i in range(10):
        v = 60.0 if i % 2 == 0 else 40.0
        assert det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=v)) is None
        ts += 0.01


def test_mean_epsilon_handles_zero_mean_window() -> None:
    """A zero-mean window must not raise; CV% is bounded via the epsilon floor."""
    det = TypeADetector(
        TypeAConfig(
            enabled=True,
            T1=1.0,
            threshold_cv=1.0,
            expected_sample_rate_hz=100.0,
        )
    )
    ts = 0.0
    # Symmetric square wave ±1 around 0: mean is (very near) zero.
    events = []
    for i in range(200):
        v = 1.0 if i % 2 == 0 else -1.0
        ev = det.feed(Sample(ts=ts, device_id=1, sensor_id=1, value=v))
        if ev is not None:
            events.append(ev)
        ts += 0.01
    # With mean≈0 and std=1, CV% would be enormous — expect events.
    assert events, "zero-mean high-variance window should still fire"
