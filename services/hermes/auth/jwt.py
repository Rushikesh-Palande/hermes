"""
JWT issue + decode.

Tokens are HS256-signed with ``hermes_jwt_secret`` (32+ bytes, enforced
by Settings). Claims:

    sub  — user UUID (string)
    iat  — issued at (Unix seconds)
    exp  — expires at (Unix seconds)

Verification rejects: bad signature, expired token, malformed payload,
non-UUID ``sub``. Each is mapped to ``InvalidToken`` so callers don't
have to know the underlying PyJWT exception names.
"""

from __future__ import annotations

import time
import uuid

import jwt

# PyJWT's library-level exception is named InvalidTokenError too — alias
# its import so we don't shadow our own re-raised type.
from jwt import ExpiredSignatureError
from jwt import InvalidTokenError as _PyJWTInvalidTokenError

from hermes.config import get_settings

JWT_ALGORITHM: str = "HS256"


class InvalidTokenError(Exception):
    """Raised when a token cannot be decoded or fails any check."""


def issue_token(user_id: uuid.UUID, *, expiry_seconds: int | None = None) -> str:
    """Mint a fresh JWT for ``user_id``."""
    settings = get_settings()
    secret = settings.hermes_jwt_secret.get_secret_value()
    now = int(time.time())
    ttl = expiry_seconds if expiry_seconds is not None else settings.hermes_jwt_expiry_seconds
    payload: dict[str, object] = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> uuid.UUID:
    """
    Verify ``token`` and return the user UUID.

    Raises ``InvalidTokenError`` for any failure (bad signature, expired,
    malformed, non-UUID ``sub``). The route layer catches this and
    surfaces 401.
    """
    settings = get_settings()
    secret = settings.hermes_jwt_secret.get_secret_value()
    try:
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except ExpiredSignatureError as exc:
        raise InvalidTokenError("token expired") from exc
    except _PyJWTInvalidTokenError as exc:
        raise InvalidTokenError(f"invalid token: {exc}") from exc

    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise InvalidTokenError("token missing subject")
    try:
        return uuid.UUID(sub)
    except ValueError as exc:
        raise InvalidTokenError("subject is not a UUID") from exc
