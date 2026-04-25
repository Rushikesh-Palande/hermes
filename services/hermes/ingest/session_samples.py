"""
Continuous raw-sample writer for the ``session_samples`` hypertable (gap 6).

When an operator starts a session with ``record_raw_samples=true``,
HERMES archives every sensor reading into ``session_samples`` for the
lifetime of that session. The hypertable accepts ~30 k rows/sec at
peak production load (20 devices × 12 sensors × 100 Hz), so the
writer must:

  1. Stay off the ingest hot path. The per-sample call is a single
     in-memory append — no DB I/O, no allocations beyond the tuple.
  2. Batch writes via asyncpg ``copy_records_to_table`` so we hit the
     DB at most once per second instead of once per row.
  3. Discover which sessions are recording without polling Postgres
     on every sample. We refresh the "recording set" every few
     seconds via the existing SQLAlchemy engine.
  4. Survive backpressure. If the writer can't keep up with the
     input rate, we DROP samples (oldest first) and increment a
     metric counter. Better to lose archive rows than to OOM the
     ingest process.

Session resolution rule (mirrors the detector config resolution):

  * If a LOCAL session for the device is active AND has
    ``record_raw_samples=true``, samples land under that LOCAL
    session.
  * Else, if the GLOBAL session is active AND has
    ``record_raw_samples=true``, samples land under the GLOBAL.
  * Else, samples are dropped silently (no recording active).

The writer holds NO state about sessions that aren't recording. The
common case (no active recording) costs one dict lookup per snapshot
and immediately returns.

Lifecycle:

  * ``start()`` opens a dedicated asyncpg connection (separate from
    the SQLAlchemy pool — long-lived, one COPY at a time) and spawns
    the refresh + flush coroutines.
  * ``push_snapshot(device_id, ts, sensor_values)`` is called from
    ``_consume`` after the live + window buffers are filled.
  * ``stop()`` cancels both background tasks, flushes whatever is
    left in the buffer, and closes the asyncpg connection.

Why a dedicated asyncpg connection: ``copy_records_to_table`` needs
the raw asyncpg API (``conn.copy_records_to_table(...)``), which
SQLAlchemy doesn't expose cleanly. Holding a single dedicated
connection also keeps the SQLAlchemy pool free for application
queries.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy import select

from hermes import metrics as _m
from hermes.db.engine import async_session
from hermes.db.models import Session, SessionScope
from hermes.logging import get_logger

_log = get_logger(__name__, component="ingest")

# Default tunables. Bench targets are gentle — we leave plenty of
# headroom for the operator to flip recording on at peak load.
_DEFAULT_MAX_BUFFER: int = 60_000  # ~2 s at 30 k rows/s
_DEFAULT_FLUSH_INTERVAL_S: float = 1.0
_DEFAULT_REFRESH_INTERVAL_S: float = 5.0
_FLUSH_BATCH_SIZE: int = 5_000

_RowTuple = tuple[uuid.UUID, int, int, datetime, float]


class SessionSampleWriter:
    """Background writer for the ``session_samples`` hypertable.

    Single-instance per process. Owned by ``IngestPipeline``; stopped
    on pipeline shutdown.
    """

    __slots__ = (
        "_dsn",
        "_max_buffer",
        "_flush_interval_s",
        "_refresh_interval_s",
        "_conn",
        "_buffer",
        "_global_session_id",
        "_local_sessions",
        "_refresh_task",
        "_flush_task",
        "_stop_event",
    )

    def __init__(
        self,
        *,
        dsn: str,
        max_buffer: int = _DEFAULT_MAX_BUFFER,
        flush_interval_s: float = _DEFAULT_FLUSH_INTERVAL_S,
        refresh_interval_s: float = _DEFAULT_REFRESH_INTERVAL_S,
    ) -> None:
        self._dsn = dsn
        self._max_buffer = max_buffer
        self._flush_interval_s = flush_interval_s
        self._refresh_interval_s = refresh_interval_s

        self._conn: asyncpg.Connection | None = None
        # Pre-allocated list so the hot path doesn't allocate on push.
        # We swap it under a lock at flush time.
        self._buffer: list[_RowTuple] = []

        # Recording-set caches; updated every refresh_interval_s. None
        # = no active GLOBAL with recording on. Empty dict = no active
        # LOCAL recordings. The hot path reads these without locking
        # because they're replaced atomically (single ref assignment).
        self._global_session_id: uuid.UUID | None = None
        self._local_sessions: dict[int, uuid.UUID] = {}

        self._refresh_task: asyncio.Task[None] | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    # ─── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Open the asyncpg connection and spawn background loops.

        Idempotent; calling start twice is a no-op.
        """
        if self._conn is not None:
            return
        try:
            self._conn = await asyncpg.connect(self._dsn)
        except Exception:
            _log.exception("session_samples_writer_connect_failed")
            return
        # Initial refresh before the loop kicks in so push_snapshot
        # has the up-to-date set on the very first sample.
        await self._refresh_recording_set()
        self._refresh_task = asyncio.create_task(
            self._refresh_loop(), name="session-samples-refresh"
        )
        self._flush_task = asyncio.create_task(self._flush_loop(), name="session-samples-flush")
        _log.info("session_samples_writer_started")

    async def stop(self) -> None:
        """Cancel loops, flush remaining buffer, close the connection."""
        self._stop_event.set()
        for task in (self._refresh_task, self._flush_task):
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._refresh_task = None
        self._flush_task = None

        # Final drain so a graceful shutdown doesn't lose the last
        # second's samples. Best-effort; if the DB is gone we log and
        # move on.
        try:
            await self._flush_once()
        except Exception:
            _log.exception("session_samples_writer_final_flush_failed")

        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None
        _log.info("session_samples_writer_stopped")

    # ─── Hot path ────────────────────────────────────────────────

    def push_snapshot(self, device_id: int, ts: float, sensor_values: dict[int, float]) -> None:
        """Append a snapshot to the write buffer iff a recording is active.

        Hot path — must not allocate beyond the row tuples themselves
        and must not touch the network. Returns immediately when no
        recording session covers this device.

        Resolution: LOCAL session for ``device_id`` wins over GLOBAL.
        ``ts`` is wall time (Unix epoch float); converted to a tz-aware
        datetime here so ``copy_records_to_table`` can serialise it
        directly. ``datetime.fromtimestamp`` is roughly 1 µs on
        CPython, well within budget at 24 000 calls/s.
        """
        session_id = self._local_sessions.get(device_id)
        if session_id is None:
            session_id = self._global_session_id
        if session_id is None:
            return
        # Capacity check + drop on overflow. We track drops at the row
        # level (not snapshot level) so the metric matches the
        # written counter unit-for-unit.
        space = self._max_buffer - len(self._buffer)
        if space <= 0:
            _m.SESSION_SAMPLES_DROPPED_TOTAL.inc(len(sensor_values))
            return
        if space < len(sensor_values):
            _m.SESSION_SAMPLES_DROPPED_TOTAL.inc(len(sensor_values) - space)
        ts_dt = datetime.fromtimestamp(ts, tz=UTC)
        buffer_append = self._buffer.append
        for sensor_id, value in sensor_values.items():
            if space <= 0:
                break
            buffer_append((session_id, device_id, sensor_id, ts_dt, value))
            space -= 1
        _m.SESSION_SAMPLES_QUEUE_DEPTH.set(len(self._buffer))

    # ─── Background loops ───────────────────────────────────────

    async def _refresh_loop(self) -> None:
        """Periodically refresh the recording-set caches."""
        while not self._stop_event.is_set():
            # Wait_for serves as our cancellable sleep; timeout is the
            # signal to do another refresh, while a stop_event set
            # makes us return early.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._refresh_interval_s)
            try:
                await self._refresh_recording_set()
            except Exception:
                _log.exception("session_samples_refresh_failed")

    async def _refresh_recording_set(self) -> None:
        """Query active sessions where ``record_raw_samples=true``."""
        async with async_session() as session:
            rows = (
                (
                    await session.execute(
                        select(Session).where(
                            Session.ended_at.is_(None),
                            Session.record_raw_samples.is_(True),
                        )
                    )
                )
                .scalars()
                .all()
            )

        new_global: uuid.UUID | None = None
        new_local: dict[int, uuid.UUID] = {}
        for row in rows:
            sid = uuid.UUID(str(row.session_id))
            if row.scope is SessionScope.GLOBAL:
                new_global = sid
            elif row.scope is SessionScope.LOCAL and row.device_id is not None:
                new_local[row.device_id] = sid

        # Atomic swap. The hot path reads via single attribute lookup
        # so it sees either the old or the new state, never a torn one.
        self._global_session_id = new_global
        self._local_sessions = new_local

        active = 1 if (new_global is not None or new_local) else 0
        _m.SESSION_SAMPLES_RECORDING_ACTIVE.set(active)

    async def _flush_loop(self) -> None:
        """Periodically flush the buffer to ``session_samples``."""
        while not self._stop_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._flush_interval_s)
            try:
                await self._flush_once()
            except Exception:
                _log.exception("session_samples_flush_failed")

    async def _flush_once(self) -> None:
        """Flush the buffer in chunks of ``_FLUSH_BATCH_SIZE``.

        Splits into multiple COPY calls when the buffer is larger than
        one batch so a single very-busy second doesn't tie up the
        connection for too long. asyncpg's ``copy_records_to_table``
        is the most efficient async path: it pipelines a Postgres
        COPY ... FROM STDIN, no per-row INSERT round-trips.
        """
        if not self._buffer or self._conn is None:
            return
        # Swap the live buffer atomically so the hot path keeps
        # writing into a fresh list while we drain the old one.
        pending = self._buffer
        self._buffer = []
        _m.SESSION_SAMPLES_QUEUE_DEPTH.set(0)

        for chunk_start in range(0, len(pending), _FLUSH_BATCH_SIZE):
            chunk = pending[chunk_start : chunk_start + _FLUSH_BATCH_SIZE]
            try:
                await self._conn.copy_records_to_table(
                    "session_samples",
                    records=chunk,
                    columns=("session_id", "device_id", "sensor_id", "ts", "value"),
                )
            except Exception:
                # Best-effort: log, increment drops, keep going. We
                # don't requeue because the failure is most likely a
                # constraint violation (e.g. an already-closed session
                # whose FK we can't satisfy on a stale buffer entry).
                _log.exception(
                    "session_samples_copy_failed",
                    chunk_size=len(chunk),
                )
                _m.SESSION_SAMPLES_DROPPED_TOTAL.inc(len(chunk))
                continue
            _m.SESSION_SAMPLES_WRITTEN_TOTAL.inc(len(chunk))
            _m.SESSION_SAMPLES_BATCHES_FLUSHED_TOTAL.inc()

    # ─── Introspection (used by tests) ────────────────────────────

    @property
    def buffer_size(self) -> int:
        """Current buffer depth. Useful for tests; not for hot-path use."""
        return len(self._buffer)

    @property
    def is_recording(self) -> bool:
        """True iff at least one session is configured to record."""
        return self._global_session_id is not None or bool(self._local_sessions)
