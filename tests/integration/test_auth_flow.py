"""
End-to-end OTP → JWT flow.

Doesn't need an SMTP server: when ``smtp_user`` / ``smtp_from`` are
empty (the test default), ``send_otp_code`` skips the SMTP call and
logs the code instead. We pull the code straight from the DB by
generating it ourselves and then re-using the same hash, OR we hook
into the same flow by inserting an OTP row directly.

Approach: drive the request endpoint, then read back the latest
``UserOtp`` row, and verify against it (we can't see the plaintext,
but we can call ``verify`` with a guess and check the response). So
the easier route is to:

    1. Pre-create a User row and a known OTP whose code we control.
    2. Call /verify with that code.

Plus a couple of negative tests through /request to exercise the
allowlist + rate-limit gates.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from hermes.auth.otp import hash_code
from hermes.config import get_settings
from hermes.db.engine import async_session
from hermes.db.models import User, UserOtp


def _seed_allowlist(tmp_path: Path, *emails: str) -> Path:
    """Write an allowlist file under tmp_path and return the path."""
    p = tmp_path / "emails.txt"
    p.write_text("\n".join(emails) + "\n", encoding="utf-8")
    return p


async def _seed_user(email: str, *, enabled: bool = True) -> User:
    async with async_session() as session:
        u = User(email=email, is_admin=False, is_enabled=enabled)
        session.add(u)
        await session.flush()
        await session.refresh(u)
        return u


async def _seed_otp(user_id, code: str) -> None:
    async with async_session() as session:
        session.add(
            UserOtp(
                user_id=user_id,
                code_hash=hash_code(code),
                expires_at=datetime.now(tz=UTC) + timedelta(seconds=300),
            )
        )


@pytest.mark.db
@pytest.mark.asyncio
async def test_request_for_disallowed_email_returns_204_silently(
    api_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        get_settings(),
        "allowed_emails_path",
        _seed_allowlist(tmp_path, "allowed@example.com"),
    )
    resp = await api_client.post("/api/auth/otp/request", json={"email": "evil@example.com"})
    assert resp.status_code == 204
    # No User row created.
    async with async_session() as session:
        existing = (
            await session.execute(select(User).where(User.email == "evil@example.com"))
        ).scalar_one_or_none()
    assert existing is None


@pytest.mark.db
@pytest.mark.asyncio
async def test_request_for_allowed_email_creates_otp_row(
    api_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        get_settings(),
        "allowed_emails_path",
        _seed_allowlist(tmp_path, "alice@example.com"),
    )
    resp = await api_client.post("/api/auth/otp/request", json={"email": "alice@example.com"})
    assert resp.status_code == 204
    async with async_session() as session:
        rows = (
            (
                await session.execute(
                    select(UserOtp).join(User).where(User.email == "alice@example.com")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


@pytest.mark.db
@pytest.mark.asyncio
async def test_verify_with_correct_code_returns_jwt(api_client: AsyncClient) -> None:
    user = await _seed_user("alice@example.com")
    code = "424242"
    await _seed_otp(user.user_id, code)

    resp = await api_client.post(
        "/api/auth/otp/verify",
        json={"email": "alice@example.com", "otp": code},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and len(body["access_token"]) > 20
    assert body["expires_in"] > 0


@pytest.mark.db
@pytest.mark.asyncio
async def test_verify_with_wrong_code_returns_401(api_client: AsyncClient) -> None:
    user = await _seed_user("alice@example.com")
    await _seed_otp(user.user_id, "424242")

    resp = await api_client.post(
        "/api/auth/otp/verify",
        json={"email": "alice@example.com", "otp": "111111"},
    )
    assert resp.status_code == 401
    # Attempt counter advances.
    async with async_session() as session:
        otp = (
            await session.execute(select(UserOtp).where(UserOtp.user_id == user.user_id))
        ).scalar_one()
    assert otp.attempt_count == 1


@pytest.mark.db
@pytest.mark.asyncio
async def test_verify_for_unknown_email_returns_401(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/auth/otp/verify",
        json={"email": "ghost@example.com", "otp": "424242"},
    )
    assert resp.status_code == 401


@pytest.mark.db
@pytest.mark.asyncio
async def test_verify_disabled_user_returns_401(api_client: AsyncClient) -> None:
    user = await _seed_user("alice@example.com", enabled=False)
    code = "424242"
    await _seed_otp(user.user_id, code)
    resp = await api_client.post(
        "/api/auth/otp/verify",
        json={"email": "alice@example.com", "otp": code},
    )
    assert resp.status_code == 401


@pytest.mark.db
@pytest.mark.asyncio
async def test_jwt_round_trip_against_protected_route(api_client: AsyncClient) -> None:
    """A real JWT lets the holder access /api/devices even with the dev bypass on.

    The bypass only kicks in when no token is presented; sending a
    valid token must take the JWT path.
    """
    user = await _seed_user("alice@example.com")
    code = "424242"
    await _seed_otp(user.user_id, code)
    verify = await api_client.post(
        "/api/auth/otp/verify",
        json={"email": "alice@example.com", "otp": code},
    )
    token = verify.json()["access_token"]

    # Hit a CurrentUser-protected route with the token.
    resp = await api_client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


@pytest.mark.db
@pytest.mark.asyncio
async def test_protected_route_with_bad_jwt_returns_401(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get(
        "/api/devices",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


@pytest.mark.db
@pytest.mark.asyncio
async def test_max_attempts_locks_otp(api_client: AsyncClient) -> None:
    """``otp_max_attempts`` (default 5) wrong tries lock the OTP."""
    user = await _seed_user("alice@example.com")
    code = "424242"
    await _seed_otp(user.user_id, code)

    settings = get_settings()
    for _ in range(settings.otp_max_attempts):
        resp = await api_client.post(
            "/api/auth/otp/verify",
            json={"email": "alice@example.com", "otp": "111111"},
        )
        assert resp.status_code == 401

    # The (max+1)-th attempt — even with the CORRECT code — must fail
    # because the OTP is locked.
    resp = await api_client.post(
        "/api/auth/otp/verify",
        json={"email": "alice@example.com", "otp": code},
    )
    assert resp.status_code == 401


@pytest.mark.db
@pytest.mark.asyncio
async def test_logout_returns_204(api_client: AsyncClient) -> None:
    resp = await api_client.post("/api/auth/logout")
    assert resp.status_code == 204
