-- 0002_core_tables.sql
-- Core tables for the HERMES data model. Implements §3 of
-- docs/design/DATABASE_REDESIGN.md:
--
--   devices → packages → parameters
--                      → sessions → session_logs → events → event_windows
--                                                        → session_samples
--   sensor_offsets, users, user_otps, mqtt_brokers.
--
-- Hypertables and retention policies are configured in subsequent
-- migrations (0003 and 0005) so this file can be replayed in isolation.

BEGIN;

-- ─── Enums ─────────────────────────────────────────────────────────

CREATE TYPE parameter_scope    AS ENUM ('global', 'device', 'sensor');
CREATE TYPE session_scope      AS ENUM ('global', 'local');
CREATE TYPE session_log_event  AS ENUM
    ('start', 'stop', 'pause', 'resume', 'reconfigure', 'error');
CREATE TYPE event_type         AS ENUM ('A', 'B', 'C', 'D', 'BREAK');
CREATE TYPE device_protocol    AS ENUM ('mqtt', 'modbus_tcp');

-- ─── Devices ───────────────────────────────────────────────────────

CREATE TABLE devices (
    device_id       INTEGER         PRIMARY KEY,
    name            TEXT            NOT NULL,
    protocol        device_protocol NOT NULL DEFAULT 'mqtt',
    topic           TEXT,
    modbus_config   JSONB,
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT devices_id_range
        CHECK (device_id BETWEEN 1 AND 999),
    CONSTRAINT devices_modbus_has_config
        CHECK (protocol = 'mqtt' OR modbus_config IS NOT NULL)
);

-- ─── Packages ──────────────────────────────────────────────────────

CREATE TABLE packages (
    package_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT        NOT NULL,
    description         TEXT,
    is_default          BOOLEAN     NOT NULL DEFAULT FALSE,
    is_locked           BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by          TEXT,
    archived_at         TIMESTAMPTZ,
    parent_package_id   UUID        REFERENCES packages(package_id),

    CONSTRAINT packages_archival_order
        CHECK (archived_at IS NULL OR archived_at >= created_at)
);

-- Exactly one active default package at any time.
CREATE UNIQUE INDEX packages_only_one_default
    ON packages ((1))
    WHERE is_default = TRUE AND archived_at IS NULL;

CREATE INDEX packages_name ON packages (name) WHERE archived_at IS NULL;

-- ─── Parameters ────────────────────────────────────────────────────

CREATE TABLE parameters (
    parameter_id    BIGINT          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    package_id      UUID            NOT NULL REFERENCES packages(package_id) ON DELETE CASCADE,
    scope           parameter_scope NOT NULL,
    device_id       INTEGER         REFERENCES devices(device_id),
    sensor_id       SMALLINT,
    key             TEXT            NOT NULL,
    value           JSONB           NOT NULL,

    CONSTRAINT parameters_device_required_when_scoped
        CHECK (scope = 'global' OR device_id IS NOT NULL),
    CONSTRAINT parameters_sensor_only_at_sensor_scope
        CHECK (scope = 'sensor' OR sensor_id IS NULL),
    CONSTRAINT parameters_sensor_range
        CHECK (
            (scope = 'sensor' AND sensor_id BETWEEN 1 AND 12)
            OR scope <> 'sensor'
        )
);

-- A given key can appear at most once per (package, scope, device, sensor).
-- COALESCE lets unique index span nullable device_id / sensor_id.
CREATE UNIQUE INDEX parameters_uniq
    ON parameters (
        package_id,
        scope,
        COALESCE(device_id, 0),
        COALESCE(sensor_id, 0),
        key
    );

CREATE INDEX parameters_lookup
    ON parameters (package_id, scope, device_id, sensor_id);

-- ─── Sessions ──────────────────────────────────────────────────────

CREATE TABLE sessions (
    session_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    scope               session_scope NOT NULL,
    parent_session_id   UUID          REFERENCES sessions(session_id),
    device_id           INTEGER       REFERENCES devices(device_id),
    package_id          UUID          NOT NULL REFERENCES packages(package_id),
    started_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    ended_at            TIMESTAMPTZ,
    started_by          TEXT,
    ended_reason        TEXT,
    notes               TEXT,
    record_raw_samples  BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT sessions_scope_shape CHECK (
        (scope = 'global' AND parent_session_id IS NULL AND device_id IS NULL)
        OR
        (scope = 'local'  AND parent_session_id IS NOT NULL AND device_id IS NOT NULL)
    ),
    CONSTRAINT sessions_timing_order
        CHECK (ended_at IS NULL OR ended_at >= started_at)
);

-- At most one active global session at any moment.
CREATE UNIQUE INDEX sessions_one_active_global
    ON sessions ((1))
    WHERE scope = 'global' AND ended_at IS NULL;

-- At most one active local session per device.
CREATE UNIQUE INDEX sessions_one_active_local_per_device
    ON sessions (device_id)
    WHERE scope = 'local' AND ended_at IS NULL;

CREATE INDEX sessions_active        ON sessions (scope) WHERE ended_at IS NULL;
CREATE INDEX sessions_by_package    ON sessions (package_id);
CREATE INDEX sessions_by_device_ts  ON sessions (device_id, started_at DESC);

-- ─── Session logs ──────────────────────────────────────────────────

