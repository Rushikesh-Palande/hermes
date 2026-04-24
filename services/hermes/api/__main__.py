"""
Production entry point for the HERMES API service.

Invoked as either:
    python -m hermes.api
    uv run hermes-api
    hermes-api                    (after `pip install .`)

Runs under uvicorn. In production, systemd invokes this module; in
development, `uv run hermes-api` is the recommended path so env vars
from `.env` load automatically.

Gunicorn is NOT used — uvicorn's own process model (one worker per
process, one thread per request via asyncio) is sufficient for this
workload and avoids the gthread-vs-async impedance mismatch that
burned us in the legacy codebase.
"""

from __future__ import annotations

import uvicorn

from hermes.config import get_settings


def main() -> None:
    """Start the uvicorn server using config from env vars."""
    settings = get_settings()
    uvicorn.run(
        "hermes.api.main:create_app",
        factory=True,
        host=settings.hermes_api_host,
        port=settings.hermes_api_port,
        log_level=settings.hermes_api_log_level,
        reload=settings.hermes_dev_mode,
        access_log=settings.hermes_dev_mode,
        # One worker only: we're a single-device Pi deployment. Scaling
        # horizontally would require sticky sessions for SSE anyway.
        workers=1,
    )


if __name__ == "__main__":
    main()
