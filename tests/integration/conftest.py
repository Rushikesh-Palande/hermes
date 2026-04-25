"""
Integration-test bootstrap.

Strategy: apply all migrations exactly ONCE per pytest session, then
truncate every public table before each test. This sidesteps two
problems with the prior "drop schema + re-migrate every test" approach:

    1. ``DROP SCHEMA public CASCADE`` removes the ``timescaledb``
       extension catalog entry, but the shared library stays loaded in
       the Postgres backend. The next ``CREATE EXTENSION timescaledb``
       on the same backend session raises:
          "extension already loaded with another version".
       The official workaround is "open a fresh session" — but asyncpg
       and the Postgres pool happily reuse the same backend, so the
       extension state leaks across tests.

    2. ``CREATE TYPE`` (used by 0002 for the enums) has no IF NOT EXISTS
       form, so re-running migrations against a partially-cleaned
       schema is a coin flip on whether it errors.

Truncate-after-init is the canonical Postgres test-isolation pattern;
it leaves the extension and type catalog untouched and is one to two
orders of magnitude faster than re-running DDL between tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest_asyncio

from hermes.config import get_settings
from hermes.db.engine import dispose_engine

# Module-level guard so migrations apply exactly once per session.
# pytest collects all integration tests in one process, so a plain
# bool is enough — no need for cross-process locking.
_schema_initialised: bool = False


_DROP_USER_OBJECTS_SQL = """
DO $$
DECLARE
    r RECORD;
BEGIN
    -- Drop user tables. CASCADE picks up FKs and dependent indexes.
    -- We DO NOT drop the public schema itself — that would unload the
    -- timescaledb extension, which can't be reloaded in the same
    -- backend session (see module docstring).
    FOR r IN (
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
    ) LOOP
        EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
    END LOOP;

    -- Drop user-defined enums (CREATE TYPE is not idempotent in 0002).
    FOR r IN (
        SELECT t.typname
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typtype = 'e'
    ) LOOP
        EXECUTE 'DROP TYPE IF EXISTS public.' || quote_ident(r.typname) || ' CASCADE';
    END LOOP;
END $$;
"""

_TRUNCATE_ALL_SQL = """
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
    ) LOOP
        EXECUTE 'TRUNCATE TABLE public.'
            || quote_ident(r.tablename) || ' RESTART IDENTITY CASCADE';
    END LOOP;
END $$;
"""


@pytest_asyncio.fixture(autouse=True)
async def _reset_schema() -> AsyncIterator[None]:
    """
    Ensure migrations are applied (once per session) and truncate every
    table before yielding to the test.

    The SQLAlchemy engine is disposed at the end so subsequent tests
    don't reuse pooled connections that hold stale type-OID plan caches
    after a TRUNCATE … RESTART IDENTITY.
    """
    global _schema_initialised
    settings = get_settings()

    conn: asyncpg.Connection = await asyncpg.connect(settings.migrate_database_url)
    try:
        if not _schema_initialised:
            # First test of the session: clear any leftover objects from
            # a prior run, then apply every migration in order.
            await conn.execute(_DROP_USER_OBJECTS_SQL)
            migrations_dir = Path(__file__).parents[2] / "migrations"
            for path in sorted(migrations_dir.glob("00*.sql")):
                await conn.execute(path.read_text(encoding="utf-8"))
            _schema_initialised = True
        else:
            await conn.execute(_TRUNCATE_ALL_SQL)
    finally:
        await conn.close()

    await dispose_engine()

    yield
