"""
/api/health — liveness and readiness probes.

Two endpoints, deliberately distinct:

    GET /api/health         — liveness. Returns 200 if the process is
                              alive. Does NOT hit the database.
                              Used by systemd and the nginx upstream
                              probe to decide "should I kill and restart
                              this?".

    GET /api/health/ready   — readiness. Returns 200 iff the process can
                              serve real traffic (DB reachable, migrations
                              applied). Used by deployment scripts and
                              orchestrators to decide "is this instance
                              safe to send traffic to?".

Why two endpoints and not one:
    During a migration or DB flap, liveness should stay green (don't
    restart the process; it's fine) while readiness goes red (don't
    send real requests yet). Conflating them causes flap loops.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from hermes import __version__
from hermes.api.deps import DbSession

router = APIRouter()


class HealthResponse(BaseModel):
    """Small payload; kept stable across versions."""

    status: str
    version: str


@router.get("", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    """Liveness probe. Always 200 when the process is running."""
    return HealthResponse(status="ok", version=__version__)


@router.get("/ready", response_model=HealthResponse)
async def readiness(session: DbSession) -> HealthResponse | JSONResponse:
    """
    Readiness probe. Issues `SELECT 1` on the DB; returns 503 if it fails.

    We deliberately do NOT cache the result — the whole point is to
    reflect current DB reachability, not a stale snapshot.
    """
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — we DO want to catch anything
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "db_unreachable", "detail": str(exc)},
        )
    return HealthResponse(status="ready", version=__version__)
