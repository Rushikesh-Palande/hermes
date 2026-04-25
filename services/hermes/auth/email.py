"""
SMTP send for OTP delivery.

Uses ``aiosmtplib`` so the call doesn't block the event loop. When SMTP
isn't configured (no ``SMTP_USER`` or ``SMTP_FROM``) we LOG the code at
INFO instead — useful for local development and CI, where there's no
real mail relay. Production deployments MUST configure SMTP.

Email body is plain text by design: keeps spam-filter heuristics happy
on cold infra and survives any client without an HTML renderer.
"""

from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from hermes.config import get_settings
from hermes.logging import get_logger

_log = get_logger(__name__, component="auth")


async def send_otp_code(*, to: str, code: str) -> None:
    """
    Email a 6-digit code to ``to``.

    No-op in dev (SMTP unconfigured): logs the code so the operator can
    grab it from journalctl. The OTP itself is identical to what would
    have been sent — so a request that hits this path is still
    completable end-to-end via the verify endpoint.
    """
    settings = get_settings()
    if not settings.smtp_user or not settings.smtp_from:
        _log.info(
            "otp_email_skipped_smtp_not_configured",
            to=to,
            code=code,  # only logged when SMTP is off
        )
        return

    msg = EmailMessage()
    msg["Subject"] = "HERMES login code"
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(
        f"Your HERMES login code is {code}.\n\n"
        f"It expires in {settings.otp_expiry_seconds // 60} minutes.\n"
        "If you did not request this code, you can ignore this message."
    )

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_pass.get_secret_value(),
        start_tls=True,
    )
    _log.info("otp_email_sent", to=to)
