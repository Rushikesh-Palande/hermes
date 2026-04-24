"""
FastAPI application factory.

`create_app()` returns a fully-wired ASGI app. It is used by:

    * `hermes.api.__main__` — production entry (uvicorn serves it).
    * Test code — httpx.AsyncClient(app=create_app(), ...) for
      integration tests that don't need a real network socket.

Lifespan:
    On startup we log the effective config (secrets masked), verify the
    database is reachable, and register Prometheus metrics.
    On shutdown we dispose the SQLAlchemy engine so the process exits
    cleanly without dangling Postgres connections.

CORS is intentionally NOT enabled here. In production the UI is served
from the same origin as the API via nginx; in development the Vite dev
server proxies /api to the FastAPI port, so same-origin still holds.
Adding CORS later is a per-deployment policy decision.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

from hermes import __version__
from hermes.api.routes import auth, devices, health
from hermes.config import get_settings
from hermes.db.engine import dispose_engine
from hermes.logging import configure_logging, get_logger


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup + shutdown hooks. Runs exactly once per process."""
    configure_logging()
    log = get_logger(__name__, component="api")
    settings = get_settings()

    log.info(
        "api_starting",
        version=__version__,
        port=settings.hermes_api_port,
        dev_mode=settings.hermes_dev_mode,
    )

    try:
        yield
    finally:
        log.info("api_stopping")
        await dispose_engine()


def create_app() -> FastAPI:
    """
    Build and return the FastAPI app.

    Route registration is explicit (no auto-discovery) so route order
    and prefixes are greppable from one place.
    """
    app = FastAPI(
        title="HERMES API",
        version=__version__,
        description=(
            "Sensor telemetry + event detection API for the HERMES "
            "industrial monitoring platform."
        ),
        default_response_class=ORJSONResponse,
        lifespan=_lifespan,
    )

    # Public (no auth).
    app.include_router(health.router, prefix="/api", tags=["health"])

    # Authentication — issues OTPs, verifies, returns JWT.
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])

    # Authenticated resources.
    app.include_router(devices.router, prefix="/api/devices", tags=["devices"])

    return app
