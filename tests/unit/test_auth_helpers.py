"""
Unit tests for the auth helper modules — JWT, OTP, allowlist.

No DB or SMTP needed: all three layers are pure Python with the only
"side effect" being the JWT secret pulled from Settings (the conftest
seeds ``HERMES_JWT_SECRET`` already).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

from hermes.auth import allowlist
from hermes.auth.jwt import InvalidTokenError, decode_token, issue_token
from hermes.auth.otp import generate_code, hash_code, verify_code

# ─── OTP ────────────────────────────────────────────────────────────


def test_generate_code_is_six_digits() -> None:
    for _ in range(100):
        code = generate_code()
        assert len(code) == 6
        assert code.isdigit()


def test_hash_and_verify_round_trip() -> None:
    code = "123456"
    digest = hash_code(code)
    assert digest != code  # never store the plaintext
    assert verify_code(code, digest)


def test_verify_rejects_wrong_code() -> None:
    digest = hash_code("123456")
    assert not verify_code("000000", digest)


def test_each_hash_uses_a_fresh_salt() -> None:
    """Two hashes of the same code differ — no static salt leakage."""
    a = hash_code("123456")
    b = hash_code("123456")
    assert a != b
    assert verify_code("123456", a)
    assert verify_code("123456", b)


# ─── JWT ────────────────────────────────────────────────────────────


def test_jwt_round_trip() -> None:
    user_id = uuid.uuid4()
    token = issue_token(user_id)
    assert isinstance(token, str)
    assert decode_token(token) == user_id


def test_jwt_expired_token_raises_invalid() -> None:
    """A token with a 1 s TTL must reject after expiry."""
    user_id = uuid.uuid4()
    token = issue_token(user_id, expiry_seconds=1)
    time.sleep(1.5)
    with pytest.raises(InvalidTokenError):
        decode_token(token)


def test_jwt_tampered_signature_rejected() -> None:
    user_id = uuid.uuid4()
    token = issue_token(user_id)
    # Flip the last character of the signature.
    bad = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(InvalidTokenError):
        decode_token(bad)


def test_jwt_garbage_token_rejected() -> None:
    with pytest.raises(InvalidTokenError):
        decode_token("not-a-real-token")


# ─── Allowlist ─────────────────────────────────────────────────────


def test_allowlist_missing_file_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No file ⇒ no addresses; logs a warning, returns False."""
    from hermes.config import get_settings

    s = get_settings()
    # Point the setting at a path that definitely doesn't exist. We
    # mutate via monkeypatch so other tests in the session aren't
    # affected.
    monkeypatch.setattr(s, "allowed_emails_path", Path("/nope/does-not-exist.txt"))
    assert not allowlist.is_allowed("anyone@example.com")


def test_allowlist_reads_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "emails.txt"
    p.write_text(
        "# leading comment\n"
        "alice@example.com\n"
        "\n"  # blank line
        "BOB@example.com\n"  # case-insensitive match expected
        "# trailing comment\n",
        encoding="utf-8",
    )
    from hermes.config import get_settings

    monkeypatch.setattr(get_settings(), "allowed_emails_path", p)

    assert allowlist.is_allowed("alice@example.com")
    assert allowlist.is_allowed("ALICE@example.com")  # case-insensitive caller
    assert allowlist.is_allowed("bob@example.com")  # case-insensitive entry
    assert not allowlist.is_allowed("eve@example.com")
    assert not allowlist.is_allowed("")  # empty input rejected


def test_allowlist_strips_whitespace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "emails.txt"
    p.write_text("  alice@example.com  \n", encoding="utf-8")
    from hermes.config import get_settings

    monkeypatch.setattr(get_settings(), "allowed_emails_path", p)
    assert allowlist.is_allowed("alice@example.com")
