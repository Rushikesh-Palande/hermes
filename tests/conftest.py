"""
Shared pytest fixtures.

Scope conventions:
    * session-scoped fixtures for anything slow (engine, app).
    * function-scoped for per-test state (db transactions, auth tokens).

Marker conventions (declared in pyproject.toml):
    * `db`   — requires a running Postgres. Skipped if DATABASE_URL is unset.
    * `mqtt` — requires a running Mosquitto.

A fixture that touches the DB SHOULD NOT mock it. Integration tests run
against a real Postgres via docker-compose.dev.yml; unit tests avoid
the DB entirely.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture(scope="session", autouse=True)
def _load_env_defaults() -> Iterator[None]:
    """
    Provide safe defaults for env vars the Settings class requires.

    Test code should not leak secrets into the repo; these values are
    deliberately non-functional (database_url points nowhere usable).
    Tests that actually need the DB use the `db_session` fixture below.
    """
    defaults = {
        "DATABASE_URL": "postgresql+asyncpg://hermes_app:test@localhost:5432/hermes_test",
        "MIGRATE_DATABASE_URL": "postgresql://hermes_migrate:test@localhost:5432/hermes_test",
        "HERMES_JWT_SECRET": "x" * 64,
        "HERMES_LOG_FORMAT": "console",
        # Enables the dev-mode auth bypass in `get_current_user` so
        # CurrentUser-protected routes don't need a JWT in tests. The
        # real flow lands in Phase 3.5.
        "HERMES_DEV_MODE": "1",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    yield


@pytest_asyncio.fixture
async def api_client() -> AsyncIterator[AsyncClient]:
    """
    HTTPX client wired directly into the FastAPI app via ASGI transport.

    No real socket is opened — requests dispatch straight to route
    handlers. ASGITransport does NOT trigger lifespan events, so this
    fixture mounts ``app.state`` manually for the routes that need it
    (config endpoints rely on a live ``config_provider``).

    The MQTT pipeline is NOT started here — tests don't talk to a broker.
    The detection engine on the stub pipeline is real, so reset_device()
    calls from the config routes work as in production.
    """
    from hermes.api.main import create_app
    from hermes.config import get_settings

    app = create_app()
    transport = ASGITransport(app=app)

    # Best-effort state setup. If the DB isn't reachable (e.g. unit-only
    # test runs that pull this fixture indirectly), leave state empty so
    # routes that don't need it still work; routes that do need it 503.
    try:
        from hermes.detection.db_config import DbConfigProvider
        from hermes.detection.engine import DetectionEngine
        from hermes.detection.session import ensure_default_session
        from hermes.detection.sink import LoggingEventSink

        _, package_id = await ensure_default_session()
        provider = DbConfigProvider(package_id)
        await provider.reload()
        engine = DetectionEngine(provider, LoggingEventSink())

        class _StubPipeline:
            """Just enough surface for config routes to call reset_device."""

            def __init__(self) -> None:
                self.detection_engine = engine

        app.state.config_provider = provider
        app.state.ingest_pipeline = _StubPipeline()
    except Exception:
        # Tests that don't need the DB-backed state work without it.
        pass

    # Settings is imported solely to ensure the env defaults applied
    # above produced a valid Settings — fail fast if not.
    get_settings()

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
