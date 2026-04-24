"""
Database layer for HERMES.

Modules:
    engine  — async SQLAlchemy engine + session factory.
    models  — declarative ORM models mirroring `migrations/0002_core_tables.sql`.

The ORM is NOT the source of truth for the schema — `migrations/*.sql` is.
Models are hand-maintained to match, and a migration test verifies the
two stay in sync.
"""
