"""
Shared O(1) sliding time-window with running sum.

Type B (post-window deviation), Type C (range-on-avg), and Type D
(two-stage averaging) all need a rolling mean over a T-second window.
Each has the same legacy-parity semantics:

    * Pop samples older than ``ts - T`` before admitting the new one.
    * While warming up (``window_count < init_threshold``), evictions
      from the deque do NOT subtract from the running sum — this is
      how the first full window is primed.
    * Data-gap > 2 s resets the window entirely.
    * The rolling mean is undefined until the window is both warm and
      has at least two samples.

Type A uses a separate specialised version because it also tracks a
running sum of squares for the CV% identity (E[X²] − E[X]²).
"""

from __future__ import annotations

from collections import deque

# Inter-sample gap beyond this (seconds) clears the window on the next
# arrival. Legacy parity.
_DATA_GAP_RESET_S: float = 2.0


class SlidingWindow:
    """
    Time-based sliding window with a running sum. O(1) per update.

    Not thread-safe — single caller only (ingest consumer task).
    """

    __slots__ = (
        "_t_seconds",
        "_window",
        "_sum",
        "_count",
        "_initialized",
        "_init_threshold",
        "_last_ts",
    )

    def __init__(
        self,
        t_seconds: float,
        init_fill_ratio: float = 0.9,
        expected_sample_rate_hz: float = 100.0,
    ) -> None:
        self._t_seconds = t_seconds

        # 1.5× headroom on the deque so high-rate bursts don't evict by size.
        maxlen = max(2, int(t_seconds * expected_sample_rate_hz * 1.5))
        self._window: deque[tuple[float, float]] = deque(maxlen=maxlen)

        self._sum: float = 0.0
        self._count: int = 0
        self._initialized: bool = False
        self._init_threshold: int = max(
            2, int(t_seconds * expected_sample_rate_hz * init_fill_ratio)
        )
        self._last_ts: float | None = None

    def update(self, ts: float, value: float) -> float | None:
        """
        Admit ``(ts, value)``; return the rolling mean if warm, else None.

        ``None`` is returned in two cases: (a) the window has not yet
        reached ``init_threshold`` samples, or (b) a data-gap just reset
        the window and only one sample has been admitted since.
        """
        if self._last_ts is not None and ts - self._last_ts > _DATA_GAP_RESET_S:
            self.clear()
        self._last_ts = ts

        # Evict samples outside the window. Subtract only after warmup.
        window_start = ts - self._t_seconds
        while self._window and self._window[0][0] < window_start:
            _, old_val = self._window.popleft()
            if self._initialized:
                self._sum -= old_val
                self._count -= 1

        # Admit new sample.
        self._window.append((ts, value))
        self._sum += value
        self._count += 1

        if not self._initialized and self._count >= self._init_threshold:
            self._initialized = True

        if not self._initialized or self._count < 2:
            return None
        return self._sum / self._count

    @property
    def mean(self) -> float | None:
        """Latest rolling mean, or None if not yet warm."""
        if not self._initialized or self._count < 2:
            return None
        return self._sum / self._count

    @property
    def is_warm(self) -> bool:
        """True once enough samples have landed to start emitting means."""
        return self._initialized and self._count >= 2

    def clear(self) -> None:
        """Drop all state. Called on data-gap reset or external reset."""
        self._window.clear()
        self._sum = 0.0
        self._count = 0
        self._initialized = False
        self._last_ts = None
