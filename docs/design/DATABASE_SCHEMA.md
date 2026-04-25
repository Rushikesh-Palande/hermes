# DATABASE_SCHEMA.md — every table, column, constraint, index

> **Audience:** anyone writing a query, debugging a row that "shouldn't
> exist", or planning a migration. Catalogs every database object the
> rewrite owns. Source of truth is the SQL in `migrations/`; this doc
> mirrors it for browseability.
>
> **Companion docs:**
> - [`DATABASE_REDESIGN.md`](./DATABASE_REDESIGN.md) — the rationale for *why* the schema looks like this
> - [`../guides/EVENTS.md`](../guides/EVENTS.md) — what events / event_windows store
> - [`../guides/BACKEND.md`](../guides/BACKEND.md) — SQLAlchemy model classes
> - [`../guides/WORKFLOW.md`](../guides/WORKFLOW.md) — when each table is written

---

## Table of contents

1. [Schema overview (one diagram)](#1-schema-overview-one-diagram)
2. [Migrations layout](#2-migrations-layout)
3. [Enum types](#3-enum-types)
4. [Tables](#4-tables)
   1. [`devices`](#41-devices)
   2. [`packages`](#42-packages)
   3. [`parameters`](#43-parameters)
   4. [`sessions`](#44-sessions)
   5. [`session_logs`](#45-session_logs)
   6. [`events`](#46-events--hypertable)
   7. [`event_windows`](#47-event_windows--hypertable)
   8. [`session_samples`](#48-session_samples--hypertable)
   9. [`sensor_offsets`](#49-sensor_offsets)
   10. [`users`](#410-users)
   11. [`user_otps`](#411-user_otps)
   12. [`mqtt_brokers`](#412-mqtt_brokers)
5. [Triggers](#5-triggers)
6. [Hypertables, compression, retention](#6-hypertables-compression-retention)
7. [LISTEN/NOTIFY channels](#7-listennotify-channels)
8. [Common queries](#8-common-queries)

---

## 1. Schema overview (one diagram)

```
                            ┌──────────────┐
                            │  packages    │
                            │  (immutable  │
                            │   once used) │
                            └──────┬───────┘
                                   │ package_id
                  ┌────────────────┼────────────────┐
                  │                │                │
                  ▼                ▼                ▼
         ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
         │  parameters  │ │   sessions   │ │  (other      │
         │  scope:      │ │   scope:     │ │   future     │
         │  GLOBAL /    │ │   GLOBAL /   │ │   children)  │
         │  DEVICE /    │ │   LOCAL      │ │              │
         │  SENSOR      │ │              │ │              │
         └──────┬───────┘ └──────┬───────┘ └──────────────┘
                │                │
        device_id (opt)     parent_session_id (LOCAL → GLOBAL)
        sensor_id  (opt)    device_id          (LOCAL only)
                │                │
                ▼                ▼
         ┌──────────────┐ ┌──────────────┐
         │  devices     │ │ session_logs │
         │  device_id   │ │ START/STOP/. │
         │  protocol:   │ │ append-only  │
         │  MQTT/MODBUS │ │              │
         │  (modbus_cfg)│ └──────────────┘
         └──────┬───────┘
                │
                │ device_id
                ├────────────────────────────┐
                ▼                            ▼
         ┌──────────────┐             ┌──────────────┐
         │ sensor_      │             │  events      │
         │ offsets      │             │  (HYPERTABLE │
         │ (calibration)│             │   on         │
         └──────────────┘             │  triggered_at)│
                                      └──────┬───────┘
                                             │ window_id (1:1)
                                             ▼
                                      ┌──────────────┐
                                      │ event_       │
                                      │ windows      │
                                      │ (HYPERTABLE  │
                                      │  +/-9 s BLOB)│
                                      └──────────────┘

Other tables, independent of the package/session graph:

  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
  │   users      │  │  user_otps   │  │ mqtt_brokers │  │ session_     │
  │              │  │              │  │ (one active  │  │ samples      │
  │              │  │              │  │  at a time)  │  │ (HYPERTABLE) │
  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
                                                          │
                                                          │ session_id
                                                          ▼
                                                   sessions(session_id)
```

## 2. Migrations layout

```
migrations/
├── 0001_init_extensions.sql      CREATE EXTENSION timescaledb, pgcrypto
├── 0002_core_tables.sql          all tables + enums + most indexes (281 LOC)
├── 0003_hypertables.sql          create_hypertable + compression for events,
│                                 event_windows, session_samples
├── 0004_triggers.sql             end_local_children, lock_package_on_session_end,
│                                 touch_updated_at
└── 0005_retention_policies.sql   compression policies + (deferred) retention
```

Every migration is **append-only and idempotent** — `CREATE TYPE` and
`CREATE TABLE` use `IF NOT EXISTS` where possible, and TimescaleDB's
helpers (`create_hypertable`, `add_compression_policy`) take
`if_not_exists => TRUE`. Re-running a successful migration is a no-op.

The runner is `scripts/db-migrate.sh` (apply in lexicographic order).
There's no Alembic — the `migrations/versions/` directory was removed
in alpha.25 because it was a leftover Alembic placeholder.

To **roll forward**: write `0NNN_<slug>.sql` with whatever DDL.
To **roll back**: write a NEW migration that does the inverse.
We never edit a past migration.

---

## 3. Enum types

Created in `0002_core_tables.sql`, after `BEGIN;`.

| Enum | Values | Used by |
|------|--------|---------|
| `parameter_scope` | `'global'`, `'device'`, `'sensor'` | `parameters.scope` |
| `session_scope` | `'global'`, `'local'` | `sessions.scope` |
| `session_log_event` | `'start'`, `'stop'`, `'pause'`, `'resume'`, `'reconfigure'`, `'error'` | `session_logs.event` |
| `event_type` | `'A'`, `'B'`, `'C'`, `'D'`, `'BREAK'` | `events.event_type` |
| `device_protocol` | `'mqtt'`, `'modbus_tcp'` | `devices.protocol` |

> ⚠️ Postgres enum values are lowercase / mixed case. Python `StrEnum`
> sends member NAME by default (uppercase). The `_pg_enum()` helper
> in `services/hermes/db/models.py` uses `values_callable=lambda x:
> [e.value for e in x]` to bridge. Forgetting this gives errors like
> `invalid input value for enum: "GLOBAL"`.

---

## 4. Tables

### 4.1 `devices`

A physical data source. Operator-assigned `device_id` (NOT auto-incremented).

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `device_id` | INTEGER | — | **PK**, 1..999 |
| `name` | TEXT | — | NOT NULL, operator-friendly label |
| `protocol` | `device_protocol` | `'mqtt'` | NOT NULL |
| `topic` | TEXT | NULL | Per-device MQTT topic override; if NULL, uses `Settings.mqtt_topic_adc` |
| `modbus_config` | JSONB | NULL | Required when `protocol='modbus_tcp'`. Validated by `ModbusConfig` pydantic model |
| `is_active` | BOOLEAN | `TRUE` | NOT NULL. Soft-disable; ingest skips inactive devices |
| `created_at` | TIMESTAMPTZ | `now()` | NOT NULL |
| `updated_at` | TIMESTAMPTZ | `now()` | NOT NULL. Auto-touched by `touch_updated_at` trigger |

**Constraints:**

- `devices_id_range`: `device_id BETWEEN 1 AND 999`
- `devices_modbus_has_config`: `protocol = 'mqtt' OR modbus_config IS NOT NULL`

**Why operator-assigned `device_id`:** the legacy contract documents
that operators reference devices by number on the dashboard. Auto-
incrementing would break that mental model on every test/dev cycle.
Range 1..999 is a defensive cap.

### 4.2 `packages`

A configuration preset. Locked once a session that used it has closed.
Editing a locked package is done by cloning it (`POST /api/packages/{id}/clone`).

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `package_id` | UUID | `gen_random_uuid()` | **PK** |
| `name` | TEXT | — | NOT NULL |
| `description` | TEXT | NULL | |
| `is_default` | BOOLEAN | `FALSE` | NOT NULL. Auto-bootstrapped by `ensure_default_session()` |
| `is_locked` | BOOLEAN | `FALSE` | NOT NULL. Set by `lock_package_on_session_end` trigger |
| `created_at` | TIMESTAMPTZ | `now()` | NOT NULL |
| `created_by` | TEXT | NULL | Free-form actor ("api", "ingest-bootstrap", future user email) |
| `archived_at` | TIMESTAMPTZ | NULL | Operator soft-archive (UI not wired yet) |
| `parent_package_id` | UUID | NULL | FK to `packages(package_id)`. Set on clone |

**Constraints:**

- `packages_archival_order`: `archived_at IS NULL OR archived_at >= created_at`

**Indexes:**

- `packages_only_one_default` (UNIQUE, partial): on `((1))` where
  `is_default = TRUE AND archived_at IS NULL`. Enforces "exactly one
  active default at any time".
- `packages_name` (partial): on `name` where `archived_at IS NULL`.

### 4.3 `parameters`

The configuration store. Each row is one key/value pair scoped to a
`(package_id, scope, device_id?, sensor_id?)` tuple. Detector
thresholds live here; `DbConfigProvider` resolves SENSOR > DEVICE >
GLOBAL on each lookup.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `parameter_id` | BIGINT IDENTITY ALWAYS | — | **PK** |
| `package_id` | UUID | — | NOT NULL, FK `packages(package_id)` ON DELETE CASCADE |
| `scope` | `parameter_scope` | — | NOT NULL |
| `device_id` | INTEGER | NULL | FK `devices(device_id)`. Required when `scope IN ('device', 'sensor')` |
| `sensor_id` | SMALLINT | NULL | Required when `scope='sensor'`, NULL otherwise |
| `key` | TEXT | — | NOT NULL. Stable strings; current set: `type_a.config`, `type_b.config`, `type_c.config`, `type_d.config`, `mode_switching.config` |
| `value` | JSONB | — | NOT NULL. Shape determined by `key` (one Pydantic model per key) |

**Constraints:**

- `parameters_device_required_when_scoped`: `scope = 'global' OR device_id IS NOT NULL`
- `parameters_sensor_only_at_sensor_scope`: `scope = 'sensor' OR sensor_id IS NULL`
- `parameters_sensor_range`: `(scope = 'sensor' AND sensor_id BETWEEN 1 AND 12) OR scope <> 'sensor'`

**Indexes:**

- `parameters_uniq` (UNIQUE): on `(package_id, key, scope, COALESCE(device_id, 0), COALESCE(sensor_id, 0))`. The COALESCE trick lets a unique index span nullable columns.
- `parameters_lookup`: on `(package_id, scope, device_id, sensor_id)` for the SENSOR → DEVICE → GLOBAL walk.

### 4.4 `sessions`

A monitoring run. Binds events to the configuration package that was
active when they fired.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `session_id` | UUID | `gen_random_uuid()` | **PK** |
| `scope` | `session_scope` | — | NOT NULL |
| `parent_session_id` | UUID | NULL | FK `sessions(session_id)`. NOT NULL for LOCAL, NULL for GLOBAL |
| `device_id` | INTEGER | NULL | FK `devices(device_id)`. NOT NULL for LOCAL, NULL for GLOBAL |
| `package_id` | UUID | — | NOT NULL, FK `packages(package_id)` |
| `started_at` | TIMESTAMPTZ | `now()` | NOT NULL |
| `ended_at` | TIMESTAMPTZ | NULL | Set by stop. NULL while active |
| `started_by` | TEXT | NULL | "api", "ingest-bootstrap", future user email |
| `ended_reason` | TEXT | NULL | Operator-supplied reason on stop |
| `notes` | TEXT | NULL | Free-form |
| `record_raw_samples` | BOOLEAN | `FALSE` | NOT NULL. Toggles `SessionSampleWriter` for this session's lifetime |

**Constraints:**

- `sessions_scope_shape`:
  ```
  (scope = 'global' AND parent_session_id IS NULL AND device_id IS NULL)
   OR
  (scope = 'local'  AND parent_session_id IS NOT NULL AND device_id IS NOT NULL)
  ```
- `sessions_timing_order`: `ended_at IS NULL OR ended_at >= started_at`

**Indexes:**

- `sessions_one_active_global` (UNIQUE, partial): on `((1))` where
  `scope = 'global' AND ended_at IS NULL`. Enforces single active GLOBAL.
- `sessions_one_active_local_per_device` (UNIQUE, partial): on
  `device_id` where `scope = 'local' AND ended_at IS NULL`. Single active LOCAL per device.
- `sessions_active`: on `scope` where `ended_at IS NULL`.
- `sessions_by_package`: on `package_id`.
- `sessions_by_device_ts`: on `(device_id, started_at DESC)`.

### 4.5 `session_logs`

Append-only audit trail of session lifecycle events. Written by
`/api/sessions` route handlers + the bootstrap path.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `log_id` | BIGINT IDENTITY ALWAYS | — | **PK** |
| `session_id` | UUID | — | NOT NULL, FK `sessions(session_id)` ON DELETE CASCADE |
| `event` | `session_log_event` | — | NOT NULL |
| `ts` | TIMESTAMPTZ | `now()` | NOT NULL |
| `actor` | TEXT | NULL | "api", "ingest-bootstrap", future user email |
| `details` | JSONB | NULL | Free-form context (reason, package_id, device_id) |

**Indexes:**

- `session_logs_by_session_ts`: on `(session_id, ts)` — chronological per session
- `session_logs_by_ts`: on `(ts DESC)` — global recent audit feed

### 4.6 `events` — HYPERTABLE

Every detected event. TimescaleDB hypertable partitioned on
`triggered_at`, `chunk_time_interval = 1 day` (set in 0003).

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `event_id` | BIGINT IDENTITY ALWAYS | — | Part of composite PK |
| `triggered_at` | TIMESTAMPTZ | — | NOT NULL. Detector's `triggered_at` (FIRST crossing for debounced events; `_debounce_start` for Type A/B/C/D; sensor_below_start_time for BREAK). Part of composite PK |
| `fired_at` | TIMESTAMPTZ | `now()` | NOT NULL. Actual DB-write time |
| `session_id` | UUID | — | NOT NULL, FK `sessions(session_id)` |
| `event_type` | `event_type` | — | NOT NULL |
| `device_id` | INTEGER | — | NOT NULL |
| `sensor_id` | SMALLINT | — | NOT NULL, 1..12 |
| `triggered_value` | REAL | NULL | Projected from `metadata` for fast list-view queries |
| `metadata` | JSONB | — | NOT NULL. Detector-specific, see [`EVENTS.md`](../guides/EVENTS.md) §9 |
| `window_id` | BIGINT | NULL | FK `event_windows(window_id)` |

**Constraints:**

- `events_sensor_range`: `sensor_id BETWEEN 1 AND 12`
- `events_fire_vs_trigger`: `fired_at >= triggered_at`
- **Composite PK**: `(event_id, triggered_at)` — required for hypertable partitioning. Test seeds must pin `fired_at = triggered_at` to satisfy the constraint with future-dated events.

**Indexes:** (created in 0002, propagate to each chunk automatically)

- `events_by_session_ts`: on `(session_id, triggered_at DESC)`
- `events_by_device_ts`: on `(device_id, triggered_at DESC)`
- `events_by_sensor_ts`: on `(device_id, sensor_id, triggered_at DESC)`
- `events_by_type_ts`: on `(event_type, triggered_at DESC)`
- `events_metadata_gin`: GIN(`metadata` jsonb_path_ops) for `metadata @> '{key:val}'` queries

**Compression** (set in 0003 + policy in 0005):

```sql
ALTER TABLE events SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'session_id, device_id, event_type',
    timescaledb.compress_orderby   = 'triggered_at'
);

SELECT add_compression_policy('events', INTERVAL '30 days');
```

Chunks older than 30 days compress automatically. ~10× space saving
typical.

### 4.7 `event_windows` — HYPERTABLE

The ±9 s sample window for each event. 1:1 with `events.event_id`.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `window_id` | BIGINT IDENTITY ALWAYS | — | Part of composite PK |
| `event_id` | BIGINT | — | NOT NULL. UNIQUE per event. Part of composite PK with window_start |
| `window_start` | TIMESTAMPTZ | — | NOT NULL. `triggered_at - 9 s`. Hypertable partitions on this |
| `window_end` | TIMESTAMPTZ | — | NOT NULL. `triggered_at + 9 s` |
| `sample_rate_hz` | REAL | `123.0` | Informational; legacy default carried forward |
| `sample_count` | INTEGER | — | NOT NULL |
| `encoding` | TEXT | `'zstd+delta-f32'` | NOT NULL. Today's writer uses `'json-utf8'`; the legacy default is reserved |
| `data` | BYTEA | — | NOT NULL. Encoded sample list |

**Indexes:**

- `event_windows_by_event`: on `event_id`

**Compression**: same pattern as events; 0003 sets the segmentby/orderby; 0005 adds the policy (compress chunks older than 1 hour because raw windows are large).

### 4.8 `session_samples` — HYPERTABLE

The opt-in raw-sample archive (gap 6, alpha.20). Written when an
operator's session has `record_raw_samples=true`.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `session_id` | UUID | — | NOT NULL, FK `sessions(session_id)` ON DELETE CASCADE |
| `device_id` | INTEGER | — | NOT NULL |
| `sensor_id` | SMALLINT | — | NOT NULL, 1..12 |
| `ts` | TIMESTAMPTZ | — | NOT NULL. Hypertable partitions on this, chunk = 1 hour |
| `value` | REAL | — | NOT NULL. Engineering-units value (post-offset) |

**Constraints:**

- `session_samples_sensor_range`: `sensor_id BETWEEN 1 AND 12`

No primary key by design — TimescaleDB hypertables typically don't
have one because the chunk constraint plus the lookup index covers
query patterns. The SQLAlchemy model declares a composite PK
(`session_id, device_id, sensor_id, ts`) purely for ORM bookkeeping.

**Indexes:**

- `session_samples_lookup`: on `(session_id, device_id, sensor_id, ts DESC)`

**Compression**: aggressive — segmentby (session_id, device_id, sensor_id), orderby ts. Compresses after 1 hour (per 0005). At 30 k rows/sec a 1 h chunk is ~100 M rows; raw would be untenable.

**Retention:** application-managed. When a session is closed and the
operator wants to keep its samples, they stay. When they want to
purge, `DELETE WHERE session_id = X` does it (the FK is `ON DELETE
CASCADE` — deleting the parent session deletes the samples).

### 4.9 `sensor_offsets`

Per-sensor calibration. `engineering_value = raw_value − offset`.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `device_id` | INTEGER | — | Part of PK. FK `devices(device_id)` ON DELETE CASCADE |
| `sensor_id` | SMALLINT | — | Part of PK |
| `offset_value` | REAL | `0.0` | NOT NULL |
| `updated_at` | TIMESTAMPTZ | `now()` | NOT NULL |

**PK:** `(device_id, sensor_id)`.

Loaded into `OffsetCache` at ingest startup; updated via
`PUT /api/devices/{id}/offsets/{sensor_id}` (single) or
`PUT /api/devices/{id}/offsets` (bulk replace).

### 4.10 `users`

Operator accounts. OTP-only authentication; no password column.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `user_id` | BIGINT IDENTITY ALWAYS | — | **PK** |
| `email` | TEXT | — | NOT NULL UNIQUE. Lowercase-normalised at insert |
| `display_name` | TEXT | NULL | |
| `is_active` | BOOLEAN | `TRUE` | NOT NULL. Disabled users can't log in |
| `created_at` | TIMESTAMPTZ | `now()` | NOT NULL |
| `last_login_at` | TIMESTAMPTZ | NULL | Updated on successful OTP verify |

### 4.11 `user_otps`

OTP attempts. argon2-hashed code, with rate limits enforced from
Settings.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `otp_id` | BIGINT IDENTITY ALWAYS | — | **PK** |
| `user_id` | BIGINT | — | NOT NULL, FK `users(user_id)` ON DELETE CASCADE |
| `code_hash` | TEXT | — | NOT NULL. argon2 hash; original code never stored |
| `created_at` | TIMESTAMPTZ | `now()` | NOT NULL |
| `expires_at` | TIMESTAMPTZ | — | NOT NULL. `created_at + Settings.otp_expiry_seconds` |
| `consumed_at` | TIMESTAMPTZ | NULL | Set on successful verify |
| `attempt_count` | INTEGER | `0` | NOT NULL. Bumped on each failed verify |

**Indexes:**

- `user_otps_live` (partial): on `(user_id, expires_at)` where `consumed_at IS NULL`. Used for the rate-limit lookup.

### 4.12 `mqtt_brokers`

Operator-managed MQTT broker registry (gap 4, alpha.18). At most one
row may be active at a time.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `broker_id` | BIGINT IDENTITY ALWAYS | — | **PK** |
| `host` | TEXT | — | NOT NULL |
| `port` | INTEGER | `1883` | NOT NULL |
| `username` | TEXT | NULL | |
| `password_enc` | TEXT | NULL | Fernet token. Decrypts via `auth.secret_box.decrypt(...)` |
| `use_tls` | BOOLEAN | `FALSE` | NOT NULL |
| `is_active` | BOOLEAN | `TRUE` | NOT NULL |
| `created_at` | TIMESTAMPTZ | `now()` | NOT NULL |

**Indexes:**

- `mqtt_brokers_one_active` (UNIQUE, partial): on `((1))` where `is_active = TRUE`. Enforces single active broker.

> **Note:** the active-row swap is NOT yet wired to the running ingest
> client. Operators must `systemctl restart hermes-ingest` after
> switching the active broker. Live switchover is a tracked follow-up.

---

## 5. Triggers

`migrations/0004_triggers.sql`. Two domain triggers + one generic.

### 5.1 `sessions_lock_package` — lock package on session close

```sql
CREATE TRIGGER sessions_lock_package
    AFTER UPDATE OF ended_at ON sessions
    FOR EACH ROW EXECUTE FUNCTION lock_package_on_session_end();
```

When a session transitions from `ended_at IS NULL` to `ended_at IS NOT NULL`,
the trigger sets `packages.is_locked = TRUE` for the session's package.
Re-running on already-locked packages is a no-op.

Why: every event in a closed session was detected against a specific
`parameters` configuration; if we let the operator edit those rows
afterwards, the package's "what config produced this event?" semantics
break. Edits to a locked package require cloning first
(`POST /api/packages/{id}/clone`).

### 5.2 `sessions_end_local_children` — cascade GLOBAL stop

```sql
CREATE TRIGGER sessions_end_local_children
    AFTER UPDATE OF ended_at ON sessions
    FOR EACH ROW EXECUTE FUNCTION end_local_children();
```

When a GLOBAL session closes, every active LOCAL session that has it
as `parent_session_id` is closed too. Saves the operator from
hand-stopping every per-device override before stopping the parent.

### 5.3 `touch_updated_at` — generic updated_at maintainer

Applied to `devices` (the only table with an `updated_at` column).
Keeps `updated_at` in sync with the row's last modification without
the application layer having to remember.

---

## 6. Hypertables, compression, retention

`migrations/0003_hypertables.sql` + `migrations/0005_retention_policies.sql`.

| Table | Partition column | Chunk interval | Compression segmentby | Compression policy (age) |
|-------|------------------|----------------|----------------------|---------------------------|
| `events` | `triggered_at` | 1 day | `session_id, device_id, event_type` | 30 days |
| `event_windows` | `window_start` | 1 day | `event_id` | 1 hour |
| `session_samples` | `ts` | 1 hour | `session_id, device_id, sensor_id` | 1 hour |

> **Why `event_windows` segments by `event_id`:** windows are 1:1 with
> events; segmenting by event_id means each compressed batch contains
> one window's worth of samples — efficient compression because
> intra-window samples are highly correlated (delta encoding).

> **Why `session_samples` chunks every hour:** at 30 k rows/sec, a 1 h
> chunk is ~100 M rows. Smaller chunks would still work but the
> compression overhead would be higher; 1 h is the sweet spot.

**Retention is application-managed for `event_windows`.** Per the
contract, raw windows are part of the operator's audit trail; they
shouldn't auto-delete. A future periodic job will age them out
according to operator policy (default: never).

---

## 7. LISTEN/NOTIFY channels

| Channel | Payload | Sender | Listeners |
|---------|---------|--------|-----------|
| `hermes_config_changed` | `package_id` (UUID as text) | `/api/config/...` route handlers, after `_commit_and_reload` | Every `DbConfigProvider` instance with `start_listener()` running. Multi-shard ingest processes use this to invalidate config caches |

Shard-aware reload sequence:

```
operator → PUT /api/config/...
         → API process commits parameter rows
         → API process reloads its own provider in-process
         → API process emits NOTIFY hermes_config_changed '<package_id>'
                                         │
            ┌────────────────────────────┼────────────────────────┐
            ▼                            ▼                        ▼
         shard 0                      shard 1                  shard 2
         LISTEN drains the notification:
           ├── DbConfigProvider.reload()
           └── DetectionEngine.reset_device(device_id) for each
```

Adding a new NOTIFY channel: emit via `pg_notify(channel, payload)`,
add a listener via `connection.add_listener(channel, callback)` on a
dedicated asyncpg connection (NOT the SQLAlchemy pool — pooled
connections are reused and the LISTEN registration is per-connection).

---

## 8. Common queries

### "What's the active session?"

```sql
SELECT * FROM sessions
WHERE scope = 'global' AND ended_at IS NULL;
```

### "Events for sensor 5 on device 1, last hour"

```sql
SELECT event_id, triggered_at, event_type, triggered_value, metadata
FROM events
WHERE device_id = 1
  AND sensor_id = 5
  AND triggered_at > now() - interval '1 hour'
ORDER BY triggered_at DESC;
```

Hits `events_by_sensor_ts` index.

### "Find events whose metadata mentions threshold_cv > 10"

```sql
SELECT event_id, triggered_at, metadata
FROM events
WHERE metadata @> '{"threshold_cv": 10}';
```

Hits `events_metadata_gin` index. (Note: `@>` is JSONB containment.)

### "Sessions currently recording raw samples"

```sql
SELECT session_id, scope, device_id, started_at
FROM sessions
WHERE ended_at IS NULL AND record_raw_samples = TRUE;
```

This is the same query `SessionSampleWriter._refresh_recording_set`
runs every 5 s.

### "Total raw samples archived for a session"

```sql
SELECT count(*) FROM session_samples WHERE session_id = '<uuid>';
```

Slow on a huge session because the count touches every chunk. Operator
UI doesn't run this — it's a forensic query.

### "Force-decompress an old chunk for ad-hoc investigation"

```sql
SELECT decompress_chunk(chunk_name) FROM timescaledb_information.chunks
WHERE hypertable_name = 'events'
  AND range_start <= '2026-04-01'
  AND range_end   >  '2026-04-01';
```

Re-compress with `compress_chunk(chunk_name)` after.

### "Apply all migrations from scratch"

```bash
export MIGRATE_DATABASE_URL=postgresql://hermes_migrate:...@localhost/hermes
./scripts/db-migrate.sh
```

Idempotent. Runs every `migrations/00*.sql` in order.
