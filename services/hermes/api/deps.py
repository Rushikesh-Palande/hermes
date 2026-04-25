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
    When ``HERMES_DEV_MODE=1`` AND no token is presented,
    ``get_current_user()`` synthesises an in-memory admin user (never
    persisted). This keeps tests + first-boot dev unblocked. Production
    deployments MUST set ``HERMES_DEV_MODE=0`` so the bypass is
    unreachable. When a token IS presented, JWT decode happens regardless
    of the bypass flag — so a partially-rolled-out frontend can flip to
    real auth without flipping the env.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hermes.auth.jwt import InvalidTokenError, decode_token
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
        1. Token present → decode JWT, look up User, check is_enabled.
        2. No token + HERMES_DEV_MODE=1 → return the synthetic admin.
        3. Otherwise → 401.

    Putting JWT decode first means a real token always wins over the
    bypass, so a frontend running in dev mode can flip to real auth
    without flipping any env vars.
    """
    settings = get_settings()

    if token:
        try:
            user_id = decode_token(token)
        except InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        user = (
            await session.execute(select(User).where(User.user_id == user_id))
        ).scalar_one_or_none()
        if user is None or not user.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="user not found or disabled",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    if settings.hermes_dev_mode:
        del session  # dev bypass; no DB round-trip
        return _synthetic_dev_user()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
