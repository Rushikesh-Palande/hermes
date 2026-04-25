"""
SlidingWindow invariants. Shared by Type B/C/D detectors.
"""

from __future__ import annotations

from hermes.detection.sliding import SlidingWindow


def test_returns_none_until_warm() -> None:
    win = SlidingWindow(t_seconds=1.0, init_fill_ratio=0.9, expected_sample_rate_hz=100.0)
    # init_threshold = 90; samples under that return None.
    for i in range(50):
        assert win.update(i * 0.01, 5.0) is None
    assert not win.is_warm


def test_returns_mean_once_warm() -> None:
    win = SlidingWindow(t_seconds=1.0, init_fill_ratio=0.9, expected_sample_rate_hz=100.0)
    last = None
    for i in range(100):
        last = win.update(i * 0.01, 50.0)
    assert last == 50.0
    assert win.is_warm


def test_eviction_maintains_running_sum_after_warmup() -> None:
    win = SlidingWindow(t_seconds=1.0, init_fill_ratio=0.9, expected_sample_rate_hz=100.0)
    # Warmup at value 50.
    for i in range(100):
        win.update(i * 0.01, 50.0)
    # Feed new samples at value 60; as old samples evict, mean walks up.
    mean_after_partial = win.update(1.00, 60.0)
    # Just one 60 sample added, no eviction yet (window_start=0.00 evicts
    # only samples with ts<0.00). Mean should still be ~50.
    assert mean_after_partial is not None
    assert 50.0 <= mean_after_partial <= 50.2


def test_data_gap_resets_window() -> None:
    win = SlidingWindow(t_seconds=1.0, init_fill_ratio=0.9, expected_sample_rate_hz=100.0)
    for i in range(100):
        win.update(i * 0.01, 50.0)
    assert win.is_warm

    # 5-second gap — must clear state.
    win.update(6.0, 50.0)
    assert not win.is_warm
    assert win.mean is None


def test_clear_resets_everything() -> None:
    win = SlidingWindow(t_seconds=1.0, expected_sample_rate_hz=100.0)
    for i in range(100):
        win.update(i * 0.01, 50.0)
    win.clear()
    assert not win.is_warm
    assert win.mean is None
    # One more sample shouldn't warm instantly.
    assert win.update(10.0, 50.0) is None


def test_mean_tracks_true_average_after_window_fill() -> None:
    win = SlidingWindow(t_seconds=0.5, init_fill_ratio=0.9, expected_sample_rate_hz=100.0)
    # init_threshold = 45. Feed 100 samples alternating 40 and 60 (mean=50).
    last = None
    for i in range(200):
        v = 60.0 if i % 2 == 0 else 40.0
        last = win.update(i * 0.01, v)
    assert last is not None
    assert abs(last - 50.0) < 0.5  # close to 50
