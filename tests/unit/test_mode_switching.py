"""
Unit tests for ``ModeStateMachine`` (gap 3 — mode switching).

Covers the legacy state-machine semantics from
``EVENT_DETECTION_CONTRACT.md`` §2.3 / §7:

  * disabled short-circuit (returns ``active=True`` always)
  * POWER_ON → STARTUP transition (sustained above-threshold)
  * STARTUP → BREAK transition (sustained below-threshold) emits BREAK
    with ``triggered_at`` = the FIRST below-threshold sample's wall time
  * BREAK → STARTUP recovery (no second BREAK event)
  * Transient drop within the startup grace window doesn't reset the timer
  * Sustained drop beyond the grace window resets the startup timer
  * Asymmetry: any single recovery above ``break_threshold`` resets the
    below-threshold timer immediately (no break_reset_grace)
  * Per-(device, sensor) isolation
  * ``reset(device_id)`` clears state for that device only

Tests advance the clock by handing in explicit ``ts`` values; no
``time.sleep`` needed.
"""

from __future__ import annotations

from hermes.db.models import EventType
from hermes.detection.config import ModeSwitchingConfig, StaticConfigProvider
from hermes.detection.mode_switching import ModeStateMachine, SensorMode


def _machine(cfg: ModeSwitchingConfig) -> ModeStateMachine:
    """Build a ModeStateMachine wrapped in a Static provider."""
    return ModeStateMachine(StaticConfigProvider(mode_switching=cfg))


# ─── Disabled short-circuit ──────────────────────────────────────


def test_disabled_returns_active_for_every_sample() -> None:
    """When ``enabled=False``, the machine never transitions, never fires."""
    machine = _machine(ModeSwitchingConfig(enabled=False))
    for ts, value in [(0.0, 0.0), (1.0, 1000.0), (2.0, -500.0)]:
        decision = machine.feed(device_id=1, sensor_id=1, value=value, ts=ts)
        assert decision.active is True
        assert decision.break_event is None
    # Never created any state.
    assert machine.mode_of(1, 1) is SensorMode.POWER_ON


# ─── POWER_ON → STARTUP ──────────────────────────────────────────


def test_power_on_to_startup_after_sustained_above_threshold() -> None:
    cfg = ModeSwitchingConfig(enabled=True, startup_threshold=100.0, startup_duration_seconds=0.5)
    machine = _machine(cfg)

    # First above-threshold sample arms the timer but doesn't transition.
    d = machine.feed(1, 1, value=150.0, ts=10.0)
    assert d.active is False
    assert machine.mode_of(1, 1) is SensorMode.POWER_ON

    # Halfway through — still POWER_ON.
    d = machine.feed(1, 1, value=150.0, ts=10.3)
    assert d.active is False
    assert machine.mode_of(1, 1) is SensorMode.POWER_ON

    # Cross the duration boundary — transition to STARTUP.
    d = machine.feed(1, 1, value=150.0, ts=10.5)
    assert d.active is True
    assert d.break_event is None
    assert machine.mode_of(1, 1) is SensorMode.STARTUP


def test_power_on_below_threshold_does_not_transition() -> None:
    cfg = ModeSwitchingConfig(enabled=True, startup_threshold=100.0, startup_duration_seconds=0.5)
    machine = _machine(cfg)
    for ts in (0.0, 0.5, 1.0, 1.5):
        d = machine.feed(1, 1, value=50.0, ts=ts)
        assert d.active is False
    assert machine.mode_of(1, 1) is SensorMode.POWER_ON


# ─── Grace window for transient drops during POWER_ON ────────────


def test_brief_dip_within_grace_does_not_reset_startup_timer() -> None:
    """A < grace-duration drop is forgiven; sustained accumulation continues."""
    cfg = ModeSwitchingConfig(
        enabled=True,
        startup_threshold=100.0,
        startup_duration_seconds=2.0,
        startup_reset_grace_s=1.0,
    )
    machine = _machine(cfg)

    # Cross above threshold at t=0.
    machine.feed(1, 1, value=150.0, ts=0.0)
    # Brief dip at t=0.5, recovers at t=0.8 (< 1 s grace) — timer NOT reset.
    machine.feed(1, 1, value=50.0, ts=0.5)
    machine.feed(1, 1, value=150.0, ts=0.8)
    # Continue accumulating; we're still using the original t=0 anchor,
    # so by t=2.0 we should transition.
    d = machine.feed(1, 1, value=150.0, ts=2.0)
    assert d.active is True
    assert machine.mode_of(1, 1) is SensorMode.STARTUP


