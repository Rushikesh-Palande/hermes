-- 0003_hypertables.sql
--
-- Converts `events` and `session_samples` into TimescaleDB hypertables.
-- Runs after 0002 so the tables already exist in their OLTP shape; this
-- migration adds the partitioning + compression behaviour layered on top.
--
-- Why two different chunk intervals:
--   * events — 1 day. At ~1 event per device per hour, ~20 devices, a
--     daily chunk holds ~500 rows. Keeps recent-range scans cheap and
--     keeps compression granularity aligned with operator mental model
--     ("yesterday's events").
--   * session_samples — 1 hour. This table accepts 123 Hz × 12 sensors
--     × 20 devices = ~30 k rows/second, so smaller chunks keep write
--     I/O bounded and let compression trigger quickly.

BEGIN;

-- ─── events ────────────────────────────────────────────────────────

SELECT create_hypertable(
    'events',
    'triggered_at',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists       => TRUE
);

-- Indexes created in 0002 automatically propagate to each chunk.

-- ─── session_samples ───────────────────────────────────────────────

SELECT create_hypertable(
    'session_samples',
    'ts',
    chunk_time_interval => INTERVAL '1 hour',
    if_not_exists       => TRUE
);

-- Compression settings. `segmentby` keys group rows for dictionary
-- compression; `orderby` keys enable run-length encoding on timestamps.
-- A typical sensor stream compresses 10–20× with these settings.
ALTER TABLE session_samples SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'session_id, device_id, sensor_id',
    timescaledb.compress_orderby   = 'ts'
);

-- Index created AFTER ALTER TABLE so it lives on the compressed shape too.
CREATE INDEX IF NOT EXISTS session_samples_lookup
    ON session_samples (session_id, device_id, sensor_id, ts DESC);

COMMIT;
