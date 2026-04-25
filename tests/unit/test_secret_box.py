"""
Unit tests for the at-rest secret encryption (``hermes.auth.secret_box``).

The conftest sets ``HERMES_JWT_SECRET`` to a deterministic 64-char
string, so the derived Fernet key is stable across test runs and
across this whole test module.
"""

from __future__ import annotations

import pytest

from hermes.auth.secret_box import _box, decrypt, encrypt


def test_round_trip_recovers_plaintext() -> None:
    """Encrypt then decrypt returns the same string."""
    plaintext = "hunter2"
    token = encrypt(plaintext)
    assert decrypt(token) == plaintext


def test_round_trip_handles_unicode() -> None:
    plaintext = "påsswörd-Ω-🔒"
    assert decrypt(encrypt(plaintext)) == plaintext


def test_round_trip_handles_empty_string() -> None:
    """Fernet accepts empty payloads — useful for clearing a stored secret."""
    assert decrypt(encrypt("")) == ""


def test_token_does_not_contain_plaintext() -> None:
    """A token must not leak the plaintext literally (basic confidentiality)."""
    plaintext = "VERY_SECRET_VALUE_XYZ"
    token = encrypt(plaintext)
    assert plaintext not in token


def test_two_encryptions_produce_different_tokens() -> None:
    """Fernet uses a random IV — same plaintext encrypts to different tokens."""
    a = encrypt("same-plaintext")
    b = encrypt("same-plaintext")
    assert a != b
    # Both still decrypt back to the same plaintext.
    assert decrypt(a) == decrypt(b) == "same-plaintext"


def test_tampered_token_raises_value_error() -> None:
    """Modifying a single byte of the token must fail decryption."""
    token = encrypt("real-secret")
    # Flip a non-format byte in the middle. Avoid the version prefix
    # (first byte) — Fernet allows that to be present, but other bytes
    # are HMAC-protected.
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    with pytest.raises(ValueError, match="invalid or tampered"):
        decrypt(tampered)


def test_box_singleton_is_cached() -> None:
    """Repeated builds return the same Fernet — avoids HKDF on every call."""
    assert _box() is _box()
