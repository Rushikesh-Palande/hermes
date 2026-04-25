-- 0005_retention_policies.sql
--
-- TimescaleDB retention and compression policies. Separated from table
-- creation because (a) operators may want to tune these per deployment
-- (e.g. a small-SSD Pi vs a full server) and (b) they depend on the
-- hypertables from 0003 already existing.
--
-- Retention summary (see §6 of DATABASE_REDESIGN.md):
--   * events             — kept forever; compressed after 30 days.
--   * event_windows      — application-managed cleanup (see TODO below).
--   * session_samples    — compressed after 1 hour; retention is tied
--                          to the session's lifecycle, handled in
--                          application code (not a retention policy).
--
-- To change retention at runtime, use:
--   SELECT remove_retention_policy('events');
--   SELECT add_retention_policy('events', INTERVAL '6 months');

BEGIN;

-- Compress events chunks older than 30 days. Keeps recent-range queries
-- fast on the row store; archives older data in column-store form.
SELECT add_compression_policy(
    'events',
    INTERVAL '30 days',
    if_not_exists => TRUE
);

-- TODO(phase-5+): make event_windows a hypertable and re-add a retention
-- policy here. Today the table is a plain BIGINT-PK table — Timescale's
-- add_retention_policy() requires a hypertable target (and the partition
-- column must be part of every unique constraint). Promoting it to a
-- hypertable on `start_ts` therefore needs a composite-PK schema change
-- in 0002 + a follow-up ORM update. Until that lands, application code
-- (a periodic job) handles cleanup of old window BLOBs.

-- Compress session_samples chunks as soon as they close (1 h boundary).
-- At 30 k rows/second, a 1 h chunk is ~100 M rows before compression —
-- without this policy the raw table would be untenable on a Pi SSD.
SELECT add_compression_policy(
    'session_samples',
    INTERVAL '1 hour',
    if_not_exists => TRUE
);

COMMIT;
