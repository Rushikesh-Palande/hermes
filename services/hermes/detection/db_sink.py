"""
Persistent event sink: writes detected events + ±9 s windows to Postgres.

Design:

    Sync ``publish(event)`` — called from the detection engine on the
        ingest consumer task. Pushes the event onto an asyncio.Queue
        and returns immediately. Detection MUST NOT wait on a DB write.

    Async ``_writer_loop`` task — drains the queue. For each event:

        1. Wait until the post-window deadline ``triggered_at + post_seconds``
           so any samples arriving up to ±9 s after the trigger have a
           chance to land in the EventWindowBuffer.
        2. Slice the buffer for that sensor over [triggered_at − pre,
           triggered_at + post] and encode it.
        3. Insert the ``events`` row, then the ``event_windows`` row,
           then UPDATE the event row's ``window_id`` so reads can join
           in either direction. All three statements run in one
           transaction.

The schema does not declare an FK between events.window_id and
event_windows.window_id, so the chicken-and-egg problem (each row's
PK is needed by the other) is handled by inserting events first
without window_id, then back-filling.

Failure handling: any DB error is logged and the event is dropped.
There is no retry queue — preserving 100% event delivery vs. handling
back-pressure cleanly is a Phase 4+ trade-off; for now we surface drops
through metrics.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import update

from hermes import metrics as _m
from hermes.db.engine import async_session
from hermes.db.models import Event, EventWindow
from hermes.detection.encoding import encode_window
from hermes.detection.types import DetectedEvent
from hermes.detection.window_buffer import EventWindowBuffer
from hermes.logging import get_logger

_log = get_logger(__name__, component="detection")

# Default window halves match the legacy 18 s total (±9 s).
DEFAULT_PRE_WINDOW_S: float = 9.0
DEFAULT_POST_WINDOW_S: float = 9.0


class DbEventSink:
    """
    Async-write sink that persists events to Postgres with their windows.

    Construct with a session UUID (from ``ensure_default_session``) and a
    shared ``EventWindowBuffer`` (the same instance the ingest pipeline
    pushes samples into). Call ``start()`` after construction to spawn
    the writer task; ``stop()`` on shutdown to drain.
    """

    def __init__(
        self,
        session_id: uuid.UUID,
        window_buffer: EventWindowBuffer,
        pre_window_seconds: float = DEFAULT_PRE_WINDOW_S,
        post_window_seconds: float = DEFAULT_POST_WINDOW_S,
        sample_rate_hz: float = 123.0,
    ) -> None:
        self._session_id = session_id
        self._buffer = window_buffer
        self._pre = pre_window_seconds
        self._post = post_window_seconds
        self._sample_rate = sample_rate_hz
        self._queue: asyncio.Queue[DetectedEvent | None] = asyncio.Queue()
        self._writer_task: asyncio.Task[None] | None = None

    # ─── Sink protocol ─────────────────────────────────────────────

    def publish(self, event: DetectedEvent) -> None:
        """Sync entry point — never blocks. Drops on full queue (unbounded by default)."""
        try:
            self._queue.put_nowait(event)
            _m.DB_WRITER_PENDING.set(self._queue.qsize())
        except asyncio.QueueFull:
            _log.warning(
                "event_queue_full_dropped",
                event_type=event.event_type.value,
                device_id=event.device_id,
                sensor_id=event.sensor_id,
            )

    # ─── Lifecycle ─────────────────────────────────────────────────

    async def start(self) -> None:
        if self._writer_task is not None:
            return
        self._writer_task = asyncio.create_task(self._writer_loop(), name="event-writer")

    async def stop(self) -> None:
        # Sentinel signals the loop to drain and exit.
        await self._queue.put(None)
        if self._writer_task is not None:
            await self._writer_task
            self._writer_task = None

    # ─── Internals ─────────────────────────────────────────────────

    async def _writer_loop(self) -> None:
        """Drain queue forever. Exits on ``None`` sentinel."""
        while True:
            event = await self._queue.get()
            if event is None:
                return
            try:
                await self._write_event(event)
            except Exception:  # noqa: BLE001 — keep the writer alive
                _log.exception(
                    "event_write_failed",
                    event_type=event.event_type.value,
                    device_id=event.device_id,
                    sensor_id=event.sensor_id,
                )

    async def _write_event(self, event: DetectedEvent) -> None:
        # Wait for the post-window to elapse so the buffer has the full ±N s.
        deadline = event.triggered_at + self._post
        wait = deadline - time.time()
        if wait > 0:
            await asyncio.sleep(wait)

        start_ts = event.triggered_at - self._pre
        end_ts = event.triggered_at + self._post
        samples = self._buffer.slice(event.device_id, event.sensor_id, start_ts, end_ts)
        data, encoding = encode_window(samples)

        triggered_dt = datetime.fromtimestamp(event.triggered_at, tz=UTC)
        start_dt = datetime.fromtimestamp(start_ts, tz=UTC)
        end_dt = datetime.fromtimestamp(end_ts, tz=UTC)
        triggered_value = float(event.metadata.get("trigger_value", 0.0))

        async with async_session() as session:
            event_row = Event(
                session_id=self._session_id,
                triggered_at=triggered_dt,
                device_id=event.device_id,
                sensor_id=event.sensor_id,
                event_type=event.event_type,
                triggered_value=triggered_value,
                metadata_=dict(event.metadata),
            )
            session.add(event_row)
            await session.flush()  # populates event_id

            window_row = EventWindow(
                event_id=event_row.event_id,
                start_ts=start_dt,
                end_ts=end_dt,
                sample_rate_hz=self._sample_rate,
                sample_count=len(samples),
                encoding=encoding,
                data=data,
            )
            session.add(window_row)
            await session.flush()  # populates window_id

            # Back-link event → window now that both rows exist.
            await session.execute(
                update(Event)
                .where(
                    Event.event_id == event_row.event_id,
                    Event.triggered_at == triggered_dt,
                )
                .values(window_id=window_row.window_id)
            )

        _m.EVENTS_PERSISTED_TOTAL.labels(event_type=event.event_type.value).inc()
        _m.DB_WRITER_PENDING.set(self._queue.qsize())
        _log.info(
            "event_persisted",
            event_id=event_row.event_id,
            event_type=event.event_type.value,
            device_id=event.device_id,
            sensor_id=event.sensor_id,
            sample_count=len(samples),
        )
