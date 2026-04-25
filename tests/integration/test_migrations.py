"""
Migration smoke test.

The autouse ``_reset_schema`` fixture in ``conftest.py`` has already
dropped ``public``, re-applied every migration in ``migrations/``, and
disposed the SQLAlchemy engine. This test simply asserts that every
expected table is now present — catching drift between the SQL files
and the ORM models defined in ``hermes.db.models``.
"""

from __future__ import annotations

import asyncpg
import pytest

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
    settings = get_settings()
    conn: asyncpg.Connection = await asyncpg.connect(settings.migrate_database_url)
    try:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        found = {row["table_name"] for row in rows}
    finally:
        await conn.close()

    missing = EXPECTED_TABLES - found
    assert not missing, f"migrations did not produce expected tables: {missing}"
