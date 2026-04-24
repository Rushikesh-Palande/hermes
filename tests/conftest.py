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
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    yield


@pytest_asyncio.fixture
async def api_client() -> AsyncIterator[AsyncClient]:
    """
    HTTPX client wired directly into the FastAPI app via ASGI transport.

    No real socket is opened — requests dispatch straight to route
    handlers. Integration tests that need a real socket should use
    the `live_server` fixture (lands in a later PR).
    """
    from hermes.api.main import create_app  # local import: avoids cost when tests skip

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
