"""
At-rest symmetric encryption for sensitive operator-configured secrets.

Used for the MQTT broker password (gap 4) and reserved for any future
"the operator types a password into the UI and it lands in Postgres"
flows. NOT used for OTP hashing (that's argon2 in ``otp.py``) or JWT
signing (HS256 in ``jwt.py``).

Why Fernet (cryptography lib) and not raw AES:
    Fernet bundles AES-128-CBC + HMAC-SHA256 + a versioned token format
    + monotonic timestamp + random IV. It's the right primitive for
    "encrypt this short string, decrypt later, rotate the key when the
    operator rotates HERMES_JWT_SECRET".

Why derive the key from HERMES_JWT_SECRET instead of a new env var:
    * One secret to manage — operators already store JWT_SECRET safely.
    * Rotating JWT_SECRET already invalidates every active session;
      requiring re-entry of the broker password fits the same "reset
      everything" mental model.
    * Domain separation via the HKDF info string ("hermes:secret_box.v1")
      means a leaked Fernet key can't forge JWTs and vice versa.

Key derivation: HKDF-SHA256(secret, salt=b"hermes/secret_box/v1",
length=32). The result is base64-encoded for Fernet.

Token format on disk: a Fernet token is itself a urlsafe-base64 string,
stored verbatim in ``mqtt_brokers.password_enc``. Unmodified tokens
round-trip; tampered tokens fail verification at decrypt time.
"""

from __future__ import annotations

import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


@lru_cache(maxsize=1)
def _box() -> Fernet:
    """Build the process-wide Fernet from the configured JWT secret.

    Cached because Fernet construction does an HMAC key schedule that
    we don't want to redo per encrypt/decrypt call. Cache invalidates
    naturally when the process restarts; tests that rotate the secret
    can call ``_box.cache_clear()``.
    """
    from hermes.config import get_settings

    secret = get_settings().hermes_jwt_secret.get_secret_value().encode("utf-8")
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"hermes/secret_box/v1",
        info=b"hermes:secret_box.v1",
    ).derive(secret)
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 string and return a Fernet token (urlsafe-base64)."""
    return _box().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a Fernet token written by ``encrypt``. Raises on tampering."""
    try:
        return _box().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        # Surface as a domain-specific error rather than leak Fernet
        # internals to the caller.
        raise ValueError("secret_box: invalid or tampered token") from exc
