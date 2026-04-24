"""
FastAPI dependency injectables.

Keep these small and focused; one exported function per concept. Route
handlers pull them in via `Depends(...)`, which gives us:

    * Uniform database session lifecycle (commit on success, rollback
      on exception, close always).
    * Uniform authentication semantics (JWT decode happens in one place).
    * Easy override in tests — `app.dependency_overrides[get_current_user]
      = lambda: fake_user` replaces auth with a fixture in one line.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from hermes.db.engine import async_session
from hermes.db.models import User

# OTP-only auth: clients send the JWT we issued on /api/auth/otp/verify
# as a Bearer token. The tokenUrl below is informational — we never
# expose a password grant endpoint.
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/otp/verify", auto_error=False)


async def db() -> AsyncIterator[AsyncSession]:
    """Request-scoped DB session. Commits on clean return, rolls back on raise."""
    async with async_session() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(db)]


async def get_current_user(
    token: Annotated[str | None, Depends(_oauth2_scheme)],
    session: DbSession,
) -> User:
    """
    Resolve the authenticated user from a JWT bearer token.

    Raises 401 for: missing token, expired token, unknown/ disabled user.

    TODO(phase-1-auth): implement JWT decode + user lookup. Stubbed for
    the scaffold; see `hermes.auth` module (to be added in the auth PR).
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Auth flow not yet implemented (scaffold only)",
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
