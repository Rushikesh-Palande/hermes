"""
Unit tests for ``SessionSampleWriter`` hot-path semantics (gap 6).

These tests exercise the in-memory parts only — buffer fill/swap,
session resolution, drop-on-overflow accounting. The DB-touching
parts (``copy_records_to_table``, ``_refresh_recording_set``) are
covered separately by an integration test that hits a real Postgres.

We bypass ``start()`` and inject the recording-set caches directly so
tests run in milliseconds without an asyncpg connection.
"""

from __future__ import annotations

import uuid

from hermes import metrics as _m
from hermes.ingest.session_samples import SessionSampleWriter


def _writer(*, max_buffer: int = 1000) -> SessionSampleWriter:
    return SessionSampleWriter(dsn="postgresql://nowhere", max_buffer=max_buffer)


def _values(n: int = 12) -> dict[int, float]:
    return {sid: float(sid) for sid in range(1, n + 1)}


def test_no_recording_session_drops_silently() -> None:
    """When no global/local session has recording on, push is a no-op."""
    w = _writer()
    w.push_snapshot(device_id=1, ts=10.0, sensor_values=_values())
    assert w.buffer_size == 0
    assert w.is_recording is False


def test_global_recording_captures_every_device() -> None:
    """A GLOBAL session covers all devices."""
    w = _writer()
    gid = uuid.uuid4()
    w._global_session_id = gid

    w.push_snapshot(device_id=1, ts=10.0, sensor_values=_values())
    w.push_snapshot(device_id=2, ts=10.0, sensor_values=_values())
    assert w.buffer_size == 24
    # Every row references the GLOBAL session id.
    assert all(row[0] == gid for row in w._buffer)


def test_local_session_overrides_global_for_its_device() -> None:
    """LOCAL session for device 7 takes precedence over the GLOBAL."""
    w = _writer()
    gid = uuid.uuid4()
    lid = uuid.uuid4()
    w._global_session_id = gid
    w._local_sessions = {7: lid}

    w.push_snapshot(device_id=7, ts=10.0, sensor_values=_values())
    w.push_snapshot(device_id=8, ts=10.0, sensor_values=_values())

    by_device = {row[1]: row[0] for row in w._buffer}
    assert by_device[7] == lid
    assert by_device[8] == gid


def test_local_only_no_global_skips_other_devices() -> None:
    """LOCAL with no GLOBAL records ONLY that device."""
    w = _writer()
    lid = uuid.uuid4()
    w._local_sessions = {7: lid}

    w.push_snapshot(device_id=7, ts=10.0, sensor_values=_values())
    w.push_snapshot(device_id=8, ts=10.0, sensor_values=_values())

    assert w.buffer_size == 12  # only device 7
    assert all(row[1] == 7 for row in w._buffer)


def test_buffer_overflow_drops_excess_rows() -> None:
    """When the buffer is full, excess rows drop and the metric ticks."""
    w = _writer(max_buffer=15)
    w._global_session_id = uuid.uuid4()
    before = _m.counter_value(_m.SESSION_SAMPLES_DROPPED_TOTAL)

    # First snapshot of 12 fills 12/15.
    w.push_snapshot(device_id=1, ts=10.0, sensor_values=_values())
    assert w.buffer_size == 12

    # Second snapshot of 12 only has 3 slots left; 9 should drop.
    w.push_snapshot(device_id=2, ts=10.0, sensor_values=_values())
    assert w.buffer_size == 15  # capped

    after = _m.counter_value(_m.SESSION_SAMPLES_DROPPED_TOTAL)
    assert after - before == 9


def test_full_buffer_drops_entire_snapshot() -> None:
    """A snapshot arriving against a full buffer drops every row."""
    w = _writer(max_buffer=5)
    w._global_session_id = uuid.uuid4()

    # Fill buffer to capacity using two snapshots.
    w.push_snapshot(device_id=1, ts=10.0, sensor_values=_values(n=5))
    assert w.buffer_size == 5

    before = _m.counter_value(_m.SESSION_SAMPLES_DROPPED_TOTAL)
    # Now full — next snapshot of 12 should drop entirely.
    w.push_snapshot(device_id=1, ts=10.1, sensor_values=_values())
    after = _m.counter_value(_m.SESSION_SAMPLES_DROPPED_TOTAL)
    assert w.buffer_size == 5
    assert after - before == 12


def test_recording_state_flag_reflects_caches() -> None:
    w = _writer()
    assert w.is_recording is False

    w._global_session_id = uuid.uuid4()
    assert w.is_recording is True

    w._global_session_id = None
    w._local_sessions = {3: uuid.uuid4()}
    assert w.is_recording is True

    w._local_sessions = {}
    assert w.is_recording is False


def test_row_tuple_shape_matches_session_samples_columns() -> None:
    """Row tuples must align with copy_records_to_table column order."""
    w = _writer()
    gid = uuid.uuid4()
    w._global_session_id = gid

    w.push_snapshot(device_id=42, ts=1700000000.5, sensor_values={3: 12.5})
    assert w.buffer_size == 1
    row = w._buffer[0]
    # Order: (session_id, device_id, sensor_id, ts, value)
    assert row[0] == gid
    assert row[1] == 42
    assert row[2] == 3
    assert row[3].timestamp() == 1700000000.5
    assert row[3].tzinfo is not None
    assert row[4] == 12.5


def test_metrics_queue_depth_gauge_tracks_buffer() -> None:
    """The QUEUE_DEPTH gauge updates after each push_snapshot."""
    w = _writer()
    w._global_session_id = uuid.uuid4()
    w.push_snapshot(device_id=1, ts=10.0, sensor_values=_values())
    assert _m.gauge_value(_m.SESSION_SAMPLES_QUEUE_DEPTH) == 12.0
    w.push_snapshot(device_id=2, ts=10.0, sensor_values=_values())
    assert _m.gauge_value(_m.SESSION_SAMPLES_QUEUE_DEPTH) == 24.0
