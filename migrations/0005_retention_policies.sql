-- 0005_retention_policies.sql
--
-- TimescaleDB retention and compression policies. Separated from table
-- creation because (a) operators may want to tune these per deployment
-- (e.g. a small-SSD Pi vs a full server) and (b) they depend on the
-- hypertables from 0003 already existing.
--
-- Retention summary (see §6 of DATABASE_REDESIGN.md):
--   * events             — kept forever; compressed after 30 days.
--   * event_windows      — dropped after 1 year (raw ±9s BLOB is bulky;
--                          events still carry summary metadata).
--   * session_samples    — compressed after 1 hour; retention is tied
--                          to the session's lifecycle, handled in
--                          application code (not a retention policy).
--
-- To change retention at runtime, use:
--   SELECT remove_retention_policy('event_windows');
--   SELECT add_retention_policy('event_windows', INTERVAL '6 months');

BEGIN;

-- Compress events chunks older than 30 days. Keeps recent-range queries
-- fast on the row store; archives older data in column-store form.
SELECT add_compression_policy(
    'events',
    INTERVAL '30 days',
    if_not_exists => TRUE
);

-- Drop event_windows older than 1 year. The parent events row survives
-- forever; only the ±9s waveform BLOB is released.
SELECT add_retention_policy(
    'event_windows',
    INTERVAL '1 year',
    if_not_exists => TRUE
);

-- Compress session_samples chunks as soon as they close (1 h boundary).
-- At 30 k rows/second, a 1 h chunk is ~100 M rows before compression —
-- without this policy the raw table would be untenable on a Pi SSD.
SELECT add_compression_policy(
    'session_samples',
    INTERVAL '1 hour',
    if_not_exists => TRUE
);

COMMIT;
