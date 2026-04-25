"""
End-to-end test for the SessionSampleWriter against a real Postgres.

Covers what the unit tests can't: the asyncpg ``copy_records_to_table``
call path, the ``_refresh_recording_set`` query, and the round-trip
through the ``session_samples`` hypertable. We exercise:

  * A session with ``record_raw_samples=true`` is picked up by the
    refresh loop (or the synchronous initial refresh in start()).
  * Samples buffered from ``push_snapshot`` actually land in the
    table after a flush.
  * Stopping the writer flushes any leftover buffer.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import select

from hermes.db.engine import async_session
from hermes.db.models import Package, Session, SessionScope
from hermes.ingest.session_samples import SessionSampleWriter


def _dsn() -> str:
    """Strip the +asyncpg driver prefix for raw asyncpg.connect."""
    return os.environ["MIGRATE_DATABASE_URL"]


async def _seed_recording_global_session() -> uuid.UUID:
    """Create a default package + GLOBAL session with recording on."""
    async with async_session() as s:
        pkg = Package(name="rec-test", is_default=False)
        s.add(pkg)
        await s.flush()
        sess = Session(
            scope=SessionScope.GLOBAL,
            package_id=pkg.package_id,
            record_raw_samples=True,
            started_by="test",
        )
        s.add(sess)
        await s.commit()
        await s.refresh(sess)
        return uuid.UUID(str(sess.session_id))


@pytest.mark.db
@pytest.mark.asyncio
async def test_writer_persists_samples_against_recording_session() -> None:
    """End-to-end: bring up the writer, push a snapshot, expect rows in the DB."""
    session_id = await _seed_recording_global_session()

    writer = SessionSampleWriter(
        dsn=_dsn(),
        max_buffer=1000,
        flush_interval_s=0.2,
        refresh_interval_s=10.0,  # we don't need the periodic refresh
    )
    await writer.start()
    try:
        # The initial refresh in start() should have picked up the session.
        assert writer.is_recording is True

        # Push two snapshots' worth of samples.
        writer.push_snapshot(device_id=1, ts=1700000000.0, sensor_values={1: 10.0, 2: 20.0})
        writer.push_snapshot(device_id=2, ts=1700000000.5, sensor_values={3: 30.0})

        # Wait for at least one flush cycle.
        await asyncio.sleep(0.5)
    finally:
        await writer.stop()

    # Verify the rows landed.
    async with async_session() as s:
        from hermes.db.models import SessionSample

        rows = (
            (await s.execute(select(SessionSample).where(SessionSample.session_id == session_id)))
            .scalars()
            .all()
        )
        # 2 + 1 = 3 rows total.
        assert len(rows) == 3
        # Verify session_id is the one we seeded.
        assert all(r.session_id == session_id for r in rows)
        # Spot-check one row.
        sensors = {(r.device_id, r.sensor_id): r.value for r in rows}
        assert sensors[(1, 1)] == 10.0
        assert sensors[(1, 2)] == 20.0
        assert sensors[(2, 3)] == 30.0


@pytest.mark.db
@pytest.mark.asyncio
async def test_writer_drops_when_no_recording_active() -> None:
    """No active session has recording on → no buffering, no DB writes."""
    writer = SessionSampleWriter(
        dsn=_dsn(),
        max_buffer=1000,
        flush_interval_s=0.2,
        refresh_interval_s=10.0,
    )
    await writer.start()
    try:
        assert writer.is_recording is False

        writer.push_snapshot(device_id=1, ts=1700000000.0, sensor_values={1: 10.0})
        # Buffer stays empty because no session covers this device.
        assert writer.buffer_size == 0

        await asyncio.sleep(0.3)
    finally:
        await writer.stop()


@pytest.mark.db
@pytest.mark.asyncio
async def test_writer_picks_up_new_recording_after_refresh() -> None:
    """Sessions started AFTER writer.start() are caught by the periodic refresh."""
    writer = SessionSampleWriter(
        dsn=_dsn(),
        max_buffer=1000,
        flush_interval_s=0.2,
        refresh_interval_s=0.3,  # tight so the test stays fast
    )
    await writer.start()
    try:
        assert writer.is_recording is False

        # Now flip recording on.
        await _seed_recording_global_session()
        # Wait a refresh cycle.
        await asyncio.sleep(0.5)
        assert writer.is_recording is True
    finally:
        await writer.stop()


@pytest.mark.db
@pytest.mark.asyncio
async def test_writer_stop_flushes_remaining_buffer() -> None:
    """A graceful stop() drains the in-memory buffer before closing."""
    session_id = await _seed_recording_global_session()

    writer = SessionSampleWriter(
        dsn=_dsn(),
        max_buffer=1000,
        flush_interval_s=60.0,  # essentially never auto-flushes
        refresh_interval_s=10.0,
    )
    await writer.start()
    writer.push_snapshot(device_id=1, ts=1700000000.0, sensor_values={1: 99.0})
    assert writer.buffer_size == 1

    # stop() should drain the single buffered row.
    await writer.stop()

    async with async_session() as s:
        from hermes.db.models import SessionSample

        rows = (
            (await s.execute(select(SessionSample).where(SessionSample.session_id == session_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].value == 99.0


@pytest.mark.db
@pytest.mark.asyncio
async def test_local_session_routes_only_its_device() -> None:
    """LOCAL session's device_id is the only one whose samples land under it."""
    # Seed a global non-recording + a local recording for device 5.
    async with async_session() as s:
        pkg = Package(name="local-rec", is_default=False)
        s.add(pkg)
        await s.flush()
        # Need to seed a device row so the FK on local sessions resolves.
        from hermes.db.models import Device

        s.add(Device(device_id=5, name="dev-5"))
        global_sess = Session(
            scope=SessionScope.GLOBAL,
            package_id=pkg.package_id,
            record_raw_samples=False,
            started_by="test",
        )
        s.add(global_sess)
        await s.flush()
        local_sess = Session(
            scope=SessionScope.LOCAL,
            parent_session_id=global_sess.session_id,
            device_id=5,
            package_id=pkg.package_id,
            record_raw_samples=True,
            started_by="test",
        )
        s.add(local_sess)
        await s.commit()
        await s.refresh(local_sess)
        local_id = uuid.UUID(str(local_sess.session_id))

    writer = SessionSampleWriter(
        dsn=_dsn(),
        max_buffer=1000,
        flush_interval_s=0.2,
        refresh_interval_s=10.0,
    )
    await writer.start()
    try:
        # Push for both device 5 (recording) and device 9 (not).
        writer.push_snapshot(device_id=5, ts=1700000000.0, sensor_values={1: 1.0, 2: 2.0})
        writer.push_snapshot(device_id=9, ts=1700000000.0, sensor_values={1: 99.0})
        await asyncio.sleep(0.5)
    finally:
        await writer.stop()

    async with async_session() as s:
        from hermes.db.models import SessionSample

        rows = (
            (await s.execute(select(SessionSample).where(SessionSample.session_id == local_id)))
            .scalars()
            .all()
        )
        # Only device 5 rows landed.
        assert len(rows) == 2
        assert {r.device_id for r in rows} == {5}
