"""
Type D (two-stage averaging) detector invariants.

Type D depends on Type C's ``current_avg``; the tests construct both
detectors and feed them the same samples in the order the engine does
(C first, then D), mirroring the production behaviour.

To keep tests fast, T4/T5 are deliberately small (a real deployment
uses T4=10 s and T5=30 s, which would take ~40 s of synthetic samples
to warm up).
"""

from __future__ import annotations

from hermes.db.models import EventType
from hermes.detection.config import TypeCConfig, TypeDConfig
from hermes.detection.type_c import TypeCDetector
from hermes.detection.type_d import TypeDDetector
from hermes.detection.types import Sample


def _feed_pair(
    type_c: TypeCDetector,
    type_d: TypeDDetector,
    ts: float,
    value: float,
    device_id: int = 1,
    sensor_id: int = 1,
) -> tuple[object, object]:
    """Feed both detectors the same sample. Returns (c_event, d_event)."""
    sample = Sample(ts=ts, device_id=device_id, sensor_id=sensor_id, value=value)
    c_event = type_c.feed(sample)
    d_event = type_d.feed(sample)
    return c_event, d_event


def _make_pair(
    *,
    enabled: bool,
    t3: float = 0.3,
    t4: float = 0.2,
    t5: float = 2.0,
    tolerance_pct: float = 5.0,
    debounce_seconds: float = 0.0,
) -> tuple[TypeCDetector, TypeDDetector]:
    """Construct a (C, D) pair with consistent expected_sample_rate_hz."""
    type_c = TypeCDetector(
        TypeCConfig(
            enabled=False,  # we only need C's current_avg, never C's events
            T3=t3,
            expected_sample_rate_hz=100.0,
        )
    )
    type_d = TypeDDetector(
        TypeDConfig(
            enabled=enabled,
            T4=t4,
            T5=t5,
            tolerance_pct=tolerance_pct,
            debounce_seconds=debounce_seconds,
            expected_sample_rate_hz=100.0,
        ),
        type_c,
    )
    return type_c, type_d


def _feed_steady(
    type_c: TypeCDetector,
    type_d: TypeDDetector,
    start_ts: float,
    n: int,
    value: float,
    rate_hz: float = 100.0,
) -> float:
    ts = start_ts
    dt = 1.0 / rate_hz
    for _ in range(n):
        _feed_pair(type_c, type_d, ts, value)
        ts += dt
    return ts


def test_disabled_detector_never_fires() -> None:
    type_c, type_d = _make_pair(enabled=False)
    # Feed wildly varying signal; disabled D never fires.
    for i in range(800):
        v = 100.0 if i % 2 == 0 else 0.0
        _, ev = _feed_pair(type_c, type_d, i * 0.01, v)
        assert ev is None


def test_warmup_suppresses_events_until_t4_plus_t5_elapsed() -> None:
    type_c, type_d = _make_pair(enabled=True, t4=0.2, t5=2.0, tolerance_pct=5.0)

    # Feed for 1.5 s — less than T4 + T5 (= 2.2 s) → no fire even on
    # extreme values.
    ts = 0.0
    for i in range(150):
        v = 1000.0 if i == 100 else 50.0
        _, ev = _feed_pair(type_c, type_d, ts, v)
        assert ev is None
        ts += 0.01


def test_steady_signal_inside_band_does_not_fire() -> None:
    type_c, type_d = _make_pair(enabled=True, t4=0.2, t5=2.0, tolerance_pct=5.0)
    # 4 seconds of constant 50 — avg_T3 ≈ 50, avg_T5 ≈ 50, band [45, 55].
    ts = _feed_steady(type_c, type_d, 0.0, 400, 50.0)
    # Continue feeding constant; still no fire.
    for _ in range(200):
        _, ev = _feed_pair(type_c, type_d, ts, 50.0)
        assert ev is None
        ts += 0.01


def test_step_change_outside_band_fires() -> None:
    type_c, type_d = _make_pair(enabled=True, t3=0.3, t4=0.2, t5=2.0, tolerance_pct=5.0)
    # Warm up at 50 for 4 s — avg_T3, avg_T4, avg_T5 all around 50.
    ts = _feed_steady(type_c, type_d, 0.0, 400, 50.0)

    # Step change to 100. avg_T3 walks up quickly (T3=0.3); avg_T5 lags
    # because it averages per-second buckets that are still mostly 50.
    fire_event = None
    for _ in range(300):
        _, ev = _feed_pair(type_c, type_d, ts, 100.0)
        if ev is not None and fire_event is None:
            fire_event = ev
        ts += 0.01

    assert fire_event is not None, "expected a Type D event after step change"
    assert fire_event.event_type is EventType.D
    md = fire_event.metadata
    # avg_T3 should be near 100 by then; avg_T5 lagging well below.
    assert md["avg_T3"] > md["upper_bound"]


def test_no_fire_when_paired_c_has_no_avg() -> None:
    # Configure D with very small T4/T5 so D could warm up almost
    # instantly; pair it with a C that has a much LONGER T3 so C is
    # still cold when D is ready. Result: D must hold fire while C is
    # not yet emitting an avg_T3.
    type_c = TypeCDetector(TypeCConfig(enabled=False, T3=10.0, expected_sample_rate_hz=100.0))
    type_d = TypeDDetector(
        TypeDConfig(
            enabled=True,
            T4=0.1,
            T5=2.0,
            tolerance_pct=0.01,
            expected_sample_rate_hz=100.0,
        ),
        type_c,
    )
    ts = 0.0
    # 3 seconds of varying signal — D would otherwise fire on tiny tol,
    # but C's avg_T3 is None so D returns None.
    for i in range(300):
        v = 100.0 if i % 2 == 0 else 0.0
        _, ev = _feed_pair(type_c, type_d, ts, v)
        assert ev is None
        ts += 0.01


def test_debounce_delays_fire() -> None:
    debounce_s = 0.4
    type_c, type_d = _make_pair(
        enabled=True,
        t3=0.3,
        t4=0.2,
        t5=2.0,
        tolerance_pct=5.0,
        debounce_seconds=debounce_s,
    )
    ts = _feed_steady(type_c, type_d, 0.0, 400, 50.0)

    fire_ts = None
    first_event = None
    for _ in range(500):
        _, ev = _feed_pair(type_c, type_d, ts, 100.0)
        if ev is not None and first_event is None:
            first_event = ev
            fire_ts = ts
        ts += 0.01

    assert first_event is not None
    assert fire_ts is not None
    assert fire_ts - first_event.triggered_at >= debounce_s - 1e-9


def test_reset_clears_state() -> None:
    type_c, type_d = _make_pair(enabled=True, t4=0.2, t5=2.0, tolerance_pct=5.0)
    _feed_steady(type_c, type_d, 0.0, 400, 50.0)
    type_d.reset()

    # After reset: D is cold again. Even with paired C still warm and
    # an extreme step value, D needs T4 + T5 to warm up before firing.
    ts = 100.0
    for i in range(50):
        v = 100.0 if i % 2 == 0 else 50.0
        _, ev = _feed_pair(type_c, type_d, ts, v)
        assert ev is None
        ts += 0.01
