"""
Type B detector — post-window deviation.

Fire condition (legacy parity, EVENT_DETECTION_CONTRACT §4):

    avg_T2(t) = mean of samples in (t − T2, t]
    lower    = avg_T2 − (REF_VALUE × lower_threshold_pct) / 100
    upper    = avg_T2 + (REF_VALUE × upper_threshold_pct) / 100
    fire when value < lower OR value > upper

Unlike Type A, Type B compares the LATEST sample against a band, not a
statistic of the window. The rolling mean is just the band's centre.

Sliding window + warmup + data-gap semantics come from SlidingWindow.
Debounce semantics match A and C: first crossing arms ``_debounce_start``,
fire is delayed until ``debounce_seconds`` elapses, and the fired event
carries the original crossing timestamp (not the fire time).
"""

from __future__ import annotations

from hermes.db.models import EventType
from hermes.detection.config import REF_VALUE, TypeBConfig
from hermes.detection.sliding import SlidingWindow
from hermes.detection.types import DetectedEvent, Sample


class TypeBDetector:
    """Per-sensor Type B detector. Not thread-safe; single caller only."""

    __slots__ = ("_config", "_window", "_debounce_start", "_current_avg")

    def __init__(self, config: TypeBConfig) -> None:
        self._config = config
        self._window = SlidingWindow(
            t_seconds=config.T2,
            init_fill_ratio=config.init_fill_ratio,
            expected_sample_rate_hz=config.expected_sample_rate_hz,
        )
        self._debounce_start: float | None = None
        # Exposed for the UI / future event metadata. Matches legacy's
        # ``AvgTypeB.current_avg``.
        self._current_avg: float | None = None

    @property
    def current_avg(self) -> float | None:
        """Latest computed avg_T2; None until the window is warm."""
        return self._current_avg

    def feed(self, sample: Sample) -> DetectedEvent | None:
        config = self._config
        if not config.enabled:
            return None

        avg_t2 = self._window.update(sample.ts, sample.value)
        if avg_t2 is None:
            # Warmup or data-gap — compute stats stay primed but no fire.
            self._current_avg = None
            return None
        self._current_avg = avg_t2

        lower = avg_t2 - (REF_VALUE * config.lower_threshold_pct) / 100.0
        upper = avg_t2 + (REF_VALUE * config.upper_threshold_pct) / 100.0
        out_of_range = sample.value < lower or sample.value > upper

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
            event_type=EventType.B,
            device_id=sample.device_id,
            sensor_id=sample.sensor_id,
            triggered_at=triggered_at,
            metadata={
                "avg_T2": avg_t2,
                "lower_bound": lower,
                "upper_bound": upper,
                "trigger_value": sample.value,
                "window_seconds": config.T2,
            },
        )

    def reset(self) -> None:
        self._window.clear()
        self._debounce_start = None
        self._current_avg = None
