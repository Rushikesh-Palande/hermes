# HERMES v2 — Database Redesign

**Status:** Locked design, pending code scaffolding.
**Supersedes:** `/home/embed/hammer/src/database/mqtt_database.py` (18 tables, 96-column wide events row, silent config overwrites).
**Cross-references:** `docs/contracts/DATABASE_CONTRACT.md` (legacy), `docs/contracts/EVENT_DETECTION_CONTRACT.md` (behaviour), `docs/contracts/BUG_DECISION_LOG.md` (what we're fixing).

## 0. The big idea

Three problems with the legacy schema:

1. **Config is ephemeral.** Changing a threshold overwrites the old value. Events fired Monday cannot be replayed against Monday's config on Wednesday. There is no history.
2. **Config is fragmented.** Ten separate tables (`event_config_type_a`, `…_per_sensor`, b/c/d variants, `mode_switching_*`, `app_config`) encode the same shape: "a tunable parameter with scope". Operators cannot ask "what changed last week?".
3. **Events are a wide row.** The `events` table has 96 columns (one set per event type × per sensor column). Adding an event type means ALTER TABLE. Adding a sensor means more columns. The `data_window` BLOB is inlined, bloating every scan.

The new design fixes all three with four ideas:

| Concept | What it is |
|---|---|
| **Package** | An immutable, named, versioned snapshot of every tunable parameter. "Production Motor Test v3". |
| **Parameter** | One row per (package, scope, key). Queryable, diffable, audit-trailable. |
| **Session** | A monitoring run. Starts with a package, ends when stopped. One GLOBAL at a time, multiple LOCAL overrides for specific devices. |
| **Event** | A detection occurrence, tagged with its session, so config at trigger time is always reconstructible. |

Events FK → sessions FK → packages FK → parameters. Given any event, you replay exactly what config produced it. Forever.

## 1. Locked decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Parameters stored row-per-key** (not JSON blob) | Diffable, queryable ("which packages have `event_a.t1 > 5`?"), audit-trailable. ~300 rows/package is trivial. |
| 2 | **Unified `events` table** with `event_type` discriminator and JSONB `metadata` | Collapses 96 columns into ~10. Event-type-specific fields live in JSONB. `data_window` BLOB split into its own table so scans stay fast. |
| 3 | **Local sessions die with their global parent** | Simpler invariants; matches operator mental model ("stop monitoring" = everything stops). |
| 4 | **Legacy migration: "Legacy v1" synthetic session** | Old events reparent under one synthetic session + synthetic package. Preserves history without polluting the new model. |
| 5 | **PostgreSQL 16 + TimescaleDB 2.x** | Hypertables for high-volume tables (event_windows, optional session_samples), JSONB for metadata, real FKs, proper types, continuous aggregates for rolling stats. Pi 4 handles it (< 80 MB RAM baseline). |
| 6 | **Raw samples OPT-IN per session** (`record_raw_samples = true`) | 30k samples/sec × 12 sensors × 20 devices is unfeasible always-on. Opt-in enables debug/audit/ML without drowning the Pi. |
| 7 | **UUIDs for user-facing IDs, BIGINT identity for internal** | Packages and sessions get UUIDs (appear in URLs, exports). High-volume internal rows get BIGINT identity. |
| 8 | **Soft delete only** (`archived_at` timestamp) | Packages referenced by sessions cannot be hard-deleted; archiving preserves referential integrity forever. |

## 2. Entity overview (text ER)

```
                                                    ┌──────────────────┐
                                                    │    devices       │
                                                    │──────────────────│
                                                    │ device_id PK     │
                                                    │ name             │
                                                    │ protocol         │
                                                    │ topic            │
                                                    │ is_active        │
                                                    │ created_at       │
                                                    └────────▲─────────┘
                                                             │
     ┌──────────────────┐                                    │
     │    packages      │                                    │
     │──────────────────│                                    │
     │ package_id UUID  │◀─────┐                             │
     │ name             │      │ FK                          │
     │ description      │      │                             │
     │ is_default       │      │                             │
     │ is_locked (bool) │      │                             │
     │ created_at       │      │                             │
     │ created_by       │      │                             │
     │ archived_at NULL │      │                             │
     └────────▲─────────┘      │                             │
              │                │                             │
              │ FK             │                             │
              │                │                             │
     ┌────────┴─────────┐    ┌─┴────────────────┐            │
     │   parameters     │    │    sessions      │            │
     │──────────────────│    │──────────────────│            │
     │ parameter_id PK  │    │ session_id UUID  │─ FK ──────▶│
     │ package_id FK    │    │ scope ENUM       │            │
     │ scope ENUM       │    │ parent FK NULL   │─┐          │
     │ device_id NULL   │    │ device_id NULL   │ │ self-FK  │
     │ sensor_id NULL   │    │ package_id FK    │ │ (local → │
     │ key TEXT         │    │ started_at       │ │  global) │
     │ value JSONB      │    │ ended_at NULL    │ │          │
     │ created_at       │    │ started_by       │ │          │
     └──────────────────┘    │ notes            │ │          │
                             │ record_raw       │ │          │
                             └────────▲─────────┘ │          │
                                      │           │          │
                                      │ FK        │          │
                                      │           │          │
                             ┌────────┴─────────┐ │          │
                             │   session_logs   │ │          │
                             │──────────────────│ │          │
                             │ log_id PK        │ │          │
                             │ session_id FK    │◀┘          │
                             │ event ENUM       │            │
                             │ ts               │            │
                             │ actor            │            │
                             │ details JSONB    │            │
                             └──────────────────┘            │
                                                             │
                             ┌──────────────────┐            │
                             │     events       │            │
                             │──────────────────│            │
                             │ event_id BIGINT  │            │
                             │ session_id FK    │            │
                             │ device_id FK     │────────────┘
                             │ sensor_id 1..12  │
                             │ event_type ENUM  │
                             │ triggered_at     │
                             │ triggered_value  │
                             │ metadata JSONB   │
                             │ window_id FK NULL│
                             └────────▲─────────┘
                                      │ FK
                                      │
                             ┌────────┴─────────┐
                             │  event_windows   │
                             │──────────────────│
                             │ window_id BIGINT │
                             │ event_id FK      │
                             │ start_ts, end_ts │
                             │ sample_rate_hz   │
                             │ data BYTEA (zst) │
                             └──────────────────┘

            Optional hypertable (opt-in per session):
                             ┌──────────────────┐
                             │ session_samples  │  ← TimescaleDB hypertable
                             │──────────────────│    partitioned by ts
                             │ session_id FK    │
                             │ device_id, sid   │
                             │ ts               │
                             │ value            │
                             └──────────────────┘
```

## 3. DDL — PostgreSQL 16 + TimescaleDB 2.x

### 3.1 Extensions

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()
```

### 3.2 Enums

```sql
CREATE TYPE parameter_scope AS ENUM ('global', 'device', 'sensor');
CREATE TYPE session_scope   AS ENUM ('global', 'local');
CREATE TYPE session_log_event AS ENUM
    ('start', 'stop', 'pause', 'resume', 'reconfigure', 'error');
CREATE TYPE event_type AS ENUM ('A', 'B', 'C', 'D', 'BREAK');
CREATE TYPE device_protocol AS ENUM ('mqtt', 'modbus_tcp');
```

### 3.3 Devices (unchanged semantic, tightened types)

```sql
CREATE TABLE devices (
    device_id       INTEGER     PRIMARY KEY,             -- 1..N, operator-assigned
    name            TEXT        NOT NULL,
    protocol        device_protocol NOT NULL DEFAULT 'mqtt',
    topic           TEXT,                                 -- MQTT: 'stm32/adc/{id}'; Modbus: NULL
    modbus_config   JSONB,                                -- host/port/slave_id/registers; only when protocol='modbus_tcp'
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (device_id BETWEEN 1 AND 999),
    CHECK (protocol = 'mqtt' OR modbus_config IS NOT NULL)
);
```

### 3.4 Packages

```sql
CREATE TABLE packages (
    package_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT        NOT NULL,
    description     TEXT,
    is_default      BOOLEAN     NOT NULL DEFAULT FALSE,
    is_locked       BOOLEAN     NOT NULL DEFAULT FALSE,   -- once used by a closed session, flips TRUE
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT,                                  -- username; nullable for system-created
    archived_at     TIMESTAMPTZ,                           -- soft delete
    parent_package_id UUID REFERENCES packages(package_id), -- "cloned from"

    CHECK (archived_at IS NULL OR archived_at >= created_at)
);

-- Exactly one default, respecting archival:
CREATE UNIQUE INDEX packages_only_one_default
    ON packages ((1))
    WHERE is_default = TRUE AND archived_at IS NULL;

CREATE INDEX packages_name ON packages (name) WHERE archived_at IS NULL;
```

`is_locked` matters: once a **completed** session references a package, the package becomes immutable. Editing it creates a new package (via clone). This guarantees events keep pointing to the exact config that fired them.

### 3.5 Parameters

```sql
CREATE TABLE parameters (
    parameter_id    BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    package_id      UUID        NOT NULL REFERENCES packages(package_id) ON DELETE CASCADE,
    scope           parameter_scope NOT NULL,
    device_id       INTEGER     REFERENCES devices(device_id),
    sensor_id       SMALLINT,                              -- 1..12, null for global/device scope
    key             TEXT        NOT NULL,                  -- e.g. 'event_a.t1', 'event_b.tolerance_pct'
    value           JSONB       NOT NULL,

    CHECK (scope = 'global' OR device_id IS NOT NULL),
    CHECK (scope = 'sensor' OR sensor_id IS NULL),
    CHECK (scope = 'sensor' AND sensor_id BETWEEN 1 AND 12
           OR scope <> 'sensor')
);

-- A given key can appear at most once per (package, scope, device, sensor):
CREATE UNIQUE INDEX parameters_uniq
    ON parameters (
        package_id,
        scope,
        COALESCE(device_id, 0),
        COALESCE(sensor_id, 0),
        key
    );

-- Common lookup: "all parameters for this package at this level":
CREATE INDEX parameters_lookup
    ON parameters (package_id, scope, device_id, sensor_id);
```

**Resolution order** at runtime (highest to lowest): `sensor` → `device` → `global` → key's default. The application resolver walks this order; DB does not enforce it.

**Key taxonomy (registry):** stored in code (`src/config/parameter_registry.py`), not the DB. Each key has: name, type, min/max, default, scopes-where-valid, unit, description. DB treats `value` as opaque JSONB; the registry is the schema.

### 3.6 Sessions

```sql
CREATE TABLE sessions (
    session_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    scope               session_scope NOT NULL,
    parent_session_id   UUID        REFERENCES sessions(session_id),
    device_id           INTEGER     REFERENCES devices(device_id),
    package_id          UUID        NOT NULL REFERENCES packages(package_id),
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at            TIMESTAMPTZ,
    started_by          TEXT,
    ended_reason        TEXT,
    notes               TEXT,
    record_raw_samples  BOOLEAN     NOT NULL DEFAULT FALSE,

    CHECK (scope = 'global' AND parent_session_id IS NULL AND device_id IS NULL
        OR scope = 'local'  AND parent_session_id IS NOT NULL AND device_id IS NOT NULL),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

-- At most one active global session at any moment:
CREATE UNIQUE INDEX sessions_one_active_global
    ON sessions ((1))
    WHERE scope = 'global' AND ended_at IS NULL;

-- At most one active local session per device:
CREATE UNIQUE INDEX sessions_one_active_local_per_device
    ON sessions (device_id)
    WHERE scope = 'local' AND ended_at IS NULL;

CREATE INDEX sessions_active        ON sessions (scope) WHERE ended_at IS NULL;
CREATE INDEX sessions_by_package    ON sessions (package_id);
CREATE INDEX sessions_by_device_ts  ON sessions (device_id, started_at DESC);
```

**Cascade rule** (enforced in application + trigger below): ending a global session auto-ends its local children.

```sql
CREATE OR REPLACE FUNCTION end_local_children() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.scope = 'global' AND NEW.ended_at IS NOT NULL AND OLD.ended_at IS NULL THEN
        UPDATE sessions
           SET ended_at = NEW.ended_at,
               ended_reason = 'global session ended'
         WHERE parent_session_id = NEW.session_id
           AND ended_at IS NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER sessions_cascade_end
    AFTER UPDATE OF ended_at ON sessions
    FOR EACH ROW EXECUTE FUNCTION end_local_children();
```

**Package lock trigger** (when a session ends, lock its package):

```sql
CREATE OR REPLACE FUNCTION lock_package_on_session_end() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.ended_at IS NOT NULL AND OLD.ended_at IS NULL THEN
        UPDATE packages SET is_locked = TRUE
         WHERE package_id = NEW.package_id AND is_locked = FALSE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER sessions_lock_package
    AFTER UPDATE OF ended_at ON sessions
    FOR EACH ROW EXECUTE FUNCTION lock_package_on_session_end();
```

### 3.7 Session logs

```sql
CREATE TABLE session_logs (
    log_id          BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id      UUID        NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    event           session_log_event NOT NULL,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor           TEXT,
    details         JSONB
);

CREATE INDEX session_logs_by_session_ts ON session_logs (session_id, ts);
CREATE INDEX session_logs_by_ts         ON session_logs (ts DESC);
```

### 3.8 Events

```sql
CREATE TABLE events (
    event_id        BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id      UUID        NOT NULL REFERENCES sessions(session_id),
    device_id       INTEGER     NOT NULL REFERENCES devices(device_id),
    sensor_id       SMALLINT    NOT NULL CHECK (sensor_id BETWEEN 1 AND 12),
    event_type      event_type  NOT NULL,
    triggered_at    TIMESTAMPTZ NOT NULL,              -- ORIGINAL crossing (not fire time — see BUG_DECISION_LOG)
    fired_at        TIMESTAMPTZ NOT NULL DEFAULT now(), -- when the row was written
    triggered_value DOUBLE PRECISION NOT NULL,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    window_id       BIGINT,                             -- FK set after event_windows INSERT

    CHECK (fired_at >= triggered_at - INTERVAL '1 minute')  -- sanity; debounce drift allowed
);

CREATE INDEX events_by_session_ts ON events (session_id, triggered_at DESC);
CREATE INDEX events_by_device_ts  ON events (device_id, triggered_at DESC);
CREATE INDEX events_by_sensor_ts  ON events (device_id, sensor_id, triggered_at DESC);
CREATE INDEX events_by_type_ts    ON events (event_type, triggered_at DESC);
CREATE INDEX events_metadata_gin  ON events USING GIN (metadata jsonb_path_ops);

-- Timescale hypertable by triggered_at (daily chunks):
SELECT create_hypertable('events', 'triggered_at', chunk_time_interval => INTERVAL '1 day');
```

**`metadata` contents by event type** (schema enforced in application, not DB):

```jsonc
// Type A (variance)
{
  "t1_seconds": 5.0,
  "threshold_cv_pct": 20.0,
  "observed_cv_pct": 24.3,
  "window_mean": 103.4,
  "window_stddev": 25.1,
  "debounce_seconds": 2.0,
  "ttl_seconds": 3.0
}

// Type B (post-window deviation)
{
  "t2_seconds": 10.0,
  "tolerance_pct": 5.0,
  "ref_value": 100.0,
  "avg_t2": 102.1,
  "band_lower": 96.99, "band_upper": 107.21,
  "latest_value": 113.7,
  "debounce_seconds": 0.5,
  "ttl_seconds": 2.0
}

// Type C (avg_T3 range)
{
  "t3_seconds": 8.0,
  "lower_threshold": 80.0,
  "upper_threshold": 120.0,
  "avg_t3": 124.5,
  "debounce_seconds": 0.3,
  "ttl_seconds": 1.5
}

// Type D (two-stage smoothing; depends on C)
{
  "t4_seconds": 4.0, "t5_seconds": 15.0,
  "tolerance_pct": 8.0, "ref_value": 100.0,
  "avg_t4": 108.2, "avg_t5": 100.5,
  "band_lower": 92.46, "band_upper": 108.54,
  "parent_event_c_id": 449312,
  "debounce_seconds": 0.0,
  "ttl_seconds": 2.5
}

// BREAK (mode transition)
{
  "from_mode": "STARTUP",
  "to_mode": "BREAK",
  "avg_t5": 28.4,
  "mode_threshold_lower": 30.0,
  "duration_at_crossing_s": 2.1
}
```

### 3.9 Event windows (±9 s data blob)

```sql
CREATE TABLE event_windows (
    window_id       BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id        BIGINT      NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    start_ts        TIMESTAMPTZ NOT NULL,
    end_ts          TIMESTAMPTZ NOT NULL,
    sample_rate_hz  REAL        NOT NULL DEFAULT 123.0,
    sample_count    INTEGER     NOT NULL,
    encoding        TEXT        NOT NULL DEFAULT 'zstd+delta-f32',  -- see §3.9.1
    data            BYTEA       NOT NULL,

    CHECK (end_ts > start_ts),
    CHECK (sample_count > 0)
);

CREATE INDEX event_windows_by_event ON event_windows (event_id);
```

Then FK the pointer back on `events`:

```sql
ALTER TABLE events
    ADD CONSTRAINT events_window_fk
    FOREIGN KEY (window_id) REFERENCES event_windows(window_id)
    DEFERRABLE INITIALLY DEFERRED;
```

#### 3.9.1 BLOB encoding

Default: `zstd+delta-f32`:
1. Samples are float32 triples `(t_offset_ms: u16, value: f32)`.
2. `t_offset_ms` is delta-encoded from `start_ts` — fits in 16 bits for ±18 s @ 123 Hz.
3. Whole buffer compressed with zstd level 3.

Typical window: 18 s × 123 Hz = ~2 214 samples × 6 bytes = ~13 KB raw → ~2 KB zstd-compressed. 100× smaller than the legacy double-precision BLOB.

Decoding is strictly a client/library concern; DB stores bytes. The `encoding` column lets us change format later without a schema migration.

### 3.10 Session samples (optional raw archive)

```sql
CREATE TABLE session_samples (
    session_id  UUID        NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    device_id   INTEGER     NOT NULL,
    sensor_id   SMALLINT    NOT NULL CHECK (sensor_id BETWEEN 1 AND 12),
    ts          TIMESTAMPTZ NOT NULL,
    value       REAL        NOT NULL
);

SELECT create_hypertable('session_samples', 'ts',
    chunk_time_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

-- Compression: 10-20× for sensor data.
ALTER TABLE session_samples SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'session_id, device_id, sensor_id',
    timescaledb.compress_orderby   = 'ts'
);
SELECT add_compression_policy('session_samples', INTERVAL '1 hour');

CREATE INDEX session_samples_lookup
    ON session_samples (session_id, device_id, sensor_id, ts DESC);
```

Writes gated by `sessions.record_raw_samples = TRUE`. When a session ends with recording on, you can export the full raw archive for that session as a single Parquet via:

```sql
COPY (SELECT * FROM session_samples WHERE session_id = :sid)
     TO '/tmp/session_samples.parquet' WITH (FORMAT PARQUET);
```

(Requires `pg_parquet`; otherwise use an application-side export.)

### 3.11 Sensor offsets

```sql
CREATE TABLE sensor_offsets (
    device_id   INTEGER     NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    sensor_id   SMALLINT    NOT NULL CHECK (sensor_id BETWEEN 1 AND 12),
    offset_value DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, sensor_id)
);
```

Kept separate from `parameters` because offsets are physical calibration (a property of the wiring/ADC), not a monitoring parameter. They persist across packages and sessions. Formula unchanged: `adjusted = raw - offset`.

### 3.12 Users

```sql
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

CREATE INDEX user_otps_live ON user_otps (user_id, expires_at) WHERE consumed_at IS NULL;
```

OTP is hashed (argon2id), never plaintext.

### 3.13 MQTT config

```sql
CREATE TABLE mqtt_brokers (
    broker_id       BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host            TEXT        NOT NULL,
    port            INTEGER     NOT NULL DEFAULT 1883,
    username        TEXT,
    password_enc    TEXT,                                 -- encrypted at application layer
    use_tls         BOOLEAN     NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX mqtt_brokers_one_active
    ON mqtt_brokers ((1)) WHERE is_active = TRUE;
```

Exactly one active broker at a time.

## 4. Common queries

**Resolve the effective parameter value for a sensor at runtime:**

```sql
WITH hierarchy AS (
    SELECT value, CASE scope
        WHEN 'sensor' THEN 3
        WHEN 'device' THEN 2
        WHEN 'global' THEN 1
    END AS precedence
    FROM parameters
    WHERE package_id = :pkg
      AND key = :key
      AND (scope = 'global'
        OR (scope = 'device' AND device_id = :dev)
        OR (scope = 'sensor' AND device_id = :dev AND sensor_id = :sen))
)
SELECT value FROM hierarchy
ORDER BY precedence DESC LIMIT 1;
```

**List all events for a session, with config reconstructed:**

```sql
SELECT e.triggered_at, e.event_type, e.sensor_id, e.triggered_value, e.metadata,
       p.name AS package_name
  FROM events e
  JOIN sessions s ON s.session_id = e.session_id
  JOIN packages p ON p.package_id = s.package_id
 WHERE e.session_id = :sid
 ORDER BY e.triggered_at DESC;
```

**Diff two packages:**

```sql
SELECT COALESCE(a.key, b.key) AS key,
       a.scope, a.device_id, a.sensor_id,
       a.value AS value_a, b.value AS value_b
  FROM parameters a
  FULL OUTER JOIN parameters b
    ON a.key = b.key AND a.scope = b.scope
   AND COALESCE(a.device_id, 0) = COALESCE(b.device_id, 0)
   AND COALESCE(a.sensor_id, 0) = COALESCE(b.sensor_id, 0)
 WHERE a.package_id = :pkg_a
   AND b.package_id = :pkg_b
   AND a.value IS DISTINCT FROM b.value;
```

**Current active global session (and its locals):**

```sql
SELECT g.session_id, g.started_at, g.package_id,
       array_agg(l.device_id) FILTER (WHERE l.session_id IS NOT NULL) AS overridden_devices
  FROM sessions g
  LEFT JOIN sessions l ON l.parent_session_id = g.session_id AND l.ended_at IS NULL
 WHERE g.scope = 'global' AND g.ended_at IS NULL
 GROUP BY g.session_id;
```

## 5. Migration from legacy

### 5.1 Strategy

1. Stand up new Postgres alongside legacy SQLite.
2. Run a one-shot migration script that:
   - Creates synthetic package **"Legacy v1"** from the current values in `event_config_type_*` + `mode_switching_*` + `app_config`.
   - Locks that package (`is_locked = TRUE`).
   - Creates synthetic session **"Legacy v1 session"** (scope=global, started_at = earliest legacy event, ended_at = latest legacy event).
   - For each row in legacy `events` table, emits ONE row per flagged event type (fixes legacy bug #5 — wide-row stores multiple types per row) and writes to new `events` + `event_windows`.
3. Switch application writes to Postgres. Legacy SQLite becomes read-only archive.

### 5.2 Why synthetic

Legacy events have no anchor to their config. We could try to reverse-engineer config-at-trigger-time from git history, but it's brittle and time-consuming. Fabricating a single "Legacy v1" package that holds whatever the config WAS at migration time preserves the events without lying about their provenance — the session name and package name both say "Legacy v1", so anyone querying knows these events predate the new model.

### 5.3 Migration script skeleton

```python
# tools/migrate_legacy.py
def migrate():
    legacy = sqlite3.connect('/mnt/ssd/mqtt_database/mqtt_database.db')
    new = psycopg.connect(...)

    pkg_id = create_legacy_package(legacy, new)           # 1 row in packages, N in parameters
    sess_id = create_legacy_session(legacy, new, pkg_id)  # 1 row in sessions

    for legacy_row in legacy.execute("SELECT * FROM events"):
        for event_type in extract_flagged_types(legacy_row):   # BUG_DECISION_LOG #5
            event_id = insert_event(new, sess_id, legacy_row, event_type)
            if legacy_row['data_window']:
                insert_window(new, event_id, legacy_row['data_window'])

    lock_session(new, sess_id)
    lock_package(new, pkg_id)
```

Run it once. Verify row counts match (each legacy row might produce 1..N new rows depending on flagged types). Freeze the legacy DB at that point.

## 6. Retention & archival

| Table | Retention | Notes |
|-------|-----------|-------|
| `events` | Forever (hypertable; compress chunks > 30 d) | Core audit trail. |
| `event_windows` | 1 year, then drop chunks (configurable) | ±9s raw is bulky. Events keep summary in `metadata`. |
| `session_samples` | Tied to session lifecycle; drop when session archived | Opt-in; can disable entirely by leaving `record_raw_samples = FALSE`. |
| `session_logs` | Forever | Cheap. |
| `sessions` | Forever | Reference by events. |
| `packages` | Forever (soft delete) | Locked packages cannot be mutated. |
| `parameters` | Same lifetime as their package | CASCADE delete when package is hard-deleted (should rarely happen). |

Retention policies scripted with TimescaleDB:

```sql
SELECT add_retention_policy('event_windows', INTERVAL '1 year');
SELECT add_compression_policy('events', INTERVAL '30 days');
SELECT add_compression_policy('event_windows', INTERVAL '7 days');
```

## 7. Invariants the schema enforces (summary)

1. **At most one active global session** (partial unique index).
2. **At most one active local session per device** (partial unique index).
3. **Local sessions always have a global parent with a matching device_id** (CHECK).
4. **Ending a global cascades to ending its locals** (trigger).
5. **A package used by any ended session is locked** (trigger); edits must clone.
6. **Default package is unique when not archived** (partial unique index).
7. **Parameter keys are unique within (package, scope, device, sensor)** (unique index).
8. **Offsets are 1..12 per device, exactly one row per pair** (primary key).
9. **Event types come from a fixed enum** — adding a new type is a migration, not a silent insert.
10. **`triggered_at` is original crossing time, not fire time** — application contract, DB holds the value.

## 8. What the rewrite gets from this

- **Audit trail**: every event replayable against its exact config. Forever.
- **Package presets**: operators save "Startup", "Production", "Calibration" once, switch with one click.
- **Local overrides**: Device 3 can be in calibration mode while the rest run production.
- **Event archaeology**: "Why did sensor 7 fire a B event on March 14 at 02:17? Show me the exact thresholds." — one query.
- **Queryable history**: "Which packages ever had `event_b.tolerance_pct > 10`?" — one query.
- **Config diffs**: "What changed between Package v3 and v4?" — one query.
- **Clean event table**: 10 narrow columns + GIN-indexed JSONB, not 96-column wide rows. Adding event types = adding an enum value, not ALTER TABLE.
- **Optional raw archive**: debug a tricky sensor by opting into `record_raw_samples` on a single local session. No impact when off.
- **Compression**: Timescale gives 10-20× on `session_samples`, 5-10× on `events`.
- **No silent config overwrites**: editing a locked package forces a new package → new session → clean lineage.

## 9. What still needs a decision

These do not block DDL, but block later code:

- [ ] **Parameter registry schema.** A YAML or Python dict listing every key, its type, min/max, default, units, and which scope levels are legal. Needed before the parameters resolver exists.
- [ ] **Retention defaults.** The numbers above are my suggestions; confirm `event_windows = 1 year` and `events compression = 30 d` are right for your storage budget.
- [ ] **Backup cadence.** Nightly `pg_basebackup` to `/mnt/ssd/backup/`? Offsite? On a Pi this is a real operational decision.
- [ ] **Compression vs latency.** Timescale compression takes a few seconds to chunk; at > 30 d it's fine. If you want real-time dashboards over compressed chunks, there's a slight read penalty — negligible for this workload but worth noting.

## 10. Files to create from this design

In the rewrite repo (`C:\Software\Hermes`):

```
db/
├── migrations/
│   ├── 0001_init_extensions.sql
│   ├── 0002_core_tables.sql          ← all CREATE TABLE from §3
│   ├── 0003_hypertables.sql          ← create_hypertable calls
│   ├── 0004_triggers.sql             ← cascade + lock triggers
│   ├── 0005_retention_policies.sql   ← add_retention_policy / add_compression_policy
│   └── 0006_legacy_migration.sql     ← synthetic package/session DDL (data in Python)
├── seed/
│   ├── default_package.py            ← reasonable starter parameters
│   └── initial_admin.py
└── README.md                          ← links back here

src/hermes/db/
├── models.py                          ← SQLAlchemy ORM
├── parameters/
│   ├── registry.py                    ← the parameter registry
│   └── resolver.py                    ← sensor→device→global resolution
├── sessions.py                        ← session lifecycle
├── packages.py                        ← clone, archive, lock semantics
└── events.py                          ← insert / query

tools/
└── migrate_legacy.py                  ← one-shot legacy SQLite → Postgres
```

That's it. DDL is locked. Next step is scaffolding and the migration runner. Say the word and I start writing.
