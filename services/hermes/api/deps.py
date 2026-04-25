"""
FastAPI dependency injectables.

Keep these small and focused; one exported function per concept. Route
handlers pull them in via `Depends(...)`, which gives us:

    * Uniform database session lifecycle (commit on success, rollback
      on exception, close always).
    * Uniform authentication semantics (JWT decode happens in one place).
    * Easy override in tests — `app.dependency_overrides[get_current_user]
      = lambda: fake_user` replaces auth with a fixture in one line.

Dev-mode bypass:
    When ``HERMES_DEV_MODE=1``, ``get_current_user()`` synthesises an
    in-memory admin user (never persisted). This unblocks local work
    before the OTP + JWT flow lands in Phase 3.5. Production deployments
    MUST set ``HERMES_DEV_MODE=0`` so the bypass is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from hermes.config import get_settings
from hermes.db.engine import async_session
from hermes.db.models import User

# OTP-only auth: clients send the JWT we issued on /api/auth/otp/verify
# as a Bearer token. The tokenUrl below is informational — we never
# expose a password grant endpoint.
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/otp/verify", auto_error=False)

# Fixed identity for the dev-mode bypass user so tests and logs can
# recognise it. The value is arbitrary but pinned so audit trails stay
# stable across process restarts.
_DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000deadbeef")
_DEV_USER_EMAIL = "dev@hermes.local"


async def db() -> AsyncIterator[AsyncSession]:
    """Request-scoped DB session. Commits on clean return, rolls back on raise."""
    async with async_session() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(db)]


def _synthetic_dev_user() -> User:
    """Return an in-memory admin User. Never persisted."""
    return User(
        user_id=_DEV_USER_ID,
        email=_DEV_USER_EMAIL,
        display_name="Dev Bypass",
        is_admin=True,
        is_enabled=True,
        created_at=datetime.now(tz=UTC),
        last_login_at=None,
    )


async def get_current_user(
    token: Annotated[str | None, Depends(_oauth2_scheme)],
    session: DbSession,
) -> User:
    """
    Resolve the authenticated user from a JWT bearer token.

    Order of operations:
        1. If HERMES_DEV_MODE is set AND no token is presented, return a
           synthetic admin user (bypass). This keeps local dev and CI
           unblocked while the OTP/JWT flow is still under construction.
        2. Otherwise require a token; raise 401 if missing.
        3. TODO(phase-3.5-auth): decode JWT, lookup User in DB, check
           is_enabled, return. Until then, a real token path is not
           implemented and raises 501.
    """
    settings = get_settings()

    if settings.hermes_dev_mode and not token:
        del session  # dev bypass; no DB round-trip
        return _synthetic_dev_user()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    del session  # not yet used
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="JWT verification lands in Phase 3.5",
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
