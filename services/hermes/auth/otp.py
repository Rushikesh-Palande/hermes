"""
OTP generation, hashing, verification.

Uses argon2id (via argon2-cffi) so a leaked ``user_otps.code_hash``
column does not leak the codes themselves. Codes are 6-digit
zero-padded strings drawn from ``secrets.randbelow``; a 6-digit code
gives 1 in 1 000 000 odds per guess, paired with the
``otp_max_attempts`` cap (default 5) to bound brute force.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# A single PasswordHasher with default Argon2id params: m=64MiB,
# t=3, p=4 — strong enough for 6-digit codes that only live ~5 min.
_HASHER = PasswordHasher()


def generate_code() -> str:
    """Return a uniformly-random 6-digit zero-padded code."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code: str) -> str:
    """argon2id-hash the code; safe to persist in ``user_otps.code_hash``."""
    return _HASHER.hash(code)


def verify_code(code: str, code_hash: str) -> bool:
    """Return True iff ``code`` matches the stored ``code_hash``."""
    try:
        _HASHER.verify(code_hash, code)
    except VerifyMismatchError:
        return False
    return True
