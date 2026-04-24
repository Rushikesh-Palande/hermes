"""
Authentication routes — OTP over email.

Flow (from docs/reference/templates/other_templates.md, login.html):

    1. Client POSTs email to /api/auth/otp/request.
       Server: if email is in allowed_emails.txt, generate a 6-digit OTP,
       hash it with argon2id, store (user_id, code_hash, expires_at),
       and email the plaintext OTP. Always returns 204 to avoid
       revealing whether an email is allowlisted (prevents enumeration).

    2. Client POSTs { email, otp } to /api/auth/otp/verify.
       Server: argon2-verify against the latest unconsumed OTP for that
       user. On success: mark consumed, issue a short-lived JWT, return
       it as `{ "access_token": ..., "token_type": "bearer", "expires_in": N }`.

    3. Client uses the JWT as `Authorization: Bearer <token>`.

    4. POST /api/auth/logout — pure client-side invalidation. Server
       returns 204. (We deliberately don't run a revocation list; JWT
       expiry does the job. If we ever need revocation, add a minimal
       blocklist keyed by jti.)

This module is a SCAFFOLD — the actual OTP generation, hashing, email
send, JWT issuance, and rate limiting arrive in the Phase 1 auth PR.
The route signatures are fixed so the UI can be built against them
while the backend catches up.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

router = APIRouter()


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


# ─── Routes ────────────────────────────────────────────────────────


@router.post(
    "/otp/request",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={204: {"description": "Always. Do not expose allowlist membership."}},
)
async def request_otp(payload: OtpRequest) -> None:
    """
    Request an OTP for `payload.email`.

    Returns 204 regardless of whether the email is allowlisted — this
    is intentional to prevent account enumeration.

    TODO(phase-1-auth): generate OTP, hash with argon2id, persist, email.
    """
    _ = payload  # stub
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="OTP request not yet implemented (scaffold only)",
    )


@router.post("/otp/verify", response_model=TokenResponse)
async def verify_otp(payload: OtpVerify) -> TokenResponse:
    """
    Verify an OTP; on success, return a signed JWT.

    TODO(phase-1-auth): argon2-verify, mark OTP consumed, issue JWT.
    """
    _ = payload  # stub
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="OTP verify not yet implemented (scaffold only)",
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> None:
    """
    Client-side logout. Server has no session to invalidate; JWT expiry
    handles it. Exists as a 204 so UIs can POST here and drop the token.
    """
    return
