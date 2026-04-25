"""
End-to-end persistence: detected event lands in events + event_windows.

These tests bypass the MQTT broker and the FastAPI app — they exercise
the components that the IngestPipeline wires together (window buffer,
detection engine, DB sink, session bootstrap) with synthesised samples.
The autouse ``_reset_schema`` fixture in ``tests/integration/conftest.py``
re-applies migrations before each test.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from hermes.db.engine import async_session
from hermes.db.models import Event, EventType, EventWindow, Package, Session, SessionScope
from hermes.detection.config import StaticConfigProvider, TypeAConfig
from hermes.detection.db_sink import DbEventSink
from hermes.detection.encoding import decode_window
from hermes.detection.engine import DetectionEngine
from hermes.detection.session import ensure_default_session
from hermes.detection.window_buffer import EventWindowBuffer


@pytest.mark.db
@pytest.mark.asyncio
async def test_ensure_default_session_creates_package_and_session() -> None:
    session_id = await ensure_default_session()
    assert session_id is not None

    # Verify both rows landed.
    async with async_session() as session:
        pkg = (
            await session.execute(select(Package).where(Package.is_default.is_(True)))
        ).scalar_one()
        sess = (
            await session.execute(select(Session).where(Session.session_id == session_id))
        ).scalar_one()

    assert pkg.is_default is True
    assert sess.scope is SessionScope.GLOBAL
    assert sess.package_id == pkg.package_id
    assert sess.ended_at is None


@pytest.mark.db
@pytest.mark.asyncio
async def test_ensure_default_session_is_idempotent() -> None:
    first = await ensure_default_session()
    second = await ensure_default_session()
    assert first == second


@pytest.mark.db
@pytest.mark.asyncio
async def test_event_round_trip_writes_event_and_window() -> None:
    """
    Feed a high-variance signal through the engine; verify the event row
    and matching event_window row land in the DB with consistent linkage.
    """
    # 1. Bootstrap session + a Device row (events.device_id has a NOT NULL FK).
    session_id = await ensure_default_session()
    device_id = 1
    async with async_session() as session:
        from hermes.db.models import Device

        session.add(Device(device_id=device_id, name="test-device"))

    # 2. Wire the detection pipeline with a tiny post-window so the test
    #    finishes in subsecond time.
    buffer = EventWindowBuffer(buffer_seconds=5.0, expected_rate_hz=100.0)
    sink = DbEventSink(
        session_id=session_id,
        window_buffer=buffer,
        pre_window_seconds=0.5,
        post_window_seconds=0.2,
    )
    await sink.start()

    engine = DetectionEngine(
        StaticConfigProvider(
            type_a=TypeAConfig(
                enabled=True,
                T1=1.0,
                threshold_cv=5.0,
                expected_sample_rate_hz=100.0,
            )
        ),
        sink,
    )

    # 3. Feed samples — start at "now" so triggered_at + post_window is
    #    in the near future (sink will sleep 0.2 s before the DB write).
    base_ts = time.time()
    for i in range(300):
        ts = base_ts + i * 0.01
        v = 60.0 if i % 2 == 0 else 40.0
        snapshot = {1: v}
        buffer.push_snapshot(device_id, ts, snapshot)
        engine.feed_snapshot(device_id, ts, snapshot)

    # 4. Wait for the writer task to flush. 1 s is comfortable beyond
    #    the 0.2 s post-window; in CI we add a small margin.
    await asyncio.sleep(1.0)
    await sink.stop()

    # 5. Verify the rows.
    async with async_session() as session:
        events = (
            (await session.execute(select(Event).where(Event.device_id == device_id)))
            .scalars()
            .all()
        )
        assert len(events) >= 1
        ev = events[0]
        assert ev.event_type is EventType.A
        assert ev.session_id == session_id
        assert ev.window_id is not None  # back-linked

        window = (
            await session.execute(select(EventWindow).where(EventWindow.window_id == ev.window_id))
        ).scalar_one()
        assert window.event_id == ev.event_id
        assert window.sample_count > 0
        decoded = decode_window(window.data, window.encoding)
        assert len(decoded) == window.sample_count
        # All decoded sample timestamps fall inside the declared range.
        assert all(
            window.start_ts.timestamp() <= ts <= window.end_ts.timestamp() for ts, _ in decoded
        )
