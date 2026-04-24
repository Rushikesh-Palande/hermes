-- 0001_init_extensions.sql
-- Part of the Phase 1 foundation. Enables PostgreSQL extensions required
-- by the HERMES schema. Idempotent; safe to replay.

BEGIN;

-- TimescaleDB provides hypertables, retention policies, and column-store
-- compression for the `events` and `session_samples` tables.
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- pgcrypto gives us gen_random_uuid() for primary keys.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

COMMIT;
