"""
/api/events — query persisted events and their ±9 s sample windows.

Endpoints:

    GET  /api/events                    — paginated list with filters
    GET  /api/events/{event_id}         — single event summary
    GET  /api/events/{event_id}/window  — decoded sample window

Filters on the list endpoint mirror the legacy dashboard's drilldown:
device_id, sensor_id, event_type, time range. ``limit`` is capped server-
side to keep the response bounded; pagination uses simple offset for
now (Phase 4 polish can swap in keyset on triggered_at if it becomes
slow at scale).

Composite PK note:
    The ``events`` table has a composite PK ``(event_id, triggered_at)``
    required by TimescaleDB hypertable partitioning. The ``event_id``
    column is still backed by an Identity sequence, so it is unique on
    its own — safe to use in URL paths.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from hermes.api.deps import CurrentUser, DbSession
from hermes.db.engine import async_session
from hermes.db.models import Event, EventType, EventWindow
from hermes.detection.encoding import decode_window

router = APIRouter()

# Hard cap on the list endpoint so a runaway query can't exhaust memory.
_LIST_LIMIT_HARD_CAP: int = 500

# Export streams in chunks of this many rows. Picked so each round-trip
# stays under ~1 MB serialised; the streamer pages through the whole
# matching set, so the total export size is unbounded by chunk size.
_EXPORT_CHUNK_SIZE: int = 1000

# Absolute ceiling on a single export to defend against a runaway query.
# At 1 M rows × ~200 B per row, the response body tops out around 200 MB.
# Operators needing more should slice by time range.
_EXPORT_MAX_ROWS: int = 1_000_000

ExportFormat = Literal["csv", "ndjson"]

# CSV column order. Stable across releases — appended-only, never
# reordered, so downstream spreadsheet tools don't break.
_CSV_COLUMNS: tuple[str, ...] = (
    "event_id",
    "triggered_at",
    "fired_at",
    "device_id",
    "sensor_id",
    "event_type",
    "triggered_value",
    "metadata",
    "session_id",
    "window_id",
)


# ─── Shapes ────────────────────────────────────────────────────────


class EventOut(BaseModel):
    """One row from the ``events`` table."""

    model_config = ConfigDict(from_attributes=True)

    event_id: int
    triggered_at: datetime
    fired_at: datetime
    session_id: str
    device_id: int
    sensor_id: int
    event_type: EventType
    triggered_value: float
    metadata: dict[str, Any]
    window_id: int | None


class EventWindowOut(BaseModel):
    """One row from ``event_windows`` plus the decoded sample list."""

    window_id: int
    event_id: int
    start_ts: datetime
    end_ts: datetime
    sample_rate_hz: float
    sample_count: int
    encoding: str
    samples: list[tuple[float, float]]


def _event_to_out(ev: Event) -> EventOut:
    """Hand-mapped because ``session_id`` widens to UUID/Any in SA."""
    return EventOut(
        event_id=ev.event_id,
        triggered_at=ev.triggered_at,
        fired_at=ev.fired_at,
        session_id=str(ev.session_id),
        device_id=ev.device_id,
        sensor_id=ev.sensor_id,
        event_type=ev.event_type,
        triggered_value=ev.triggered_value,
        metadata=ev.metadata_,
        window_id=ev.window_id,
    )


async def _get_event_or_404(session: AsyncSession, event_id: int) -> Event:
    rows = await session.execute(select(Event).where(Event.event_id == event_id))
    ev = rows.scalar_one_or_none()
    if ev is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"event {event_id} not found",
        )
    return ev


def _filtered_events_query(
    *,
    device_id: int | None,
    sensor_id: int | None,
    event_type: EventType | None,
    after: datetime | None,
    before: datetime | None,
) -> Select[tuple[Event]]:
    """Build the filter predicate shared by ``list_events`` and the exporter."""
    stmt = select(Event)
    if device_id is not None:
        stmt = stmt.where(Event.device_id == device_id)
    if sensor_id is not None:
        stmt = stmt.where(Event.sensor_id == sensor_id)
    if event_type is not None:
        stmt = stmt.where(Event.event_type == event_type)
    if after is not None:
        stmt = stmt.where(Event.triggered_at >= after)
    if before is not None:
        stmt = stmt.where(Event.triggered_at < before)
    return stmt


# ─── Routes ────────────────────────────────────────────────────────


@router.get("", response_model=list[EventOut])
async def list_events(
    user: CurrentUser,
    session: DbSession,
    device_id: Annotated[int | None, Query(ge=1, le=999)] = None,
    sensor_id: Annotated[int | None, Query(ge=1, le=12)] = None,
    event_type: Annotated[EventType | None, Query()] = None,
    after: Annotated[
        datetime | None,
        Query(description="Only events with triggered_at >= this ISO timestamp."),
    ] = None,
    before: Annotated[
        datetime | None,
        Query(description="Only events with triggered_at < this ISO timestamp."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=_LIST_LIMIT_HARD_CAP)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[EventOut]:
    """
    Return events matching the filters, newest first.

    Filters compose with AND. ``triggered_at`` is the comparison column
    (the original threshold-crossing moment, not when the row was
    written) — same field shown on the dashboard timeline.
    """
    del user
    stmt = (
        _filtered_events_query(
            device_id=device_id,
            sensor_id=sensor_id,
            event_type=event_type,
            after=after,
            before=before,
        )
        .order_by(Event.triggered_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = await session.execute(stmt)
    return [_event_to_out(ev) for ev in rows.scalars().all()]


# ─── Streaming export ──────────────────────────────────────────────
#
# Registered BEFORE the parametric ``/{event_id}`` route so that
# ``GET /api/events/export`` doesn't try to bind ``event_id="export"``
# to ``int`` and bounce as a 422.


def _csv_row(ev: Event) -> str:
    """Serialise one Event into a CSV line. Order matches ``_CSV_COLUMNS``."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            ev.event_id,
            ev.triggered_at.isoformat(),
            ev.fired_at.isoformat(),
            ev.device_id,
            ev.sensor_id,
            ev.event_type.value,
            ev.triggered_value,
            json.dumps(ev.metadata_, separators=(",", ":")),
            str(ev.session_id),
            ev.window_id if ev.window_id is not None else "",
        ]
    )
    return buffer.getvalue()


