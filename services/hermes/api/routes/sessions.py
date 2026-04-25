"""
/api/sessions — operator-driven session lifecycle (gap 5).

A *session* binds events to the configuration package that was active
when they fired. The DB enforces:

  * One active GLOBAL session at a time (partial unique index
    ``sessions_one_active_global``).
  * One active LOCAL session per device (partial unique index
    ``sessions_one_active_local_per_device``).
  * Scope shape (``sessions_scope_shape`` CHECK):
      - GLOBAL ⇒ parent_session_id IS NULL AND device_id IS NULL
      - LOCAL  ⇒ parent_session_id IS NOT NULL AND device_id IS NOT NULL
  * Timing order: ``ended_at IS NULL OR ended_at >= started_at``.

Triggers (0004_triggers.sql):
  * Closing a GLOBAL session cascades close to all its LOCAL children.
  * Closing any session locks its package (``is_locked = TRUE``).

This module exposes:

  GET    /api/sessions                — list (filterable; newest first)
  GET    /api/sessions/current        — convenience: the active GLOBAL
                                          plus all active LOCALs
  GET    /api/sessions/{id}           — detail
  POST   /api/sessions                — start a new session
  POST   /api/sessions/{id}/stop      — close a session (idempotent)
  GET    /api/sessions/{id}/logs      — audit trail (SessionLog rows)

Audit logging:
  Every start/stop writes a ``session_logs`` row with the matching
  event type. The ``actor`` field is hard-coded to ``"api"`` until
  authenticated user context is wired through (the auth bypass
  shipped in dev mode means we don't have a stable user identity yet).
  When that lands, we'll pull from ``CurrentUser`` instead.

Concurrency:
  Starts are race-free thanks to the partial unique index. If two
  clients POST a GLOBAL start simultaneously, the second will hit
  IntegrityError and we return 409. We do NOT auto-stop the existing
  active session on a new start request — that would be a data loss
  hazard the operator should consciously confirm. Clients must stop
  the running session first.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hermes.api.deps import CurrentUser, DbSession
from hermes.db.models import (
    Package,
    Session,
    SessionLog,
    SessionLogEvent,
    SessionScope,
)

router = APIRouter()


# ─── Shapes ────────────────────────────────────────────────────────


class SessionStart(BaseModel):
    """POST /api/sessions body.

    ``scope`` controls which fields are required:
      * GLOBAL — ``device_id`` and ``parent_session_id`` MUST be omitted.
      * LOCAL  — ``device_id`` MUST be set; ``parent_session_id`` is
                  derived server-side from the active global session.

    ``record_raw_samples`` toggles continuous-sample writing into
    ``session_samples`` (gap 6 territory; the writer doesn't exist
    yet but the column is honoured at the schema level).
    """

    scope: SessionScope
    package_id: uuid.UUID
    device_id: int | None = Field(default=None, ge=1, le=999)
    notes: str | None = Field(default=None, max_length=2000)
    record_raw_samples: bool = False

    @model_validator(mode="after")
    def _shape_must_match_scope(self) -> SessionStart:
        if self.scope is SessionScope.GLOBAL and self.device_id is not None:
            raise ValueError("global sessions must not specify device_id")
        if self.scope is SessionScope.LOCAL and self.device_id is None:
            raise ValueError("local sessions must specify device_id")
        return self


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    scope: SessionScope
    parent_session_id: uuid.UUID | None
    device_id: int | None
    package_id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None
    started_by: str | None
    ended_reason: str | None
    notes: str | None
    record_raw_samples: bool


class SessionStop(BaseModel):
    """POST /api/sessions/{id}/stop body. Optional reason captured in audit log."""

    ended_reason: str | None = Field(default=None, max_length=2000)


class SessionLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_id: int
    session_id: uuid.UUID
    event: SessionLogEvent
    ts: datetime
    actor: str | None
    details: dict[str, object] | None


class CurrentSessionsOut(BaseModel):
    """``/api/sessions/current`` — what's running right now."""

    global_session: SessionOut | None
    local_sessions: list[SessionOut]


# ─── Helpers ───────────────────────────────────────────────────────


async def _get_or_404(session: AsyncSession, session_id: uuid.UUID) -> Session:
    row = await session.get(Session, session_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"session {session_id} not found",
        )
    return row


async def _ensure_package_exists(session: AsyncSession, package_id: uuid.UUID) -> Package:
    """422 if the operator picks a package_id that doesn't exist."""
    pkg = await session.get(Package, package_id)
    if pkg is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"package {package_id} not found",
        )
    return pkg


