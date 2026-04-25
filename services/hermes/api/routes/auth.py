"""
Authentication routes — OTP over email.

Flow:
    1. Client POSTs email to /api/auth/otp/request. If the address is
       in the allowlist file, we generate a 6-digit OTP, argon2id-hash
       it into ``user_otps``, and email the plaintext code. We ALWAYS
       return 204 — even when the email is unknown — so an attacker
       can't probe the allowlist.

    2. Client POSTs ``{email, otp}`` to /api/auth/otp/verify. Server
       argon2-verifies against the most recent unconsumed OTP for the
       user; on success it marks the row consumed and issues a JWT.

    3. Client uses the JWT as ``Authorization: Bearer <token>``.

    4. POST /api/auth/logout returns 204. JWT expiry handles
       invalidation; we deliberately do not maintain a revocation list.

Rate limits (settings):
    * ``otp_max_per_hour``  — caps requests per email per rolling hour.
    * ``otp_resend_cooldown_seconds`` — minimum gap between requests.
    * ``otp_max_attempts``  — how many wrong codes one OTP tolerates.
    * ``otp_expiry_seconds`` — TTL for an issued OTP.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from hermes.api.deps import DbSession
from hermes.auth import allowlist
from hermes.auth.email import send_otp_code
from hermes.auth.jwt import issue_token
from hermes.auth.otp import generate_code, hash_code, verify_code
from hermes.config import get_settings
from hermes.db.models import User, UserOtp
from hermes.logging import get_logger

router = APIRouter()
_log = get_logger(__name__, component="auth")


# ─── Request / response shapes ─────────────────────────────────────


class OtpRequest(BaseModel):
    """POST /api/auth/otp/request payload."""

    email: EmailStr


class OtpVerify(BaseModel):
    """POST /api/auth/otp/verify payload."""

    email: EmailStr
    otp: str = Field(..., pattern=r"^\d{6}$", description="Six-digit code from email.")


class TokenResponse(BaseModel):
    """Issued by /api/auth/otp/verify on success."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


# ─── Helpers ───────────────────────────────────────────────────────


async def _get_or_create_user(session: DbSession, email: str) -> User:
    """Find the User row for ``email``; create one if the address is new.

    The allowlist check is done by the caller — by the time we land
    here the email is allowed. We don't pre-populate users via
    migrations because the allowlist is the source of truth.
    """
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        return existing
    user = User(email=email, is_admin=False, is_enabled=True)
    session.add(user)
    await session.flush()
    return user


async def _check_rate_limits(session: DbSession, user: User) -> None:
    """
    Enforce per-email rate limits. Raises 429 on violation.

    Per-hour cap: count OTPs created in the last hour.
    Cooldown:     reject if the most recent OTP is too fresh.
    """
    settings = get_settings()
    now = datetime.now(tz=UTC)

    # Most recent OTP for this user (cooldown gate).
    last = (
        await session.execute(
            select(UserOtp)
            .where(UserOtp.user_id == user.user_id)
            .order_by(UserOtp.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is not None:
        # SQLAlchemy returns timezone-naive datetimes for some Postgres
        # builds depending on the driver — coerce to UTC for safety.
        last_created = last.created_at
        if last_created.tzinfo is None:
            last_created = last_created.replace(tzinfo=UTC)
        elapsed = (now - last_created).total_seconds()
        if elapsed < settings.otp_resend_cooldown_seconds:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"another code can be requested in "
                    f"{int(settings.otp_resend_cooldown_seconds - elapsed)} s"
                ),
            )

    # Per-hour cap.
    one_hour_ago = now - timedelta(hours=1)
    rows = (
        (
            await session.execute(
                select(UserOtp).where(
                    UserOtp.user_id == user.user_id,
                    UserOtp.created_at >= one_hour_ago,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(rows) >= settings.otp_max_per_hour:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="hourly OTP request quota exceeded",
        )


# ─── Routes ────────────────────────────────────────────────────────


@router.post(
    "/otp/request",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={204: {"description": "Always. Do not expose allowlist membership."}},
)
async def request_otp(payload: OtpRequest, session: DbSession) -> None:
    """
    Generate an OTP and email it.

    Returns 204 regardless of whether the email is allowlisted — this
    is intentional to prevent account enumeration. Rate-limit
    violations DO surface as 429 so the operator knows to wait.
    """
    email = payload.email.lower()
    if not allowlist.is_allowed(email):
        _log.info("otp_request_for_disallowed_email", email=email)
        return  # 204 — silently swallow

    user = await _get_or_create_user(session, email)
    await _check_rate_limits(session, user)

    code = generate_code()
    settings = get_settings()
    now = datetime.now(tz=UTC)
    otp_row = UserOtp(
        user_id=user.user_id,
        code_hash=hash_code(code),
        expires_at=now + timedelta(seconds=settings.otp_expiry_seconds),
    )
    session.add(otp_row)
    await session.commit()

    try:
        await send_otp_code(to=email, code=code)
    except Exception:
        # Email backend failures shouldn't leak through the 204 path —
        # log and swallow. The row is in the DB so the operator can
        # still verify if the email arrived.
        _log.exception("otp_email_send_failed", email=email)


@router.post("/otp/verify", response_model=TokenResponse)
async def verify_otp(payload: OtpVerify, session: DbSession) -> TokenResponse:
    """Verify the code; on success, return a short-lived JWT."""
    email = payload.email.lower()
    settings = get_settings()
    now = datetime.now(tz=UTC)

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not user.is_enabled:
        # Same response as a wrong code, on purpose.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid email or code",
        )

    # Most recent unconsumed, unexpired OTP for this user. Older ones
    # are ignored — the operator might have requested several before
    # the right one arrived.
    otp = (
        await session.execute(
            select(UserOtp)
            .where(
                UserOtp.user_id == user.user_id,
                UserOtp.consumed_at.is_(None),
                UserOtp.expires_at > now,
            )
            .order_by(UserOtp.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if otp is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid email or code",
        )

    if otp.attempt_count >= settings.otp_max_attempts:
        # Lock the OTP — caller must request a new one.
        otp.consumed_at = now
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="too many attempts; request a new code",
        )

    if not verify_code(payload.otp, otp.code_hash):
        otp.attempt_count = otp.attempt_count + 1
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid email or code",
        )

    # Success.
    otp.consumed_at = now
    user.last_login_at = now
    await session.commit()

    token = issue_token(user.user_id)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.hermes_jwt_expiry_seconds,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> None:
    """
    Client-side logout. Server has no session to invalidate; JWT expiry
    handles it. Exists as a 204 so UIs can POST here and drop the token.
    """
    return
