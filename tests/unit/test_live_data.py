"""
LiveDataHub invariants.

The hub is single-threaded (asyncio event-loop only), so the tests are
plain sync functions that exercise the deque semantics.
"""

from __future__ import annotations

from hermes.ingest.live_data import LiveDataHub


def test_push_and_since_returns_all_without_cursor() -> None:
    hub = LiveDataHub(maxlen=10)
    hub.push(device_id=1, ts=1.0, values={1: 100.0})
    hub.push(device_id=1, ts=2.0, values={1: 101.0})
    snaps = hub.since(1)
    assert [s.ts for s in snaps] == [1.0, 2.0]
    assert snaps[0].values == {1: 100.0}


def test_since_filters_strictly_after_cursor() -> None:
    hub = LiveDataHub()
    hub.push(1, 1.0, {1: 10.0})
    hub.push(1, 2.0, {1: 20.0})
    hub.push(1, 3.0, {1: 30.0})
    snaps = hub.since(1, after_ts=2.0)
    assert [s.ts for s in snaps] == [3.0]  # strictly >, not >=


def test_ringbuffer_evicts_oldest_at_maxlen() -> None:
    hub = LiveDataHub(maxlen=3)
    for i in range(5):
        hub.push(1, float(i), {1: float(i)})
    snaps = hub.since(1)
    assert [s.ts for s in snaps] == [2.0, 3.0, 4.0]


def test_unknown_device_returns_empty_list() -> None:
    hub = LiveDataHub()
    assert hub.since(42) == []
    assert hub.latest_ts(42) is None


def test_devices_lists_only_pushed_devices() -> None:
    hub = LiveDataHub()
    hub.push(1, 1.0, {1: 10.0})
    hub.push(2, 1.0, {1: 10.0})
    assert sorted(hub.devices()) == [1, 2]


def test_latest_ts_tracks_most_recent_push() -> None:
    hub = LiveDataHub()
    hub.push(1, 5.0, {1: 10.0})
    hub.push(1, 6.0, {1: 11.0})
    assert hub.latest_ts(1) == 6.0
