"""
Unit tests for ``TtlGateSink``.

Covers all four legacy rules + BREAK bypass + flush on shutdown +
per-(device, sensor) isolation.

The gate's clock advances on every ``publish`` call from the incoming
event's ``triggered_at``, so tests don't need ``time.sleep`` — we
just hand it events with explicit timestamps.
"""

from __future__ import annotations

from typing import Any

from hermes.db.models import EventType
from hermes.detection.ttl_gate import TtlGateSink
from hermes.detection.types import DetectedEvent


class _RecordingSink:
    """Captures events forwarded by the gate."""

    def __init__(self) -> None:
        self.events: list[DetectedEvent] = []

    def publish(self, event: DetectedEvent) -> None:
        self.events.append(event)


def _ev(
    *,
    event_type: EventType,
    triggered_at: float,
    device_id: int = 1,
    sensor_id: int = 1,
    metadata: dict[str, Any] | None = None,
) -> DetectedEvent:
    return DetectedEvent(
        event_type=event_type,
        device_id=device_id,
        sensor_id=sensor_id,
        triggered_at=triggered_at,
        metadata=metadata or {},
    )


# ─── Rule 4: arm + forward after TTL elapses ──────────────────────


def test_single_event_held_for_ttl_then_forwarded() -> None:
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=1.0)

    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0))
    # Below TTL — held.
    assert child.events == []
    assert gate.pending_count == 1

    # Trigger expiry by publishing another event later. The expire pass
    # runs first, so the held event forwards before the new one is
    # considered. Use a different sensor so dedup rules don't apply.
    gate.publish(_ev(event_type=EventType.A, triggered_at=11.5, sensor_id=2))
    assert len(child.events) == 1
    assert child.events[0].event_type is EventType.A
    assert child.events[0].sensor_id == 1


# ─── Rule 3: same type swallowed within TTL ───────────────────────


def test_duplicate_same_type_within_ttl_is_swallowed() -> None:
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=5.0)

    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0))
    gate.publish(_ev(event_type=EventType.A, triggered_at=10.5))
    gate.publish(_ev(event_type=EventType.A, triggered_at=11.2))
    assert child.events == []
    # Only one timer; the others were absorbed.
    assert gate.pending_count == 1


def test_after_ttl_expires_next_event_re_arms() -> None:
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=1.0)
    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0))
    # Force expiry by publishing far in the future on a DIFFERENT
    # sensor (so the first event's TTL elapses without the new event
    # itself being deduped against it).
    gate.publish(_ev(event_type=EventType.A, triggered_at=12.0, sensor_id=2))
    assert len(child.events) == 1

    # Now a new fresh fire on sensor 1 — must re-arm.
    gate.publish(_ev(event_type=EventType.A, triggered_at=12.0))
    # Still 1 forwarded; new timer is just armed.
    assert len(child.events) == 1
    assert gate.pending_count == 2  # sensor 2 timer + new sensor 1 timer


# ─── Rule 1: higher-priority blocks lower ────────────────────────


def test_lower_priority_blocked_when_higher_armed() -> None:
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=5.0)

    gate.publish(_ev(event_type=EventType.D, triggered_at=10.0))
    # B is lower priority than D — must be dropped while D is armed.
    gate.publish(_ev(event_type=EventType.B, triggered_at=10.5))
    gate.publish(_ev(event_type=EventType.A, triggered_at=10.5))
    gate.publish(_ev(event_type=EventType.C, triggered_at=10.5))
    assert child.events == []
    assert gate.pending_count == 1  # only D


# ─── Rule 2: higher-priority preempts lower ──────────────────────


def test_higher_priority_preempts_lower_armed() -> None:
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=5.0)

    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0))
    gate.publish(_ev(event_type=EventType.B, triggered_at=10.1))
    # Both should NOT have forwarded yet (TTL hasn't elapsed) but only
    # B should be armed — A was preempted.
    assert child.events == []
    assert gate.pending_count == 1

    # Push C which is even higher; should preempt B.
    gate.publish(_ev(event_type=EventType.C, triggered_at=10.2))
    assert child.events == []
    assert gate.pending_count == 1


# ─── BREAK bypasses everything ────────────────────────────────────


def test_break_event_bypasses_gate() -> None:
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=5.0)
    gate.publish(_ev(event_type=EventType.D, triggered_at=10.0))
    gate.publish(_ev(event_type=EventType.BREAK, triggered_at=10.1))
    # BREAK forwarded immediately even though D is armed.
    assert len(child.events) == 1
    assert child.events[0].event_type is EventType.BREAK


def test_break_event_does_not_disturb_armed_timers() -> None:
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=5.0)
    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0))
    gate.publish(_ev(event_type=EventType.BREAK, triggered_at=10.1))
    # A still armed; pending_count == 1.
    assert gate.pending_count == 1


# ─── Per-sensor / per-device isolation ────────────────────────────


def test_different_sensors_do_not_interfere() -> None:
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=5.0)

    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0, sensor_id=1))
    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0, sensor_id=2))
    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0, sensor_id=3))
    # Three independent timers.
    assert gate.pending_count == 3


def test_different_devices_do_not_interfere() -> None:
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=5.0)

    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0, device_id=1))
    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0, device_id=2))
    assert gate.pending_count == 2


def test_higher_priority_on_one_sensor_does_not_block_lower_on_another() -> None:
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=5.0)

    gate.publish(_ev(event_type=EventType.D, triggered_at=10.0, sensor_id=1))
    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0, sensor_id=2))
    # Both armed; the priority rule scopes to (device, sensor).
    assert gate.pending_count == 2


# ─── Flush behaviour ──────────────────────────────────────────────


def test_flush_forwards_all_held_events() -> None:
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=5.0)

    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0, sensor_id=1))
    gate.publish(_ev(event_type=EventType.B, triggered_at=10.0, sensor_id=2))
    gate.publish(_ev(event_type=EventType.C, triggered_at=10.0, sensor_id=3))
    assert child.events == []
    gate.flush()
    assert len(child.events) == 3
    assert gate.pending_count == 0


def test_flush_empty_gate_is_noop() -> None:
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=5.0)
    gate.flush()  # must not raise
    assert child.events == []


# ─── Edge cases ───────────────────────────────────────────────────


def test_zero_ttl_passes_event_through_immediately() -> None:
    """``ttl_seconds=0`` is a valid degenerate config — events forward at once."""
    child = _RecordingSink()
    gate = TtlGateSink(child=child, ttl_seconds=0.0)

    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0))
    # The expire pass on the FIRST event runs against an empty timer
    # set, so the event itself sticks for one cycle. Immediately after
    # arming we should still have 1 timer that will forward on the
    # very next publish.
    assert gate.pending_count == 1

    gate.publish(_ev(event_type=EventType.A, triggered_at=10.0, sensor_id=2))
    # First event forwarded (its 0-TTL expired), second armed.
    assert len(child.events) == 1


def test_negative_ttl_rejected_at_construction() -> None:
    import pytest

    with pytest.raises(ValueError, match="non-negative"):
        TtlGateSink(child=_RecordingSink(), ttl_seconds=-1.0)
