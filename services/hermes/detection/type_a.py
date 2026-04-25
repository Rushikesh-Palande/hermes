"""
Type A detector — variance / CV% incremental sliding window.

Fire condition (legacy parity, EVENT_DETECTION_CONTRACT §3):

    CV%(t) = (sqrt(max(0, E[X²] - E[X]²)) / max(|mean|, 1e-9)) × 100
    fire when CV% > threshold_cv  over samples in (t − T1, t]

Per-sample cost is O(1): a running sum and a running sum of squares
are maintained; on eviction each stat is decremented by the outgoing
sample. Variance is derived by the population identity
``Var = E[X²] − E[X]²`` at read time.

Warmup quirk (preserved exactly from legacy — see contract §3.2 note):
    While ``window_count < init_threshold`` (i.e. ``initialized == False``),
    evictions from the deque DO NOT subtract from the running sums. This
    is how legacy primes the first full window after a cold start. Once
    ``initialized`` flips True, normal slide semantics apply. Changing
    this behaviour changes the first ~T1 seconds of fire decisions vs.
    legacy and breaks golden-traffic parity — do not "fix".

Debounce (contract §3.4):
    * First crossing of the threshold records ``_debounce_start = ts``.
    * If CV% stays above threshold for at least ``debounce_seconds``,
      the detector fires with ``triggered_at = _debounce_start`` — i.e.
      the ORIGINAL crossing timestamp, not the fire time.
    * Signal returning below threshold silently resets ``_debounce_start``.
    * After firing, ``_debounce_start`` clears so a sustained violation
      re-arms on the next sample; rate-limiting of repeat fires lives
      in the TTL layer (Phase 3e), not here.

Data-gap reset (contract §2.4):
    If the inter-sample interval exceeds 2.0 s, all window state is
    cleared — a long gap usually means a broker disconnect or device
    reboot; the old stats are no longer representative.
"""

from __future__ import annotations

import math
from collections import deque

from hermes.db.models import EventType
from hermes.detection.config import TypeAConfig
from hermes.detection.types import DetectedEvent, Sample

# Legacy-compatible data-gap threshold. Above this inter-sample gap in
# seconds, the window is cleared on the next arrival.
_DATA_GAP_RESET_S: float = 2.0

# Floor applied to |mean| in the CV denominator so a zero-mean window
# returns a bounded CV rather than a division error.
_MEAN_EPSILON: float = 1e-9


class TypeADetector:
    """Per-sensor Type A detector. Not thread-safe; single caller only."""

    __slots__ = (
        "_config",
        "_window",
        "_running_sum",
        "_running_sum_sq",
        "_window_count",
        "_initialized",
        "_init_threshold",
        "_last_ts",
        "_debounce_start",
    )

    def __init__(self, config: TypeAConfig) -> None:
        self._config = config

        # Deque maxlen is a soft bound on memory; sliding eviction is
        # driven by timestamps, not count. The 1.5× headroom tolerates
        # bursts and sample-rate drift up to ~150 Hz on a 100 Hz budget.
        maxlen = max(2, int(config.T1 * config.expected_sample_rate_hz * 1.5))
        self._window: deque[tuple[float, float]] = deque(maxlen=maxlen)

        self._running_sum: float = 0.0
        self._running_sum_sq: float = 0.0
        self._window_count: int = 0
        self._initialized: bool = False
        self._init_threshold: int = max(
            2, int(config.T1 * config.expected_sample_rate_hz * config.init_fill_ratio)
        )
        self._last_ts: float | None = None
        self._debounce_start: float | None = None

    def feed(self, sample: Sample) -> DetectedEvent | None:
        config = self._config
        if not config.enabled:
            return None

        ts = sample.ts
        value = sample.value

        # Data-gap: a long pause invalidates the running window.
        if self._last_ts is not None and ts - self._last_ts > _DATA_GAP_RESET_S:
            self._clear_window()
        self._last_ts = ts

        # Evict samples outside (ts - T1, ts]. During warmup we pop from
        # the deque but leave the running sums intact (see module doc).
        window_start = ts - config.T1
        while self._window and self._window[0][0] < window_start:
            _, old_val = self._window.popleft()
            if self._initialized:
                self._running_sum -= old_val
                self._running_sum_sq -= old_val * old_val
                self._window_count -= 1

        # Admit new sample.
        self._window.append((ts, value))
        self._running_sum += value
        self._running_sum_sq += value * value
        self._window_count += 1

        # Flip to "warm" once we've accumulated enough samples.
        if not self._initialized and self._window_count >= self._init_threshold:
            self._initialized = True

        # Need at least two samples to have non-zero variance.
        if self._window_count < 2:
            return None

        n = self._window_count
        mean = self._running_sum / n
        # max(0) guards against tiny negative variance from FP rounding.
        var = max(0.0, self._running_sum_sq / n - mean * mean)
        std = math.sqrt(var)
        cv_pct = (std / max(abs(mean), _MEAN_EPSILON)) * 100.0

        # Before warmup complete: compute the stats (so buffers stay
        # primed) but suppress firing. Contract §3.3.
        if not self._initialized:
            return None

        crossing = cv_pct > config.threshold_cv

        if not crossing:
            # Silent reset of the debounce timer.
            self._debounce_start = None
            return None

        # Arm or continue the debounce window.
        if self._debounce_start is None:
            self._debounce_start = ts

        if ts - self._debounce_start < config.debounce_seconds:
            return None

        # Fire. Original crossing timestamp goes onto the event.
        triggered_at = self._debounce_start
        self._debounce_start = None
        return DetectedEvent(
            event_type=EventType.A,
            device_id=sample.device_id,
            sensor_id=sample.sensor_id,
            triggered_at=triggered_at,
            metadata={
                "cv_percent": cv_pct,
                "average": mean,
                "std": std,
                "window_seconds": config.T1,
                "n_samples": n,
            },
        )

    def reset(self) -> None:
        """Clear all state. Called on config reload or sensor re-init."""
        self._clear_window()
        self._last_ts = None
        self._debounce_start = None

    def _clear_window(self) -> None:
        self._window.clear()
        self._running_sum = 0.0
        self._running_sum_sq = 0.0
        self._window_count = 0
        self._initialized = False