CREATE TABLE session_logs (
    log_id      BIGINT            GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id  UUID              NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    event       session_log_event NOT NULL,
    ts          TIMESTAMPTZ       NOT NULL DEFAULT now(),
    actor       TEXT,
    details     JSONB
);

CREATE INDEX session_logs_by_session_ts ON session_logs (session_id, ts);
CREATE INDEX session_logs_by_ts         ON session_logs (ts DESC);

-- ─── Events ────────────────────────────────────────────────────────

CREATE TABLE events (
    event_id        BIGINT           GENERATED ALWAYS AS IDENTITY,
    session_id      UUID             NOT NULL REFERENCES sessions(session_id),
    device_id       INTEGER          NOT NULL REFERENCES devices(device_id),
    sensor_id       SMALLINT         NOT NULL,
    event_type      event_type       NOT NULL,
    triggered_at    TIMESTAMPTZ      NOT NULL,
    fired_at        TIMESTAMPTZ      NOT NULL DEFAULT now(),
    triggered_value DOUBLE PRECISION NOT NULL,
    metadata        JSONB            NOT NULL DEFAULT '{}'::jsonb,
    window_id       BIGINT,

    CONSTRAINT events_sensor_range
        CHECK (sensor_id BETWEEN 1 AND 12),
    -- Debounce can push triggered_at back from fired_at; allow up to 1 min.
    CONSTRAINT events_fire_vs_trigger
        CHECK (fired_at >= triggered_at - INTERVAL '1 minute'),
    -- Composite PK because the Timescale hypertable partitions on triggered_at.
    PRIMARY KEY (event_id, triggered_at)
);

CREATE INDEX events_by_session_ts ON events (session_id, triggered_at DESC);
CREATE INDEX events_by_device_ts  ON events (device_id, triggered_at DESC);
CREATE INDEX events_by_sensor_ts  ON events (device_id, sensor_id, triggered_at DESC);
CREATE INDEX events_by_type_ts    ON events (event_type, triggered_at DESC);
CREATE INDEX events_metadata_gin  ON events USING GIN (metadata jsonb_path_ops);

-- ─── Event windows (±9 s data blob) ────────────────────────────────

CREATE TABLE event_windows (
    window_id       BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id        BIGINT      NOT NULL,
    start_ts        TIMESTAMPTZ NOT NULL,
    end_ts          TIMESTAMPTZ NOT NULL,
    sample_rate_hz  REAL        NOT NULL DEFAULT 123.0,
    sample_count    INTEGER     NOT NULL,
    encoding        TEXT        NOT NULL DEFAULT 'zstd+delta-f32',
    data            BYTEA       NOT NULL,

    CONSTRAINT event_windows_time_order
        CHECK (end_ts > start_ts),
    CONSTRAINT event_windows_sample_count_positive
        CHECK (sample_count > 0)
);

CREATE INDEX event_windows_by_event ON event_windows (event_id);

-- Back-reference from events.window_id → event_windows.window_id.
-- DEFERRABLE INITIALLY DEFERRED lets a transaction insert the event and
-- the window in either order before committing.
ALTER TABLE events
    ADD CONSTRAINT events_window_fk
    FOREIGN KEY (window_id) REFERENCES event_windows(window_id)
    DEFERRABLE INITIALLY DEFERRED;

-- ─── Session samples (opt-in raw archive) ──────────────────────────

CREATE TABLE session_samples (
    session_id  UUID        NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    device_id   INTEGER     NOT NULL,
    sensor_id   SMALLINT    NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    value       REAL        NOT NULL,

    CONSTRAINT session_samples_sensor_range
        CHECK (sensor_id BETWEEN 1 AND 12)
);

-- Lookup index added after hypertable creation in 0003.

-- ─── Sensor offsets ────────────────────────────────────────────────

CREATE TABLE sensor_offsets (
    device_id    INTEGER          NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    sensor_id    SMALLINT         NOT NULL,
    offset_value DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    updated_at   TIMESTAMPTZ      NOT NULL DEFAULT now(),

    CONSTRAINT sensor_offsets_sensor_range
        CHECK (sensor_id BETWEEN 1 AND 12),
    PRIMARY KEY (device_id, sensor_id)
);

-- ─── Users / auth ──────────────────────────────────────────────────

CREATE TABLE users (
    user_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT        NOT NULL UNIQUE,
    display_name    TEXT,
    is_admin        BOOLEAN     NOT NULL DEFAULT FALSE,
    is_enabled      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ
);

CREATE TABLE user_otps (
    otp_id          BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         UUID        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    code_hash       TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    consumed_at     TIMESTAMPTZ,
    attempt_count   INTEGER     NOT NULL DEFAULT 0
);

CREATE INDEX user_otps_live ON user_otps (user_id, expires_at)
    WHERE consumed_at IS NULL;

-- ─── MQTT brokers ──────────────────────────────────────────────────

CREATE TABLE mqtt_brokers (
    broker_id    BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host         TEXT        NOT NULL,
    port         INTEGER     NOT NULL DEFAULT 1883,
    username     TEXT,
    password_enc TEXT,
    use_tls      BOOLEAN     NOT NULL DEFAULT FALSE,
    is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one active broker at any time.
CREATE UNIQUE INDEX mqtt_brokers_one_active
    ON mqtt_brokers ((1)) WHERE is_active = TRUE;

COMMIT;
