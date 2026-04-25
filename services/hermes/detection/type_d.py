"""
Type D detector — two-stage averaging on avg_T5, compared to avg_T3.

Fire condition (legacy parity, EVENT_DETECTION_CONTRACT §6):

    Stage 1: avg_T4(t) = rolling mean of raw samples over last T4 s.
    Stage 2: at each elapsed wall-clock second `sec`, average the avg_T4
             values whose timestamp fell in [sec, sec+1); append the
             result to ``_one_sec_averages``.
    Stage 3: avg_T5 = mean of the last T5 entries in ``_one_sec_averages``.
    Band:    lower = avg_T5 − (REF_VALUE × tol_pct)/100
             upper = avg_T5 + (REF_VALUE × tol_pct)/100  (symmetric)
    Test:    fire when ``current_avg_t3`` (from paired Type C) is
             outside the band.

Coupling to Type C:
    The detector takes a ``TypeCDetector`` reference at construction;
    it reads ``type_c.current_avg`` once per sample as the comparison
    value. The engine guarantees Type C runs before Type D for the same
    sample (deterministic order in ``_EVENT_TYPE_ORDER``).

Warmup:
    Effective warmup ≈ T4 + T5 seconds. Stage 1 needs T4 × rate × 0.9
    samples, Stage 2 needs at least one elapsed wall-clock second AFTER
    Stage 1 warms, Stage 3 needs T5 entries in ``_one_sec_averages``.
    All three suppress fires until satisfied.

Data-gap reset:
    Inherited from ``SlidingWindow.update`` for Stage 1; if the gap
    exceeds 2 s the T4 window resets, which transitively delays the
    next Stage 2 emission. Stage 2 + 3 state is also cleared.
"""

from __future__ import annotations

from collections import deque

from hermes.db.models import EventType
from hermes.detection.config import REF_VALUE, TypeDConfig
from hermes.detection.sliding import SlidingWindow
from hermes.detection.type_c import TypeCDetector
from hermes.detection.types import DetectedEvent, Sample


class TypeDDetector:
    """Per-sensor Type D detector. Not thread-safe; single caller only."""

    __slots__ = (
        "_config",
        "_type_c",
        "_t4_window",
        "_avg_t4_buffer",
        "_one_sec_averages",
        "_t5_initialized",
        "_avg_t5_cached",
        "_last_completed_second",
        "_debounce_start",
    )

    def __init__(self, config: TypeDConfig, type_c: TypeCDetector) -> None:
        self._config = config
        self._type_c = type_c
        self._t4_window = SlidingWindow(
            t_seconds=config.T4,
            init_fill_ratio=config.init_fill_ratio,
            expected_sample_rate_hz=config.expected_sample_rate_hz,
        )
        # Buffer of (ts, avg_T4) for the current and a few previous seconds.
        # 3 s × expected rate is generous enough that we never overflow
        # before bucketing — ~300 entries at 100 Hz.
        buf_max = max(2, int(config.expected_sample_rate_hz * 3.0))
        self._avg_t4_buffer: deque[tuple[float, float]] = deque(maxlen=buf_max)
        # Deque of (sec, one_sec_avg). Sized at 2× T5 with a 60-entry floor
        # so ``_avg_t5_cached`` always has the full T5 window available.
        slots = max(int(config.T5) * 2, 60)
        self._one_sec_averages: deque[tuple[int, float]] = deque(maxlen=slots)
        self._t5_initialized: bool = False
        self._avg_t5_cached: float | None = None
        self._last_completed_second: int | None = None
        self._debounce_start: float | None = None

    def feed(self, sample: Sample) -> DetectedEvent | None:
        config = self._config
        if not config.enabled:
            return None

        ts = sample.ts

        # ── Stage 1: rolling T4 average over raw samples ──
        avg_t4 = self._t4_window.update(ts, sample.value)
        if avg_t4 is None:
            return None
        self._avg_t4_buffer.append((ts, avg_t4))

        # ── Stage 2: bucket avg_T4 by completed wall-clock second ──
        current_second = int(ts)
        if self._last_completed_second is None:
            # Anchor so the first completed second is the one BEFORE
            # current — never bucket the in-progress second.
            self._last_completed_second = current_second - 1

        for sec in range(self._last_completed_second + 1, current_second):
            entries: list[float] = []
            # Pop from the front until we hit an entry past `sec`.
            while self._avg_t4_buffer and self._avg_t4_buffer[0][0] < sec + 1:
                ts_entry, val = self._avg_t4_buffer.popleft()
                # Guard against entries strictly before `sec` (only happens
                # right after a data gap; harmless to drop).
                if ts_entry >= sec:
                    entries.append(val)
            if entries:
                self._add_one_sec_avg(sec, sum(entries) / len(entries))
        self._last_completed_second = current_second - 1

        # ── Stage 3: cached avg_T5 ──
        if not self._t5_initialized or self._avg_t5_cached is None:
            return None
        avg_t5 = self._avg_t5_cached

        # ── Pair with Type C's avg_T3 ──
        avg_t3 = self._type_c.current_avg
        if avg_t3 is None:
            return None

        tol = config.tolerance_pct
        lower = avg_t5 - (REF_VALUE * tol) / 100.0
        upper = avg_t5 + (REF_VALUE * tol) / 100.0
        out_of_range = avg_t3 < lower or avg_t3 > upper

        if not out_of_range:
            self._debounce_start = None
            return None

        if self._debounce_start is None:
            self._debounce_start = ts

        if ts - self._debounce_start < config.debounce_seconds:
            return None

        triggered_at = self._debounce_start
        self._debounce_start = None
        return DetectedEvent(
            event_type=EventType.D,
            device_id=sample.device_id,
            sensor_id=sample.sensor_id,
            triggered_at=triggered_at,
            metadata={
                "avg_T3": avg_t3,
                "avg_T4": avg_t4,
                "avg_T5": avg_t5,
                "lower_bound": lower,
                "upper_bound": upper,
                "tolerance_pct": tol,
            },
        )

    def reset(self) -> None:
        self._t4_window.clear()
        self._avg_t4_buffer.clear()
        self._one_sec_averages.clear()
        self._t5_initialized = False
        self._avg_t5_cached = None
        self._last_completed_second = None
        self._debounce_start = None

    def _add_one_sec_avg(self, sec: int, val: float) -> None:
        """Append a per-second average; refresh the avg_T5 cache."""
        self._one_sec_averages.append((sec, val))
        t5 = int(self._config.T5)
        if not self._t5_initialized and len(self._one_sec_averages) >= t5:
            self._t5_initialized = True
        if self._t5_initialized:
            recent = list(self._one_sec_averages)[-t5:]
            self._avg_t5_cached = sum(v for _, v in recent) / len(recent)
