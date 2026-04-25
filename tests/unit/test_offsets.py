"""
OffsetCache invariants.

Covers:
    * No-op pass-through when no offsets configured.
    * Subtraction formula: corrected = raw - offset.
    * Zero offsets are dropped (don't appear in the cache).
    * load() replaces atomically.
"""

from __future__ import annotations

from hermes.ingest.offsets import OffsetCache


def test_no_offsets_returns_input_unchanged() -> None:
    cache = OffsetCache()
    values = {1: 100.0, 2: 200.0}
    assert cache.apply(device_id=1, sensor_values=values) is values


def test_offset_subtracts_from_raw() -> None:
    cache = OffsetCache()
    cache.load(device_id=1, offsets={1: 5.0, 2: -3.0})
    result = cache.apply(device_id=1, sensor_values={1: 100.0, 2: 200.0})
    assert result == {1: 95.0, 2: 203.0}


def test_missing_sensor_offset_defaults_to_zero() -> None:
    cache = OffsetCache()
    cache.load(device_id=1, offsets={1: 5.0})  # sensor 2 has no offset
    result = cache.apply(device_id=1, sensor_values={1: 100.0, 2: 200.0})
    assert result == {1: 95.0, 2: 200.0}


def test_zero_offsets_are_dropped_from_cache() -> None:
    cache = OffsetCache()
    cache.load(device_id=1, offsets={1: 0.0, 2: 0.0})
    # All-zero → device absent from cache → pass-through.
    assert not cache.has_offsets(device_id=1)
    values = {1: 100.0}
    assert cache.apply(device_id=1, sensor_values=values) is values


def test_load_replaces_existing_entry() -> None:
    cache = OffsetCache()
    cache.load(device_id=1, offsets={1: 5.0})
    cache.load(device_id=1, offsets={1: 10.0, 2: 20.0})
    result = cache.apply(device_id=1, sensor_values={1: 100.0, 2: 200.0})
    assert result == {1: 90.0, 2: 180.0}


def test_reloading_with_all_zero_removes_device() -> None:
    cache = OffsetCache()
    cache.load(device_id=1, offsets={1: 5.0})
    assert cache.has_offsets(device_id=1)
    cache.load(device_id=1, offsets={1: 0.0})
    assert not cache.has_offsets(device_id=1)


def test_other_device_unaffected() -> None:
    cache = OffsetCache()
    cache.load(device_id=1, offsets={1: 5.0})
    # Device 2 has no offsets configured → apply is a pass-through.
    values = {1: 100.0}
    assert cache.apply(device_id=2, sensor_values=values) is values