def _ndjson_row(ev: Event) -> str:
    """Serialise one Event as a single NDJSON line."""
    out = _event_to_out(ev).model_dump(mode="json")
    return json.dumps(out, separators=(",", ":")) + "\n"


async def _stream_events(
    fmt: ExportFormat,
    *,
    device_id: int | None,
    sensor_id: int | None,
    event_type: EventType | None,
    after: datetime | None,
    before: datetime | None,
) -> AsyncIterator[str]:
    """
    Page through the matching events in chunks of ``_EXPORT_CHUNK_SIZE``,
    yielding one CSV / NDJSON line at a time. Each chunk opens a fresh
    session so we never hold a connection across the entire export.
    """
    base_query = _filtered_events_query(
        device_id=device_id,
        sensor_id=sensor_id,
        event_type=event_type,
        after=after,
        before=before,
    ).order_by(Event.triggered_at.desc())

    if fmt == "csv":
        # Emit the header before the first chunk.
        yield ",".join(_CSV_COLUMNS) + "\n"

    formatter = _csv_row if fmt == "csv" else _ndjson_row
    offset = 0
    emitted = 0
    while emitted < _EXPORT_MAX_ROWS:
        async with async_session() as session:
            chunk = (
                (await session.execute(base_query.limit(_EXPORT_CHUNK_SIZE).offset(offset)))
                .scalars()
                .all()
            )
        if not chunk:
            return
        for ev in chunk:
            yield formatter(ev)
            emitted += 1
            if emitted >= _EXPORT_MAX_ROWS:
                return
        if len(chunk) < _EXPORT_CHUNK_SIZE:
            return
        offset += _EXPORT_CHUNK_SIZE


@router.get("/export")
async def export_events(
    user: CurrentUser,
    fmt: Annotated[
        ExportFormat,
        Query(alias="format", description="Output format: csv or ndjson"),
    ] = "csv",
    device_id: Annotated[int | None, Query(ge=1, le=999)] = None,
    sensor_id: Annotated[int | None, Query(ge=1, le=12)] = None,
    event_type: Annotated[EventType | None, Query()] = None,
    after: Annotated[datetime | None, Query()] = None,
    before: Annotated[datetime | None, Query()] = None,
) -> StreamingResponse:
    """
    Stream every matching event as CSV (default) or NDJSON.

    Same filter shape as the list endpoint. Hard-capped at
    ``_EXPORT_MAX_ROWS`` rows per request — slice by time range if you
    need more. Each chunk opens its own DB session so an export does
    not hog a pool connection for its full duration.
    """
    del user
    media_type = "text/csv" if fmt == "csv" else "application/x-ndjson"
    extension = "csv" if fmt == "csv" else "ndjson"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"hermes-events-{stamp}.{extension}"
    return StreamingResponse(
        _stream_events(
            fmt,
            device_id=device_id,
            sensor_id=sensor_id,
            event_type=event_type,
            after=after,
            before=before,
        ),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{event_id}", response_model=EventOut)
async def get_event(event_id: int, user: CurrentUser, session: DbSession) -> EventOut:
    del user
    ev = await _get_event_or_404(session, event_id)
    return _event_to_out(ev)


@router.get("/{event_id}/window", response_model=EventWindowOut)
async def get_event_window(event_id: int, user: CurrentUser, session: DbSession) -> EventWindowOut:
    """
    Return the ±9 s sample window for an event, decoded into a list of
    ``(ts, value)`` pairs. Returns 404 if either the event or the window
    row is missing.
    """
    del user
    ev = await _get_event_or_404(session, event_id)
    if ev.window_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"event {event_id} has no window (still flushing or write failed)",
        )

    win = (
        await session.execute(select(EventWindow).where(EventWindow.window_id == ev.window_id))
    ).scalar_one_or_none()
    if win is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"window {ev.window_id} for event {event_id} missing",
        )

    samples = decode_window(win.data, win.encoding)
    return EventWindowOut(
        window_id=win.window_id,
        event_id=win.event_id,
        start_ts=win.start_ts,
        end_ts=win.end_ts,
        sample_rate_hz=win.sample_rate_hz,
        sample_count=win.sample_count,
        encoding=win.encoding,
        samples=samples,
    )
