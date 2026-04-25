"""
Session bootstrap for the ingest process.

Every persisted event row needs a ``session_id`` (FK to ``sessions``);
every session needs a ``package_id`` (FK to ``packages``). On a fresh
install nothing exists — this module ensures both records are present
and returns the active session UUID.

For Phase 3e there is exactly ONE long-running global session per
process, started on boot, ended only on shutdown. Phase 4+ will add an
operator-driven session lifecycle (start/stop, package switching) via
the API, at which point the bootstrap here becomes a fallback for
"the operator hasn't created a session yet".
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hermes.db.engine import async_session
from hermes.db.models import Package, Session, SessionScope
from hermes.logging import get_logger

_log = get_logger(__name__, component="ingest")

# Stable name for the auto-created default package; not user-visible.
_DEFAULT_PACKAGE_NAME = "default"


async def ensure_default_session() -> uuid.UUID:
    """
    Return the UUID of an active global session, creating the default
    package and session if neither exists yet.

    Idempotent — safe to call on every boot.
    """
    async with async_session() as session:
        # 1. Default package — find existing or create a fresh one.
        package_id = await _ensure_default_package(session)

        # 2. Active global session — reuse if there's one open.
        active = (
            await session.execute(
                select(Session).where(
                    Session.scope == SessionScope.GLOBAL,
                    Session.ended_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if active is not None:
            _log.info("session_resumed", session_id=str(active.session_id))
            return active.session_id

        new_session = Session(
            scope=SessionScope.GLOBAL,
            package_id=package_id,
            started_by="ingest-bootstrap",
        )
        session.add(new_session)
        await session.flush()
        _log.info(
            "session_started",
            session_id=str(new_session.session_id),
            package_id=str(package_id),
        )
        return new_session.session_id


async def _ensure_default_package(session: AsyncSession) -> uuid.UUID:
    """Find or create the default package; return its id."""
    pkg = (
        await session.execute(select(Package).where(Package.is_default.is_(True)))
    ).scalar_one_or_none()

    if pkg is not None:
        # SQLAlchemy's ``Mapped[uuid.UUID]`` widens to ``Any`` at runtime;
        # cast back so the public signature stays precise.
        return uuid.UUID(str(pkg.package_id))

    pkg = Package(
        name=_DEFAULT_PACKAGE_NAME,
        description="Auto-created on first boot. Replace via the packages API.",
        is_default=True,
        created_by="ingest-bootstrap",
    )
    session.add(pkg)
    await session.flush()
    _log.info("default_package_created", package_id=str(pkg.package_id))
    return uuid.UUID(str(pkg.package_id))
