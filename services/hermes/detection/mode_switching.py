"""
Per-(device, sensor) mode-switching state machine — gap 3.

Tracks each sensor's mode (POWER_ON / STARTUP / BREAK) and decides on
each incoming sample:

  1. whether to emit a BREAK event (STARTUP → BREAK transition)
  2. whether the sensor is currently in STARTUP (i.e. detection-active)

The state machine is a faithful port of the legacy
``check_mode_transition`` logic at
``/home/embed/hammer/src/detection/event_detector.py:855-955``. See
``docs/contracts/EVENT_DETECTION_CONTRACT.md`` §2.3 and §7 for the
authoritative spec.

Single-threaded by design: called from the asyncio consumer task only
(via ``DetectionEngine.feed_snapshot``).

Why a separate module rather than a method on ``DetectionEngine``:
the state machine has its own non-trivial state (six per-sensor
timestamps + a mode integer) and its own configuration object
(``ModeSwitchingConfig``). Keeping it isolated lets the engine stay
focused on routing samples to detectors and keeps the legacy parity
tests focused on this single piece of behaviour.

Key invariants:

* ``enabled=False`` → the machine returns ``active=True`` for every
  sensor on every sample, never emits BREAK, and keeps no state. This
  matches the legacy ``mode_switching_enabled=False`` short-circuit
  (line 879 of legacy event_detector.py).
* The BREAK event's ``triggered_at`` is the moment the value first
  fell below ``break_threshold`` — NOT the moment the duration
  elapsed. Operators have alarms wired to that earlier timestamp.
* No symmetric "break_reset_grace" exists. While in STARTUP, any
  single recovery above ``break_threshold`` resets the
  below-threshold timer immediately. This is asymmetric with the
  startup grace window and is preserved on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from hermes.db.models import EventType
from hermes.detection.config import DetectorConfigProvider, ModeSwitchingConfig
from hermes.detection.types import DetectedEvent


class SensorMode(IntEnum):
    """Sensor lifecycle mode.

    Integer values match the legacy ``sensor_modes`` codes (0/1/2) so
    debug logs and any future cross-system tooling stay readable.
    """

    POWER_ON = 0
    STARTUP = 1
    BREAK = 2


@dataclass(slots=True)
class _SensorState:
    """All per-sensor timers + the current mode. One per (device, sensor)."""

    mode: SensorMode = SensorMode.POWER_ON
    # First time we saw value > startup_threshold during the current
    # POWER_ON / BREAK wait. None means "not currently above threshold".
    above_start_time: float | None = None
    # First time we saw a transient drop below startup_threshold while
    # waiting. None means no current drop. If a drop persists for
    # ``startup_reset_grace_s``, the above-timer resets.
    above_drop_time: float | None = None
    # First time we saw value < break_threshold while in STARTUP. None
    # means "not currently below threshold". If sustained for
    # ``break_duration_seconds``, BREAK fires.
    below_start_time: float | None = None
    # Wall time at which this sensor entered STARTUP. Useful for the
    # post-STARTUP gate ("must have T seconds of STARTUP data before
    # any event can fire") — implementation hook for a future phase.
    startup_time: float | None = None


@dataclass(frozen=True, slots=True)
class ModeDecision:
    """Result of feeding one sample through the state machine.

    ``active`` mirrors the legacy ``sensor_active_states[sensor_id]`` —
    True only in STARTUP. Detection types use it for gating.

    ``break_event`` is non-None only on the STARTUP → BREAK transition
    that this very sample completed. The caller publishes it directly
    to the sink; the TTL gate already passes BREAK through unchanged.
    """

    active: bool
    break_event: DetectedEvent | None = None


# Pre-allocated singletons for the two no-event outcomes. The state
# machine returns these millions of times per process lifetime; reusing
# them avoids a per-sample dataclass allocation on the hot path. The
# break-event arm always allocates a fresh decision (rare event, and
# its contents are unique anyway).
_DECISION_ACTIVE: ModeDecision = ModeDecision(active=True, break_event=None)
_DECISION_INACTIVE: ModeDecision = ModeDecision(active=False, break_event=None)


class ModeStateMachine:
    """Per-device, per-sensor mode tracker.

    Holds state for every (device_id, sensor_id) it's seen. Lazily
    created on first sample. ``reset(device_id)`` is called from
    ``DetectionEngine.reset_device`` so a config reload starts every
    sensor back in POWER_ON — same behaviour as a fresh process.

    Configuration is fetched per-sensor on every call via the provider.
    The provider already caches the resolved config, and the
    per-sample lookup is a single dict get; cheaper than caching
    locally and risking staleness after a NOTIFY-driven reload.
    """

    __slots__ = ("_config_provider", "_states", "_mode_lookup")

    def __init__(self, config_provider: DetectorConfigProvider) -> None:
        self._config_provider = config_provider
        # Bound-method cache: avoids one LOAD_ATTR per sample on the
        # hot path. ~24 000 calls/s makes this measurable.
        self._mode_lookup = config_provider.mode_switching_for
        self._states: dict[tuple[int, int], _SensorState] = {}

    def feed(self, device_id: int, sensor_id: int, value: float, ts: float) -> ModeDecision:
        """Advance the state machine for one sample. Returns a decision.

        The disabled-fast-path is the dominant production case (gap 3
        is opt-in). It returns a pre-allocated singleton ``_DECISION_ACTIVE``
        — at 24 000 samples/s, allocating a fresh ModeDecision each time
        was a measurable bench regression. Same trick for the inactive
        and active no-event paths in the per-sample state machine; only
        the BREAK-emission arm allocates.
        """
        cfg = self._mode_lookup(device_id, sensor_id)

        # Fast path: mode switching disabled. No state, no events,
        # always active. Matches the legacy short-circuit so existing
        # deployments behave identically when the operator hasn't
        # turned this on.
        if not cfg.enabled:
            return _DECISION_ACTIVE

        state = self._states.get((device_id, sensor_id))
        if state is None:
            state = _SensorState()
            self._states[(device_id, sensor_id)] = state

        if state.mode is SensorMode.STARTUP:
            return self._tick_startup(state, device_id, sensor_id, value, ts, cfg)
        # POWER_ON or BREAK use the same recovery path.
        return self._tick_waiting(state, value, ts, cfg)

    # ─── State-machine arms ───────────────────────────────────────

    def _tick_startup(
        self,
        state: _SensorState,
        device_id: int,
        sensor_id: int,
        value: float,
        ts: float,
        cfg: ModeSwitchingConfig,
    ) -> ModeDecision:
        """STARTUP arm: watch for a sustained drop below ``break_threshold``."""
        if value < cfg.break_threshold:
            if state.below_start_time is None:
                state.below_start_time = ts
                return _DECISION_ACTIVE
            duration = ts - state.below_start_time
            if duration >= cfg.break_duration_seconds:
                # STARTUP → BREAK: emit BREAK event with the original
                # crossing time as triggered_at, NOT ts.
                crossing_time = state.below_start_time
                state.mode = SensorMode.BREAK
                state.below_start_time = None
                state.above_start_time = None
                state.above_drop_time = None
                state.startup_time = None
                event = DetectedEvent(
                    event_type=EventType.BREAK,
                    device_id=device_id,
                    sensor_id=sensor_id,
                    triggered_at=crossing_time,
                    metadata=_break_metadata(value, cfg),
                )
                return ModeDecision(active=False, break_event=event)
            return _DECISION_ACTIVE
        # Recovery: any sample at or above break_threshold clears the
        # drop timer immediately. No grace window on this side.
        state.below_start_time = None
        return ModeDecision(active=True, break_event=None)

    def _tick_waiting(
        self,
        state: _SensorState,
        value: float,
        ts: float,
        cfg: ModeSwitchingConfig,
    ) -> ModeDecision:
        """POWER_ON or BREAK arm: watch for sustained recovery above ``startup_threshold``."""
        if value > cfg.startup_threshold:
            if state.above_start_time is None:
                state.above_start_time = ts
                state.above_drop_time = None
                # Just crossed; not yet sustained.
                return _DECISION_INACTIVE
            # Already counting up. A previous drop, if any, is now
            # forgiven by this recovery.
            state.above_drop_time = None
            duration = ts - state.above_start_time
            if duration >= cfg.startup_duration_seconds:
                # Transition to STARTUP. NO BREAK event fires on the
                # BREAK → STARTUP recovery path; the prior BREAK is
                # already in the durable sink chain.
                state.mode = SensorMode.STARTUP
                state.above_start_time = None
                state.above_drop_time = None
                state.below_start_time = None
                state.startup_time = ts
                return _DECISION_ACTIVE
            return _DECISION_INACTIVE
        # Below startup_threshold while waiting. If we'd previously
        # crossed above, see whether this dip is sustained long enough
        # to reset the timer.
        if state.above_start_time is not None:
            if state.above_drop_time is None:
                state.above_drop_time = ts
            elif (ts - state.above_drop_time) >= cfg.startup_reset_grace_s:
                # Sustained drop — wipe the wait state and start fresh.
                state.above_start_time = None
                state.above_drop_time = None
        return ModeDecision(active=False, break_event=None)

    # ─── Lifecycle ────────────────────────────────────────────────

    def reset(self, device_id: int) -> None:
        """Drop all state for ``device_id``. Called on config reload."""
        for key in list(self._states):
            if key[0] == device_id:
                del self._states[key]

    def reset_all(self) -> None:
        """Drop all state. Used by tests."""
        self._states.clear()

    # ─── Introspection ────────────────────────────────────────────

    def mode_of(self, device_id: int, sensor_id: int) -> SensorMode:
        """Current mode of (device, sensor). Defaults to POWER_ON if unseen."""
        state = self._states.get((device_id, sensor_id))
        if state is None:
            return SensorMode.POWER_ON
        return state.mode


def _break_metadata(trigger_value: float, cfg: ModeSwitchingConfig) -> dict[str, Any]:
    """Build the metadata dict for a BREAK event.

    Kept minimal and stable: the API + DB layer treat metadata as
    opaque JSONB, but operators inspecting events should see the
    threshold and the value that triggered detection. ``trigger_value``
    is the sample that completed the duration window — informational,
    NOT the value at the original crossing.
    """
    return {
        "trigger_value": trigger_value,
        "break_threshold": cfg.break_threshold,
        "break_duration_seconds": cfg.break_duration_seconds,
    }