def test_sustained_drop_resets_startup_timer() -> None:
    """A drop sustained beyond grace clears the above-timer."""
    cfg = ModeSwitchingConfig(
        enabled=True,
        startup_threshold=100.0,
        startup_duration_seconds=2.0,
        startup_reset_grace_s=1.0,
    )
    machine = _machine(cfg)

    machine.feed(1, 1, value=150.0, ts=0.0)
    # Drop at t=0.5 and stay below for >= 1 s grace.
    machine.feed(1, 1, value=50.0, ts=0.5)
    machine.feed(1, 1, value=50.0, ts=1.6)  # grace expired here
    # Recover above; timer should restart from THIS sample.
    machine.feed(1, 1, value=150.0, ts=2.0)
    # By t=3.0 we're 1 s into a 2 s wait — still POWER_ON.
    d = machine.feed(1, 1, value=150.0, ts=3.0)
    assert d.active is False
    assert machine.mode_of(1, 1) is SensorMode.POWER_ON
    # By t=4.0 we cross the threshold.
    d = machine.feed(1, 1, value=150.0, ts=4.0)
    assert d.active is True
    assert machine.mode_of(1, 1) is SensorMode.STARTUP


# ─── STARTUP → BREAK ─────────────────────────────────────────────


def _into_startup(machine: ModeStateMachine, cfg: ModeSwitchingConfig) -> None:
    """Helper: drive (1,1) into STARTUP at a known time."""
    # Use cfg.startup_duration_seconds for the boundary so this works
    # for any config the test passes.
    machine.feed(1, 1, value=cfg.startup_threshold + 50.0, ts=0.0)
    machine.feed(
        1,
        1,
        value=cfg.startup_threshold + 50.0,
        ts=cfg.startup_duration_seconds,
    )
    assert machine.mode_of(1, 1) is SensorMode.STARTUP


def test_startup_to_break_emits_event_with_first_crossing_timestamp() -> None:
    cfg = ModeSwitchingConfig(
        enabled=True,
        startup_threshold=100.0,
        break_threshold=50.0,
        startup_duration_seconds=0.1,
        break_duration_seconds=2.0,
    )
    machine = _machine(cfg)
    _into_startup(machine, cfg)

    # First sample below break_threshold — timer arms, NO event yet.
    base = 10.0  # arbitrary later wall time
    d = machine.feed(1, 1, value=20.0, ts=base)
    assert d.active is True
    assert d.break_event is None

    # Sustained below for the duration.
    d = machine.feed(1, 1, value=20.0, ts=base + 1.0)
    assert d.break_event is None

    # Cross the break_duration boundary.
    d = machine.feed(1, 1, value=20.0, ts=base + 2.0)
    assert d.active is False
    assert d.break_event is not None
    assert d.break_event.event_type is EventType.BREAK
    assert d.break_event.device_id == 1
    assert d.break_event.sensor_id == 1
    # CRITICAL invariant: triggered_at = FIRST crossing time, not the
    # moment the duration elapsed.
    assert d.break_event.triggered_at == base
    assert machine.mode_of(1, 1) is SensorMode.BREAK


def test_recovery_above_break_threshold_resets_below_timer_immediately() -> None:
    """No grace window on recovery above break_threshold — single sample resets."""
    cfg = ModeSwitchingConfig(
        enabled=True,
        startup_threshold=100.0,
        break_threshold=50.0,
        startup_duration_seconds=0.1,
        break_duration_seconds=2.0,
    )
    machine = _machine(cfg)
    _into_startup(machine, cfg)

    # Drop below break at t=10.0, partial duration.
    machine.feed(1, 1, value=20.0, ts=10.0)
    machine.feed(1, 1, value=20.0, ts=11.0)  # 1 s into 2 s — not yet
    # Single sample at or above break_threshold resets the timer.
    machine.feed(1, 1, value=80.0, ts=11.5)
    # Drop again — should arm a fresh timer at this new ts.
    machine.feed(1, 1, value=20.0, ts=12.0)
    # By t=13.0 (1 s in) we should NOT have fired yet because the timer
    # restarted at 12.0.
    d = machine.feed(1, 1, value=20.0, ts=13.0)
    assert d.break_event is None
    assert machine.mode_of(1, 1) is SensorMode.STARTUP

    # Cross at t=14.0 — now we fire.
    d = machine.feed(1, 1, value=20.0, ts=14.0)
    assert d.break_event is not None
    assert d.break_event.triggered_at == 12.0  # the SECOND crossing


