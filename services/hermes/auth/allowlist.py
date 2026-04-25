"""
Email allowlist for OTP login.

A login attempt by an address that isn't in the allowlist is rejected
without sending mail. The list lives in a plain text file at
``settings.allowed_emails_path`` — one email per line, lines starting
with ``#`` are comments, blank lines are skipped.

We re-read the file on every check rather than caching it: the file is
tiny, file-system reads are cheap, and operators routinely add /
remove entries without restarting the API. If this becomes a hotspot
we'll add a 60 s TTL cache; for now simplicity beats cleverness.
"""

from __future__ import annotations

from pathlib import Path

from hermes.config import get_settings
from hermes.logging import get_logger

_log = get_logger(__name__, component="auth")


def is_allowed(email: str) -> bool:
    """Return True iff ``email`` (case-insensitive) is in the allowlist file."""
    path = get_settings().allowed_emails_path
    needle = email.strip().lower()
    if not needle:
        return False

    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        # No file = nobody is allowed. Operators see this as "your address
        # is not on the list", which is the correct user-facing semantic.
        _log.warning("allowlist_file_missing", path=str(path))
        return False

    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.lower() == needle:
            return True
    return False
