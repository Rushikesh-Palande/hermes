"""
Type C detector — range-based on avg_T3.

Fire condition (legacy parity, EVENT_DETECTION_CONTRACT §5):

    avg_T3(t) = mean of samples in (t − T3, t]
    fire when avg_T3 < threshold_lower OR avg_T3 > threshold_upper

Thresholds are absolute sensor units (not percentages) — e.g. 40.0 and
60.0 for a sensor that should stay in [40, 60]. Type D reads this
detector's ``current_avg`` each tick and uses it as its comparison
value, so keeping ``current_avg`` up-to-date is part of the contract
even outside the fire path.

Sliding window + warmup + data-gap + debounce semantics mirror Type B.
"""

from __future__ import annotations

from hermes.db.models import EventType
from hermes.detection.config import TypeCConfig
from hermes.detection.sliding import SlidingWindow
from hermes.detection.types import DetectedEvent, Sample


class TypeCDetector:
    """Per-sensor Type C detector. Not thread-safe; single caller only."""

    __slots__ = ("_config", "_window", "_debounce_start", "_current_avg")

    def __init__(self, config: TypeCConfig) -> None:
        self._config = config
        self._window = SlidingWindow(
            t_seconds=config.T3,
            init_fill_ratio=config.init_fill_ratio,
            expected_sample_rate_hz=config.expected_sample_rate_hz,
        )
        self._debounce_start: float | None = None
        self._current_avg: float | None = None

    @property
    def current_avg(self) -> float | None:
        """Latest computed avg_T3. Read by the Type D detector."""
        return self._current_avg

    def feed(self, sample: Sample) -> DetectedEvent | None:
        config = self._config
        if not config.enabled:
            # Keep ``_current_avg`` updated even when disabled so Type D
            # (which depends on it) still has a signal to work with.
            self._current_avg = self._window.update(sample.ts, sample.value)
            return None

        avg_t3 = self._window.update(sample.ts, sample.value)
        if avg_t3 is None:
            self._current_avg = None
            return None
        self._current_avg = avg_t3

        out_of_range = avg_t3 < config.threshold_lower or avg_t3 > config.threshold_upper

        if not out_of_range:
            self._debounce_start = None
            return None

        if self._debounce_start is None:
            self._debounce_start = sample.ts

        if sample.ts - self._debounce_start < config.debounce_seconds:
            return None

        triggered_at = self._debounce_start
        self._debounce_start = None
        return DetectedEvent(
            event_type=EventType.C,
            device_id=sample.device_id,
            sensor_id=sample.sensor_id,
            triggered_at=triggered_at,
            metadata={
                "avg_T3": avg_t3,
                "threshold_lower": config.threshold_lower,
                "threshold_upper": config.threshold_upper,
                "trigger_value": sample.value,
                "window_seconds": config.T3,
            },
        )

    def reset(self) -> None:
        self._window.clear()
        self._debounce_start = None
        self._current_avg = None