# ─── BREAK → STARTUP recovery (no second BREAK event) ────────────


def test_break_to_startup_recovery_emits_no_event() -> None:
    cfg = ModeSwitchingConfig(
        enabled=True,
        startup_threshold=100.0,
        break_threshold=50.0,
        startup_duration_seconds=0.5,
        break_duration_seconds=1.0,
    )
    machine = _machine(cfg)
    _into_startup(machine, cfg)

    # Drive into BREAK.
    machine.feed(1, 1, value=20.0, ts=10.0)
    d = machine.feed(1, 1, value=20.0, ts=11.0)
    assert d.break_event is not None
    assert machine.mode_of(1, 1) is SensorMode.BREAK

    # Recovery: sustained above startup_threshold.
    machine.feed(1, 1, value=150.0, ts=20.0)
    d = machine.feed(1, 1, value=150.0, ts=20.5)
    assert d.active is True
    # CRITICAL: BREAK → STARTUP does NOT emit a new event.
    assert d.break_event is None
    assert machine.mode_of(1, 1) is SensorMode.STARTUP


# ─── Per-(device, sensor) isolation ──────────────────────────────


def test_independent_state_per_device_and_sensor() -> None:
    cfg = ModeSwitchingConfig(enabled=True, startup_threshold=100.0, startup_duration_seconds=0.5)
    machine = _machine(cfg)

    # device 1 sensor 1 ramps up; device 1 sensor 2 stays low; device 2 sensor 1 ramps up.
    machine.feed(1, 1, value=150.0, ts=0.0)
    machine.feed(1, 2, value=50.0, ts=0.0)
    machine.feed(2, 1, value=150.0, ts=0.0)
    # Cross the duration on (1,1) and (2,1) only.
    machine.feed(1, 1, value=150.0, ts=0.5)
    machine.feed(1, 2, value=50.0, ts=0.5)
    machine.feed(2, 1, value=150.0, ts=0.5)

    assert machine.mode_of(1, 1) is SensorMode.STARTUP
    assert machine.mode_of(1, 2) is SensorMode.POWER_ON
    assert machine.mode_of(2, 1) is SensorMode.STARTUP


# ─── reset(device_id) ────────────────────────────────────────────


def test_reset_clears_only_that_device() -> None:
    cfg = ModeSwitchingConfig(enabled=True, startup_threshold=100.0, startup_duration_seconds=0.5)
    machine = _machine(cfg)
    # Drive both devices into STARTUP.
    machine.feed(1, 1, value=150.0, ts=0.0)
    machine.feed(2, 1, value=150.0, ts=0.0)
    machine.feed(1, 1, value=150.0, ts=0.5)
    machine.feed(2, 1, value=150.0, ts=0.5)
    assert machine.mode_of(1, 1) is SensorMode.STARTUP
    assert machine.mode_of(2, 1) is SensorMode.STARTUP

    machine.reset(device_id=1)
    assert machine.mode_of(1, 1) is SensorMode.POWER_ON  # reset
    assert machine.mode_of(2, 1) is SensorMode.STARTUP  # untouched


# ─── Metadata on BREAK events ────────────────────────────────────


def test_break_event_metadata_carries_threshold_and_trigger_value() -> None:
    cfg = ModeSwitchingConfig(
        enabled=True,
        startup_threshold=100.0,
        break_threshold=50.0,
        startup_duration_seconds=0.1,
        break_duration_seconds=1.0,
    )
    machine = _machine(cfg)
    _into_startup(machine, cfg)

    machine.feed(1, 1, value=20.0, ts=10.0)
    d = machine.feed(1, 1, value=15.0, ts=11.0)
    assert d.break_event is not None
    md = d.break_event.metadata
    assert md["trigger_value"] == 15.0
    assert md["break_threshold"] == 50.0
    assert md["break_duration_seconds"] == 1.0
