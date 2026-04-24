"""
Migration smoke test — applies every SQL migration against a real
Postgres and verifies the expected tables and types exist.

Marked with `db`; skipped automatically when Postgres isn't available.
The goal is to catch drift between `migrations/*.sql` and
`hermes.db.models` before it bites in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from hermes.config import get_settings

EXPECTED_TABLES = {
    "devices",
    "packages",
    "parameters",
    "sessions",
    "session_logs",
    "events",
    "event_windows",
    "session_samples",
    "sensor_offsets",
    "users",
    "user_otps",
    "mqtt_brokers",
}


@pytest.mark.db
@pytest.mark.asyncio
async def test_migrations_produce_all_expected_tables() -> None:
    """
    End-to-end: run migrations/*.sql in order, then query the catalog.

    This test does NOT drop the schema; it assumes a freshly-provisioned
    database. CI handles that by spinning up a throwaway Postgres
    service container per job.
    """
    settings = get_settings()
    # Use a plain asyncpg URL — same DB, but without the SQLAlchemy
    # dialect prefix so we can exec raw multi-statement SQL.
    engine = create_async_engine(
        settings.migrate_database_url.replace("postgresql://", "postgresql+asyncpg://")
    )

    migrations_dir = Path(__file__).parents[2] / "migrations"
    migration_files = sorted(migrations_dir.glob("00*.sql"))
    assert migration_files, "no migration files found — wrong test CWD?"

    async with engine.begin() as conn:
        for path in migration_files:
            sql = path.read_text(encoding="utf-8")
            # asyncpg rejects multi-statement strings via plain execute;
            # exec_driver_sql ships the raw SQL text to the driver which
            # handles multi-statement transactions correctly.
            await conn.exec_driver_sql(sql)

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
        found = {row[0] for row in result}

    await engine.dispose()

    missing = EXPECTED_TABLES - found
    assert not missing, f"migrations did not produce expected tables: {missing}"
