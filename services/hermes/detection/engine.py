"""
Detection engine — routes samples to per-sensor detectors.

Responsibilities:
    * Own the per-(device, sensor, event_type) detector instances.
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
      device; it discards the cached detectors so fresh config takes
      effect on the next sample.

Phase 3b/c covers Type A only. Types B/C/D land in Phase 3d — the
registry is already shaped to hold them (the ``EventType`` axis of the
cache key).
"""

from __future__ import annotations

from hermes.db.models import EventType
from hermes.detection.config import DetectorConfigProvider
from hermes.detection.type_a import TypeADetector
from hermes.detection.types import EventSink, Sample, SensorDetector

# A compound key per detector instance: (device_id, sensor_id, event_type).
_DetectorKey = tuple[int, int, EventType]


class DetectionEngine:
    """Stateful coordinator. Single caller only (ingest consumer task)."""

    __slots__ = ("_config_provider", "_sink", "_detectors")

    def __init__(
        self,
        config_provider: DetectorConfigProvider,
        sink: EventSink,
    ) -> None:
        self._config_provider = config_provider
        self._sink = sink
        self._detectors: dict[_DetectorKey, SensorDetector] = {}

    def feed_snapshot(self, device_id: int, ts: float, values: dict[int, float]) -> None:
        """
        Feed one timestamp's worth of samples to all applicable detectors.

        ``values`` maps sensor_id (1..12) to the offset-corrected reading.
        Iteration order follows dict insertion (Python 3.7+) so tests can
        assert a deterministic sequence.
        """
        for sensor_id, value in values.items():
            sample = Sample(ts=ts, device_id=device_id, sensor_id=sensor_id, value=value)
            # Phase 3b/c: Type A only. Extend loop to B/C/D in Phase 3d.
            detector = self._detector_for(device_id, sensor_id, EventType.A)
            event = detector.feed(sample)
            if event is not None:
                self._sink.publish(event)

    def reset_device(self, device_id: int) -> None:
        """Drop all cached detectors for ``device_id``. Config reload hook."""
        to_drop = [key for key in self._detectors if key[0] == device_id]
        for key in to_drop:
            del self._detectors[key]

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
        raise NotImplementedError(f"detector for {event_type} lands in Phase 3d")
