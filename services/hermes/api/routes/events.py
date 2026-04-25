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

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hermes.api.deps import CurrentUser, DbSession
from hermes.db.models import Event, EventType, EventWindow
from hermes.detection.encoding import decode_window

router = APIRouter()

# Hard cap on the list endpoint so a runaway query can't exhaust memory.
_LIST_LIMIT_HARD_CAP: int = 500


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
    stmt = stmt.order_by(Event.triggered_at.desc()).limit(limit).offset(offset)

    rows = await session.execute(stmt)
    return [_event_to_out(ev) for ev in rows.scalars().all()]


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
