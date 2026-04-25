"""
Integration-test bootstrap.

Every integration test runs against a freshly-initialised Postgres
schema. We drop and recreate ``public``, then replay every SQL file in
``migrations/`` once per test. The cost is a fraction of a second in CI;
the benefit is zero cross-test contamination.

Migrations are not idempotent (``CREATE TYPE`` has no ``IF NOT EXISTS``
form in Postgres 16), so reuse-without-reset is not an option.

The SQLAlchemy engine singleton in ``hermes.db.engine`` is disposed here
too — after a schema drop, pooled connections may hold stale plan caches
that crash the first query with ``cached plan must not change result
type``. Disposing forces a clean reconnect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest_asyncio

from hermes.config import get_settings
from hermes.db.engine import dispose_engine


@pytest_asyncio.fixture(autouse=True)
async def _reset_schema() -> AsyncIterator[None]:
    """
    Drop + recreate ``public`` schema, replay migrations, dispose the
    engine pool so the next query opens a fresh connection.
    """
    settings = get_settings()

    migrations_dir = Path(__file__).parents[2] / "migrations"
    migration_files = sorted(migrations_dir.glob("00*.sql"))

    conn: asyncpg.Connection = await asyncpg.connect(settings.migrate_database_url)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        for path in migration_files:
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()

    # Force a fresh connection for the next test; pooled connections cache
    # plans keyed by the dropped schema's OIDs.
    await dispose_engine()

    yield
