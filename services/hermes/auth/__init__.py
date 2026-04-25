"""
Authentication subsystem.

Two flows live here:

    1. **OTP request → email**: a 6-digit code is generated, hashed
       with argon2id, persisted to ``user_otps``, and emailed via SMTP.
    2. **OTP verify → JWT**: the operator submits the code; we
       argon2-verify against the hash, mark the row consumed, and
       issue a short-lived JWT (HS256 / ``hermes_jwt_secret``).

Modules:
    jwt.py        — issue + decode JWTs
    otp.py        — generate, hash, verify OTP codes
    email.py      — SMTP send with a "log only" fallback for dev
    allowlist.py  — read the email allowlist file

The dev-mode bypass on ``CurrentUser`` (``HERMES_DEV_MODE=1`` returns a
synthetic admin) stays for tests + first-boot bootstrap; production
deployments turn it off and rely on JWT + OTP exclusively.
"""