async def _active_global(session: AsyncSession) -> Session | None:
    return (
        await session.execute(
            select(Session).where(
                Session.scope == SessionScope.GLOBAL,
                Session.ended_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def _write_log(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    event: SessionLogEvent,
    details: dict[str, object] | None = None,
) -> None:
    """Append a session_logs row. Caller commits."""
    session.add(
        SessionLog(
            session_id=session_id,
            event=event,
            actor="api",  # TODO: replace with CurrentUser identity once auth ships
            details=details,
        )
    )


# ─── Routes ────────────────────────────────────────────────────────


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    user: CurrentUser,
    session: DbSession,
    active: Annotated[bool | None, Query()] = None,
    scope: Annotated[SessionScope | None, Query()] = None,
    device_id: Annotated[int | None, Query(ge=1, le=999)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[SessionOut]:
    """List sessions, newest first. All filters are optional and
    AND-combined.

    * ``active=true`` returns only sessions where ``ended_at IS NULL``.
    * ``scope`` filters to GLOBAL or LOCAL.
    * ``device_id`` filters LOCAL sessions for one device (no-op for
      GLOBAL sessions, which never carry a device).

    Default ``limit=100`` keeps the response bounded — callers that
    want history can paginate by ``started_at`` filtering, but for
    the UI's "active + recent" view the default is plenty.
    """
    del user
    stmt = select(Session)
    if active is True:
        stmt = stmt.where(Session.ended_at.is_(None))
    elif active is False:
        stmt = stmt.where(Session.ended_at.is_not(None))
    if scope is not None:
        stmt = stmt.where(Session.scope == scope)
    if device_id is not None:
        stmt = stmt.where(Session.device_id == device_id)
    stmt = stmt.order_by(Session.started_at.desc()).limit(limit)
    rows = await session.execute(stmt)
    return [SessionOut.model_validate(s) for s in rows.scalars().all()]


@router.get("/current", response_model=CurrentSessionsOut)
async def current_sessions(user: CurrentUser, session: DbSession) -> CurrentSessionsOut:
    """Return the currently-active global session and all active locals."""
    del user
    glb = await _active_global(session)
    local_rows = (
        (
            await session.execute(
                select(Session).where(
                    Session.scope == SessionScope.LOCAL,
                    Session.ended_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return CurrentSessionsOut(
        global_session=SessionOut.model_validate(glb) if glb is not None else None,
        local_sessions=[SessionOut.model_validate(s) for s in local_rows],
    )


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
) -> SessionOut:
    del user
    row = await _get_or_404(session, session_id)
    return SessionOut.model_validate(row)


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def start_session(
    payload: SessionStart,
    user: CurrentUser,
    session: DbSession,
) -> SessionOut:
    """Start a new session. Returns 409 if one is already active for
    the requested scope.

    For LOCAL sessions, ``parent_session_id`` is set to the currently
    active GLOBAL session — which must exist (422 otherwise). The DB
    CHECK constraint requires both ``parent_session_id`` and
    ``device_id`` to be non-NULL on local sessions.
    """
    del user
    await _ensure_package_exists(session, payload.package_id)

    parent_id: uuid.UUID | None = None
    if payload.scope is SessionScope.LOCAL:
        glb = await _active_global(session)
        if glb is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="cannot start a local session without an active global session",
            )
        parent_id = uuid.UUID(str(glb.session_id))

    new_row = Session(
        scope=payload.scope,
        parent_session_id=parent_id,
        device_id=payload.device_id,
        package_id=payload.package_id,
        notes=payload.notes,
        record_raw_samples=payload.record_raw_samples,
        started_by="api",
    )
    session.add(new_row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # The most likely cause is one of the partial unique indexes
        # (one active global, one active local per device).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot start session: another active session already holds this scope",
        ) from exc
    await session.refresh(new_row)

    # Audit row in the same transaction.
    await _write_log(
        session,
        session_id=uuid.UUID(str(new_row.session_id)),
        event=SessionLogEvent.START,
        details={
            "scope": payload.scope.value,
            "package_id": str(payload.package_id),
            "device_id": payload.device_id,
        },
    )
    await session.commit()
    return SessionOut.model_validate(new_row)


@router.post("/{session_id}/stop", response_model=SessionOut)
async def stop_session(
    session_id: uuid.UUID,
    payload: SessionStop,
    user: CurrentUser,
    session: DbSession,
) -> SessionOut:
    """Close a session.

    Idempotent: if the session is already closed, returns the existing
    row unchanged (and writes no audit log). The DB trigger
    ``end_local_children`` will cascade-close any LOCAL children when
    a GLOBAL session is closed; the trigger ``sessions_lock_package``
    will lock the session's package on first close.
    """
    del user
    row = await _get_or_404(session, session_id)
    if row.ended_at is not None:
        return SessionOut.model_validate(row)

    # The DB has a CHECK constraint requiring ended_at >= started_at;
    # ``func.now()`` plus the started_at default of ``func.now()``
    # makes that automatically true.
    from sqlalchemy import func as _func

    row.ended_at = _func.now()
    row.ended_reason = payload.ended_reason
    await session.flush()
    await session.refresh(row)

    await _write_log(
        session,
        session_id=session_id,
        event=SessionLogEvent.STOP,
        details={"reason": payload.ended_reason} if payload.ended_reason else None,
    )
    await session.commit()
    return SessionOut.model_validate(row)


@router.get("/{session_id}/logs", response_model=list[SessionLogOut])
async def list_session_logs(
    session_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
    order: Annotated[Literal["asc", "desc"], Query()] = "asc",
) -> list[SessionLogOut]:
    """Return the audit trail for one session.

    Default order is ascending so the UI can render a chronological
    timeline. ``order=desc`` is convenient for "what just happened?"
    investigations.
    """
    del user
    # Ensure the parent session exists for a clean 404, otherwise an
    # empty array would be ambiguous.
    await _get_or_404(session, session_id)

    stmt = select(SessionLog).where(SessionLog.session_id == session_id)
    if order == "asc":
        stmt = stmt.order_by(SessionLog.ts.asc(), SessionLog.log_id.asc())
    else:
        stmt = stmt.order_by(SessionLog.ts.desc(), SessionLog.log_id.desc())

    rows = await session.execute(stmt)
    return [SessionLogOut.model_validate(r) for r in rows.scalars().all()]
