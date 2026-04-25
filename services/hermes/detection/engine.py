"""
Detection engine — routes samples to per-sensor detectors.

Responsibilities:
    * Own the per-(device, sensor, event_type) detector instances.
    * Run the per-sensor mode-switching state machine (gap 3).
    * Feed each sample to every detector it applies to.
    * Forward fired events to the configured sink.

Lazy allocation: a detector is created on the first sample for its
(device, sensor) pair. This avoids pre-allocating 20 × 12 × 4 = 960
detectors for devices that may never appear in a given deployment.

Lifecycle:
    * ``feed_snapshot(...)`` is called once per MQTT message by the
      ingest consumer (see ``hermes.ingest.main._consume``), passing the
      full ``{sensor_id: value}`` snapshot produced by the parser.
    * ``reset_device(device_id)`` is called when config changes for a
      device; it discards the cached detectors AND resets the
      mode-switching state machine so fresh config takes effect on the
      next sample.

Mode-switching gating (gap 3 / EVENT_DETECTION_CONTRACT.md §2.3):
    The mode state machine is consulted for every sensor on every
    sample. Its decision determines whether downstream detectors run:

        * Type A — ALWAYS feeds the running-sum state so the variance
          window stays primed during POWER_ON / BREAK. The threshold
          comparison itself is skipped when ``active=False`` by
          discarding any event the detector returns. Matches the
          legacy ``_run_type_a_detection`` behaviour at line 1517.
        * Types B/C/D — skipped entirely when ``active=False``. Their
          windows re-prime after the next STARTUP entry. Matches the
          legacy worker_manager._process_type_X gate at lines 208,
          263, 320.

    A BREAK event emitted by the state machine bypasses all detectors
    and is published directly to the sink. The TtlGateSink already
    forwards BREAK unchanged (alpha.13), so priority/dedup rules don't
    apply.

When ``mode_switching.enabled=False`` (the default), the state machine
short-circuits to ``active=True`` and detection runs unconditionally —
deployments that haven't turned on mode switching see zero behaviour
change.
"""

from __future__ import annotations

from hermes import metrics as _m
from hermes.db.models import EventType
from hermes.detection.config import DetectorConfigProvider
from hermes.detection.mode_switching import ModeStateMachine
from hermes.detection.type_a import TypeADetector
from hermes.detection.type_b import TypeBDetector
from hermes.detection.type_c import TypeCDetector
from hermes.detection.type_d import TypeDDetector
from hermes.detection.types import EventSink, Sample, SensorDetector

# Fixed event-type order. Kept deterministic so tests can rely on it.
# Type D MUST come after Type C: D reads C's ``current_avg`` for the
# same sample tick, so C must run first.
_EVENT_TYPE_ORDER: tuple[EventType, ...] = (
    EventType.A,
    EventType.B,
    EventType.C,
    EventType.D,
)

# A compound key per detector instance: (device_id, sensor_id, event_type).
_DetectorKey = tuple[int, int, EventType]


