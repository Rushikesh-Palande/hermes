"""
STM32 clock anchoring invariants.

Covers:
    * First-sample offset initialisation.
    * Steady-state anchoring holds the offset.
    * Drift threshold triggers re-anchor (simulated counter reset).
    * Per-device isolation: one device's reset does not affect another.
"""

from __future__ import annotations

from hermes.ingest.clock import DRIFT_THRESHOLD_S, ClockRegistry, DeviceClock


def test_first_sample_sets_offset_and_returns_receive_ts() -> None:
    clock = DeviceClock()
    # Receive at wall=1000, device=500 → offset=500. Computed ts=500+500=1000.
    ts = clock.anchor(receive_ts=1000.0, dev_ts_sec=500.0)
    assert ts == 1000.0
    assert clock.offset == 500.0


def test_steady_state_uses_stored_offset() -> None:
    clock = DeviceClock()
    clock.anchor(receive_ts=1000.0, dev_ts_sec=500.0)
    # Next sample: device clock advanced 1s, wall also advanced 1s.
    # Stored offset (500) still valid → ts = 501 + 500 = 1001.
    ts = clock.anchor(receive_ts=1001.0, dev_ts_sec=501.0)
    assert ts == 1001.0
    assert clock.offset == 500.0  # unchanged


def test_drift_beyond_threshold_triggers_reanchor() -> None:
    clock = DeviceClock()
    clock.anchor(receive_ts=1000.0, dev_ts_sec=500.0)  # offset = 500
    # Simulate STM counter reset: device ts jumps back to 0 but wall advanced.
    receive = 1000.0 + DRIFT_THRESHOLD_S + 1.0  # > threshold off
    ts = clock.anchor(receive_ts=receive, dev_ts_sec=0.0)
    # Re-anchored: new offset = receive - 0 = receive; ts = receive.
    assert ts == receive
    assert clock.offset == receive


def test_drift_below_threshold_does_not_reanchor() -> None:
    clock = DeviceClock()
    clock.anchor(receive_ts=1000.0, dev_ts_sec=500.0)  # offset = 500
    # Small drift (< threshold): trust stored offset, do not re-anchor.
    drift = DRIFT_THRESHOLD_S - 1.0
    ts = clock.anchor(receive_ts=1000.0 + drift, dev_ts_sec=500.0)
    assert ts == 1000.0  # unchanged — the device says same time
    assert clock.offset == 500.0


def test_custom_threshold_is_respected() -> None:
    tight = 0.5
    clock = DeviceClock(drift_threshold_s=tight)
    clock.anchor(receive_ts=1000.0, dev_ts_sec=500.0)
    # Drift of 1 s — below default 5 s but above tight 0.5 s → re-anchor.
    ts = clock.anchor(receive_ts=1001.0, dev_ts_sec=501.5)
    # With tight threshold: ts=501.5+500=1001.5, |1001.5-1001|=0.5 not > 0.5,
    # so stays stable. Push further:
    assert ts == 1001.5
    ts2 = clock.anchor(receive_ts=1002.0, dev_ts_sec=503.0)
    # 503 + 500 = 1003; |1003 - 1002| = 1.0 > 0.5 → re-anchor to 1002.
    assert ts2 == 1002.0


def test_registry_isolates_devices() -> None:
    reg = ClockRegistry()
    reg.anchor(device_id=1, receive_ts=1000.0, dev_ts_sec=500.0)
    reg.anchor(device_id=2, receive_ts=2000.0, dev_ts_sec=100.0)
    assert reg.offset_for(1) == 500.0
    assert reg.offset_for(2) == 1900.0


def test_registry_returns_none_for_unseen_device() -> None:
    reg = ClockRegistry()
    assert reg.offset_for(42) is None


def test_registry_passes_threshold_to_new_clocks() -> None:
    reg = ClockRegistry(drift_threshold_s=0.1)
    reg.anchor(device_id=1, receive_ts=1000.0, dev_ts_sec=500.0)
    # 1 s drift on a 0.1 s threshold must re-anchor.
    ts = reg.anchor(device_id=1, receive_ts=1001.0, dev_ts_sec=502.0)
    assert ts == 1001.0
