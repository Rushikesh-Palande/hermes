"""
FastAPI application factory.

``create_app()`` returns a fully-wired ASGI app. It is used by:

    * ``hermes.api.__main__`` — production entry (uvicorn serves it).
    * Test code — ``httpx.AsyncClient(app=create_app(), ...)`` for
      integration tests that don't need a real network socket.

Lifespan (startup → yield → shutdown):

    Startup
        * Configure structlog.
        * Read settings (fail-fast on bad env).
        * Start the embedded IngestPipeline (MQTT consumer + live ring
          buffer). The SSE endpoint reads from ``app.state.live_data``.

    Shutdown
        * Stop the ingest pipeline (drain queue, disconnect MQTT).
        * Dispose the SQLAlchemy engine (close pool, exit cleanly).

Why run the MQTT consumer inside the API process (for now):

    The legacy system did exactly this (Flask + background thread) and it
    scaled to 20 devices × 12 sensors × 123 Hz on a Raspberry Pi 4. The
    SSE endpoint needs direct access to the ring buffer, and an in-process
    LiveDataHub avoids a Redis round-trip on every frame. If we ever need
    horizontal scaling, we split ingest into its own process and back the
    hub with Redis — but that's a Phase-5+ concern.

CORS is intentionally NOT enabled here. In production the UI is served
from the same origin as the API via nginx; in development the Vite dev
server proxies ``/api`` to the FastAPI port, so same-origin still holds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from hermes import __version__
from hermes.api.routes import (
    auth,
    config,
    devices,
    events,
    health,
    live_stream,
    offsets,
)
from hermes.config import get_settings
from hermes.db.engine import dispose_engine
from hermes.detection.db_config import DbConfigProvider
from hermes.detection.session import ensure_default_session
from hermes.ingest.main import IngestPipeline
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

    # Bootstrap a default Package + Session so detected events have
    # somewhere to persist and detector configs have somewhere to live.
    # Failures fall back to in-memory-only mode (no DB writes / static
    # configs) so /api/health stays green when the schema isn't ready.
    session_id: object | None = None
    config_provider: DbConfigProvider | None = None
    try:
        session_id, package_id = await ensure_default_session()
        config_provider = DbConfigProvider(package_id)
        await config_provider.reload()
    except Exception:
        log.exception("session_or_config_bootstrap_failed_continuing")

    # Spin up the embedded MQTT ingest pipeline. Failures here are
    # non-fatal — we want /api/health liveness to stay green even if the
    # broker is down, so the API can still serve static routes.
    pipeline = IngestPipeline(settings, session_id=session_id, config_provider=config_provider)
    try:
        await pipeline.start()
    except Exception:
        log.exception("ingest_start_failed_continuing")
    app.state.live_data = pipeline.live_data
    app.state.ingest_pipeline = pipeline
    app.state.config_provider = config_provider

    try:
        yield
    finally:
        log.info("api_stopping")
        try:
            await pipeline.stop()
        except Exception:
            log.exception("ingest_stop_failed")
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
            "Sensor telemetry + event detection API for the HERMES industrial monitoring platform."
        ),
        lifespan=_lifespan,
    )

    # Public (no auth). Mounted at /api/health so that liveness lives at
    # /api/health and readiness at /api/health/ready.
    app.include_router(health.router, prefix="/api/health", tags=["health"])

    # Authentication — issues OTPs, verifies, returns JWT.
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])

    # Authenticated resources.
    app.include_router(devices.router, prefix="/api/devices", tags=["devices"])
    app.include_router(events.router, prefix="/api/events", tags=["events"])
    app.include_router(config.router, prefix="/api/config", tags=["config"])
    app.include_router(
        offsets.router,
        prefix="/api/devices/{device_id}/offsets",
        tags=["offsets"],
    )

    # Live SSE telemetry feed. Public for now; will move behind auth in
    # Phase 4 (auth polish) once the JWT flow is wired through the UI.
    app.include_router(live_stream.router, prefix="/api/live_stream", tags=["live"])

    return app