class DetectionEngine:
    """Stateful coordinator. Single caller only (ingest consumer task)."""

    __slots__ = ("_config_provider", "_sink", "_detectors", "_mode_machine")

    def __init__(
        self,
        config_provider: DetectorConfigProvider,
        sink: EventSink,
    ) -> None:
        self._config_provider = config_provider
        self._sink = sink
        self._detectors: dict[_DetectorKey, SensorDetector] = {}
        self._mode_machine = ModeStateMachine(config_provider)

    def feed_snapshot(self, device_id: int, ts: float, values: dict[int, float]) -> None:
        """
        Feed one timestamp's worth of samples to all applicable detectors.

        ``values`` maps sensor_id (1..12) to the offset-corrected reading.
        Sensor iteration follows dict insertion order; detector iteration
        follows ``_EVENT_TYPE_ORDER`` so tests can assert a deterministic
        sequence and Type D reads Type C's ``current_avg`` for the same
        sample.

        Per-sample flow (with gap 3 mode switching):

            1. Run the mode state machine for this (device, sensor).
               If it emitted a BREAK event, publish it directly to the
               sink (the TTL gate forwards BREAK unchanged).
            2. Feed Type A unconditionally so its variance window stays
               primed during POWER_ON / BREAK. Discard any event Type A
               returns when the sensor is not active — matches legacy.
            3. Skip Types B/C/D entirely when the sensor is not active.
               Their internal windows rebuild after the next STARTUP
               entry.
        """
        device_label = str(device_id)
        # Pre-bind hot-loop locals (Layer 1 discipline). At 24 000
        # samples/s × 12 sensors × 4 detector types = 1.15 M iterations/s,
        # the LOAD_FAST savings are measurable in the bench.
        mode_feed = self._mode_machine.feed
        detector_for = self._detector_for
        sink_publish = self._sink.publish
        events_detected = _m.EVENTS_DETECTED_TOTAL
        type_a_const = EventType.A
        break_label = EventType.BREAK.value
        type_order = _EVENT_TYPE_ORDER

        for sensor_id, value in values.items():
            decision = mode_feed(device_id, sensor_id, value, ts)
            if decision.break_event is not None:
                events_detected.labels(
                    event_type=break_label,
                    device_id=device_label,
                ).inc()
                sink_publish(decision.break_event)

            active = decision.active
            sample = Sample(ts=ts, device_id=device_id, sensor_id=sensor_id, value=value)
            for event_type in type_order:
                # Types B/C/D skip entirely while the sensor isn't
                # active. Type A still feeds; we just suppress firing.
                if not active and event_type is not type_a_const:
                    continue
                detector = detector_for(device_id, sensor_id, event_type)
                event = detector.feed(sample)
                if event is None:
                    continue
                if not active:
                    # Type A only — window stayed primed but the sensor
                    # isn't allowed to fire right now. Drop the event,
                    # matching the legacy short-circuit.
                    continue
                events_detected.labels(
                    event_type=event_type.value,
                    device_id=device_label,
                ).inc()
                sink_publish(event)

    def reset_device(self, device_id: int) -> None:
        """Drop all cached detectors and mode state for ``device_id``."""
        to_drop = [key for key in self._detectors if key[0] == device_id]
        for key in to_drop:
            del self._detectors[key]
        self._mode_machine.reset(device_id)

    def device_ids(self) -> list[int]:
        """All device IDs that currently have at least one cached detector."""
        return list({key[0] for key in self._detectors})

    def _detector_for(
        self, device_id: int, sensor_id: int, event_type: EventType
    ) -> SensorDetector:
        key: _DetectorKey = (device_id, sensor_id, event_type)
        detector = self._detectors.get(key)
        if detector is None:
            detector = self._create(device_id, sensor_id, event_type)
            self._detectors[key] = detector
        return detector

    def _create(self, device_id: int, sensor_id: int, event_type: EventType) -> SensorDetector:
        if event_type is EventType.A:
            return TypeADetector(self._config_provider.type_a_for(device_id, sensor_id))
        if event_type is EventType.B:
            return TypeBDetector(self._config_provider.type_b_for(device_id, sensor_id))
        if event_type is EventType.C:
            return TypeCDetector(self._config_provider.type_c_for(device_id, sensor_id))
        if event_type is EventType.D:
            # D depends on C's ``current_avg``; fetch (or lazily create) C
            # for the same (device, sensor) before constructing D.
            c_detector = self._detector_for(device_id, sensor_id, EventType.C)
            assert isinstance(c_detector, TypeCDetector), (
                "Type C detector slot must hold a TypeCDetector"
            )
            return TypeDDetector(
                self._config_provider.type_d_for(device_id, sensor_id),
                c_detector,
            )
        # BREAK is emitted by ModeStateMachine, not a SensorDetector
        # — this branch should be unreachable.
        raise AssertionError(f"event_type={event_type} has no detector class")
