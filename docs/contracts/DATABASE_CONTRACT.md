# DATABASE_CONTRACT.md

## 1. Connection model

### 1.1 Class: `MQTTDatabase` (`/home/embed/hammer/src/database/mqtt_database.py` line 42)

One `MQTTDatabase` instance is created per logical consumer. The live system constructs **four** independent instances against the same underlying SQLite file:

| Instance              | Created at                                                       | Purpose                                           |
| --------------------- | ---------------------------------------------------------------- | ------------------------------------------------- |
| main `services.db`    | startup (web_server.py)                                          | UI reads, CRUD, config writes                     |
| `snapshot_db`         | `worker_manager.py` line 23                                      | Continuous `insert_continuous_sensor_data_batch`  |
| `update_db`           | `worker_manager.py` line 24                                      | `batch_update_event_detection`                    |
| ad-hoc read conns     | opened per call in `get_device`, `get_all_devices`, `get_events`, `find_event_id`, `find_snapshot_id`, `insert_event_direct` | Short-lived concurrent readers/writers           |

Every instance independently re-runs `_init_database()`, including the full migration chain (line 57), on construction.

### 1.2 Persistent connection (line 64)

```python
self.connection = sqlite3.connect(self.db_path, check_same_thread=False, timeout=60.0)
```

- `check_same_thread=False` — the connection is shared across Python threads. SQLite's "footgun" mode; serialization is entirely the caller's responsibility.
- `timeout=60.0` — Python-level wait for the writer lock (distinct from `PRAGMA busy_timeout`).

### 1.3 Thread serialization

Every public DB method is wrapped in `with self.lock:` (`self.lock = threading.Lock()`, line 49). All reads AND writes serialize on this single lock. No distinction between readers and writers at the Python level.

Exceptions that bypass `self.lock` (open their own short-lived `sqlite3.connect`):

- `get_device` (line 1436)
- `get_all_devices` (line 1457)
- `get_events` (line 3518)
- `find_event_id` (line 3365)
- `find_snapshot_id` (line 3390)
- `insert_event_direct` (line 3341)

These rely on WAL mode to avoid blocking the main connection's writes.

### 1.4 PRAGMAs applied at init (lines 68–80)

```
PRAGMA busy_timeout       = 60000      (60 s)
PRAGMA journal_mode       = WAL
PRAGMA synchronous        = OFF
PRAGMA cache_size         = -64000     (64 MB, negative = KB)
PRAGMA temp_store         = MEMORY
PRAGMA foreign_keys       = ON
PRAGMA wal_autocheckpoint = 500        (pages; ~2 MB at 4 KB pages)
PRAGMA journal_size_limit = 4194304    (4 MB WAL cap)
```

After `_init_database` seeds `app_config`, `_apply_configured_pragmas()` (line 1180) re-runs `busy_timeout`, `cache_size`, `wal_autocheckpoint`, `journal_size_limit` using the *current* app_config values (keys: `db_busy_timeout_ms`, `db_cache_size_kb`, `db_wal_checkpoint_pages`, `db_journal_size_limit_bytes`). `synchronous`, `journal_mode`, and `foreign_keys` are NOT re-applied from config — once set they stick.

`insert_event_direct` uses a private connection with `PRAGMA busy_timeout = 10000` (10 s).
`find_event_id` / `find_snapshot_id` / `get_events` use `PRAGMA busy_timeout = 30000` (30 s).

### 1.5 DB path resolution (`resolve_mqtt_db_path`, line 23)

```
DEFAULT_DB_PATH  = "/mnt/ssd/mqtt_database/mqtt_database.db"
FALLBACK_DB_PATH = <module_dir>/mqtt_database.db
```

Resolution order:
1. `os.getenv("MQTT_DATABASE_PATH")` if set.
2. Caller-provided `preferred_path`.
3. `DEFAULT_DB_PATH`.

The first of those that satisfies `_parent_dir_is_writable` (dir exists AND writable) is used. If none satisfy AND the candidate file does not already exist, it falls back to `FALLBACK_DB_PATH` (always writable — module directory).

The fallback path is reported via stdout: `[MQTTDatabase] Falling back to writable local DB path: ...`.

### 1.6 Corruption recovery (lines 2899–2945)

`_is_corruption_error` matches "database disk image is malformed", "file is not a database", or generic "malformed" in the error string. `_recover_from_corruption` renames the DB file to `<path>.corrupt-YYYYMMDD_HHMMSS` and re-runs `_init_database` on a fresh file. Guarded by `self._recovery_cooldown_s` (default 30 s, sourced from app_config key `db_recovery_cooldown_s`). Only invoked from `insert_continuous_sensor_data` and `insert_continuous_sensor_data_batch`.

---

## 2. Schema — complete inventory

### 2.1 `device_names` (line 83)

Purpose: legacy custom-name overlay; keyed by **string** `device_id` (e.g. `"device-1"`) which is disjoint from the integer `devices.device_id` PK.

```sql
CREATE TABLE device_names (
    device_id   TEXT PRIMARY KEY,
    custom_name TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

No indexes beyond implicit PK. Row count at steady state: 0-N custom names.

### 2.2 `devices` (line 128)

Purpose: authoritative device list for the MQTT-only deployment.

```sql
CREATE TABLE devices (
    device_id             INTEGER PRIMARY KEY,
    device_name           TEXT NOT NULL,
    is_active             BOOLEAN DEFAULT 1,
    topic                 TEXT,
    use_per_sensor_config BOOLEAN DEFAULT 0,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX idx_device_id ON devices(device_id);
```

No CHECK on `device_id`. Foreign keys reference this from every per-sensor config table (but those tables' `CHECK (device_id BETWEEN 1 AND 20)` is on the child side, not here).

Steady state: 1–20 rows.

### 2.3 `users` (line 145)

```sql
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_login    TEXT
);
```

Seeded with `admin / admin` (SHA-256 hex-digest hash) if empty (line 155–164). `last_login` is never written by this module — only authentication lookups. Steady state: 1 row.

### 2.4 `system_config` (line 169)

```sql
CREATE TABLE system_config (
    id                     INTEGER PRIMARY KEY CHECK (id = 1),
    auto_restart_enabled   BOOLEAN DEFAULT 0,
    restart_time_hour      INTEGER DEFAULT 3,
    restart_time_minute    INTEGER DEFAULT 0,
    last_restart_timestamp TEXT,
    created_at             TEXT DEFAULT (datetime('now')),
    updated_at             TEXT DEFAULT (datetime('now')),
    CHECK (restart_time_hour   BETWEEN 0 AND 23),
    CHECK (restart_time_minute BETWEEN 0 AND 59)
);
```

Singleton row (`id=1`). Accessed via `get_system_config` / `update_system_config`. `update_system_config` writes `updated_at = datetime('now')` via SQL (UTC-ish, differs from the Python-formatted `%Y-%m-%d %H:%M:%S` strings written everywhere else). `last_restart_timestamp` is written in ISO-8601 with microseconds by `auto_restart.py`, inconsistent with the rest of the schema.

### 2.5 `app_config` (line 196)

```sql
CREATE TABLE app_config (
    key              TEXT PRIMARY KEY,
    value            TEXT NOT NULL,
    default_val      TEXT NOT NULL,
    data_type        TEXT NOT NULL CHECK (data_type IN ('int','float','str','bool')),
    category         TEXT NOT NULL,
    label            TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    requires_restart INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_app_config_category ON app_config(category);
```

Seeded via `INSERT OR IGNORE` from `app_config_defaults` (line 211). Counting the literal tuples: **96 default rows** spanning 16 categories (Acquisition, Detection, Mode Switching, Type A/B/C/D, Live Stream, UI, MQTT, Database, Server, Workers, API, Device, Logging, Auth, Validation). See §4.1 for full behavior.

### 2.6 `event_config_type_a` (line 427)

```sql
CREATE TABLE event_config_type_a (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    timeframe_seconds REAL NOT NULL,
    threshold_lower   REAL NOT NULL,
    threshold_upper   REAL NOT NULL,
    enabled           BOOLEAN DEFAULT 1,
    ttl_seconds       REAL NOT NULL DEFAULT 5.0,
    debounce_seconds  REAL NOT NULL DEFAULT 0.0,  -- added via ALTER TABLE (line 465)
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    CHECK (timeframe_seconds BETWEEN 1 AND 60),
    CHECK (threshold_lower <= threshold_upper),
    CHECK (ttl_seconds > 0)
);
```

Singleton global row (`id=1`).

### 2.7 `event_config_type_a_per_sensor` (line 471)

```sql
CREATE TABLE event_config_type_a_per_sensor (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id         INTEGER NOT NULL,
    sensor_id         INTEGER NOT NULL,
    timeframe_seconds REAL NOT NULL,
    threshold_lower   REAL NOT NULL,
    threshold_upper   REAL NOT NULL,
    enabled           BOOLEAN DEFAULT 1,
    ttl_seconds       REAL DEFAULT 5.0,   -- added via ALTER TABLE (line 498)
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE(device_id, sensor_id),
    FOREIGN KEY (device_id) REFERENCES devices(device_id),
    CHECK (device_id BETWEEN 1 AND 20),
    CHECK (sensor_id BETWEEN 1 AND 12),
    CHECK (timeframe_seconds BETWEEN 1 AND 60),
    CHECK (threshold_lower <= threshold_upper)
);
CREATE INDEX idx_per_sensor_device        ON event_config_type_a_per_sensor(device_id);
CREATE INDEX idx_per_sensor_device_sensor ON event_config_type_a_per_sensor(device_id, sensor_id);
```

Steady state: up to 20 × 12 = 240 rows.

Note there is no `debounce_seconds` column on the per-sensor table (unlike the global); per-sensor configs inherit debounce from global.

### 2.8 `event_config_type_b` (line 566)

```sql
CREATE TABLE event_config_type_b (
    id                 INTEGER PRIMARY KEY CHECK (id = 1),
    t2_seconds         REAL NOT NULL,
    threshold_lower    REAL NOT NULL,
    threshold_upper    REAL NOT NULL,
    pre_event_seconds  REAL NOT NULL,
    post_event_seconds REAL NOT NULL,
    enabled            BOOLEAN DEFAULT 1,
    ttl_seconds        REAL NOT NULL DEFAULT 10.0,
    debounce_seconds   REAL NOT NULL DEFAULT 0.0,  -- added via ALTER TABLE (line 628)
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    CHECK (t2_seconds         > 0),
    CHECK (pre_event_seconds  >= 0),
    CHECK (post_event_seconds >= 0),
    CHECK (ttl_seconds > 0)
);
```

No CHECK on threshold ordering (unlike Type A/C/D). Default seed (line 637): `t2=2.0, lower=40.0, upper=60.0, pre=9.0, post=9.0, ttl=10.0, debounce=0.0`.

### 2.9 `event_config_type_b_per_sensor` (line 640)

```sql
CREATE TABLE event_config_type_b_per_sensor (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id          INTEGER NOT NULL,
    sensor_id          INTEGER NOT NULL,
    t2_seconds         REAL NOT NULL,
    threshold_lower    REAL NOT NULL,  -- added via ALTER (line 666)
    threshold_upper    REAL NOT NULL,  -- added via ALTER (line 668)
    pre_event_seconds  REAL NOT NULL,
    post_event_seconds REAL NOT NULL,
    enabled            BOOLEAN DEFAULT 1,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE(device_id, sensor_id),
    FOREIGN KEY (device_id) REFERENCES devices(device_id),
    CHECK (device_id BETWEEN 1 AND 20),
    CHECK (sensor_id BETWEEN 1 AND 12),
    CHECK (t2_seconds         > 0),
    CHECK (pre_event_seconds  >= 0),
    CHECK (post_event_seconds >= 0)
);
CREATE INDEX idx_type_b_per_sensor_device        ON event_config_type_b_per_sensor(device_id);
CREATE INDEX idx_type_b_per_sensor_device_sensor ON event_config_type_b_per_sensor(device_id, sensor_id);
```

No `ttl_seconds` or `debounce_seconds` per-sensor.

### 2.10 `event_config_type_c` (line 712)

```sql
CREATE TABLE event_config_type_c (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    t3_seconds       REAL NOT NULL,
    threshold_lower  REAL NOT NULL,
    threshold_upper  REAL NOT NULL,
    enabled          BOOLEAN DEFAULT 1,
    ttl_seconds      REAL NOT NULL DEFAULT 3.0,
    debounce_seconds REAL NOT NULL DEFAULT 0.0,  -- added via ALTER (line 758)
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    CHECK (t3_seconds > 0),
    CHECK (threshold_lower < threshold_upper),   -- strict less-than!
    CHECK (ttl_seconds > 0)
);
```

**Note the strict `<`** — combined with the `max(upper, lower + 0.01)` rewrite in `save_type_c_per_sensor_config` (line 1919) and `save_type_c_per_sensor_configs_bulk` (line 1979), this layered behavior means equal lower/upper is silently bumped to `lower+0.01`.

Seed default (line 767): `t3=5.0, lower=40.0, upper=60.0, ttl=3.0, debounce=0.0`.

### 2.11 `event_config_type_c_per_sensor` (line 862)

```sql
CREATE TABLE event_config_type_c_per_sensor (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id          INTEGER NOT NULL,
    sensor_id          INTEGER NOT NULL,
    t3_seconds         REAL NOT NULL,
    threshold_lower    REAL NOT NULL,
    threshold_upper    REAL NOT NULL,
    pre_event_seconds  REAL NOT NULL,
    post_event_seconds REAL NOT NULL,
    enabled            BOOLEAN DEFAULT 1,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE(device_id, sensor_id),
    FOREIGN KEY (device_id) REFERENCES devices(device_id),
    CHECK (device_id BETWEEN 1 AND 20),
    CHECK (sensor_id BETWEEN 1 AND 12),
    CHECK (t3_seconds > 0),
    CHECK (threshold_lower < threshold_upper),   -- strict less-than
    CHECK (pre_event_seconds  >= 0),
    CHECK (post_event_seconds >= 0)
);
CREATE INDEX idx_type_c_per_sensor_device        ON event_config_type_c_per_sensor(device_id);
CREATE INDEX idx_type_c_per_sensor_device_sensor ON event_config_type_c_per_sensor(device_id, sensor_id);
```

### 2.12 `event_config_type_d` (line 771)

```sql
CREATE TABLE event_config_type_d (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    t4_seconds       REAL NOT NULL,
    t5_seconds       REAL NOT NULL DEFAULT 30.0,  -- added via full table rebuild (line 817)
    threshold_lower  REAL NOT NULL,
    threshold_upper  REAL NOT NULL,
    enabled          BOOLEAN DEFAULT 1,
    ttl_seconds      REAL NOT NULL DEFAULT 8.0,
    debounce_seconds REAL NOT NULL DEFAULT 0.0,   -- added via ALTER (line 850)
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    CHECK (t4_seconds > 0),
    CHECK (t5_seconds > 0),
    CHECK (threshold_lower <= threshold_upper),  -- NOTE: <= after rebuild at line 829
    CHECK (ttl_seconds > 0)
);
```

Table is rebuilt (line 817) when `t5_seconds` is missing. At that point the CHECK is `threshold_lower <= threshold_upper` (less-than-or-equal, loosened from the earlier `<`). If the rebuild never ran (fresh installs, line 771 path), the original DDL still has `threshold_lower < threshold_upper`.

Seed (line 859): `t4=10.0, t5=30.0, lower=40.0, upper=60.0, ttl=8.0, debounce=0.0`.

### 2.13 `event_config_type_d_per_sensor` (line 889)

```sql
CREATE TABLE event_config_type_d_per_sensor (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id          INTEGER NOT NULL,
    sensor_id          INTEGER NOT NULL,
    t4_seconds         REAL NOT NULL,
    threshold_lower    REAL NOT NULL,
    threshold_upper    REAL NOT NULL,
    pre_event_seconds  REAL NOT NULL,
    post_event_seconds REAL NOT NULL,
    enabled            BOOLEAN DEFAULT 1,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE(device_id, sensor_id),
    FOREIGN KEY (device_id) REFERENCES devices(device_id),
    CHECK (device_id BETWEEN 1 AND 20),
    CHECK (sensor_id BETWEEN 1 AND 12),
    CHECK (t4_seconds > 0),
    CHECK (threshold_lower < threshold_upper),   -- strict; never loosened
    CHECK (pre_event_seconds  >= 0),
    CHECK (post_event_seconds >= 0)
);
CREATE INDEX idx_type_d_per_sensor_device        ON event_config_type_d_per_sensor(device_id);
CREATE INDEX idx_type_d_per_sensor_device_sensor ON event_config_type_d_per_sensor(device_id, sensor_id);
```

No `t5_seconds` per-sensor — always inherits from global.

### 2.14 `mode_switching_config` (line 917)

```sql
CREATE TABLE mode_switching_config (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    enabled                  BOOLEAN DEFAULT 0,
    startup_threshold        REAL NOT NULL DEFAULT 100.0,
    break_threshold          REAL NOT NULL DEFAULT 50.0,
    startup_duration_seconds REAL NOT NULL DEFAULT 2.0,
    break_duration_seconds   REAL NOT NULL DEFAULT 2.0,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    CHECK (startup_threshold > break_threshold),
    CHECK (startup_duration_seconds > 0),
    CHECK (break_duration_seconds   > 0)
);
```

Seed (line 940): `enabled=0, startup=100.0, break=50.0, startup_dur=0.1, break_dur=2.0`. Note seed value of 0.1 contradicts the table's default 2.0.

### 2.15 `mode_switching_config_per_sensor` (line 945)

```sql
CREATE TABLE mode_switching_config_per_sensor (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id                INTEGER NOT NULL,
    sensor_id                INTEGER NOT NULL,
    startup_threshold        REAL NOT NULL,
    break_threshold          REAL NOT NULL,
    startup_duration_seconds REAL NOT NULL,
    break_duration_seconds   REAL NOT NULL,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    UNIQUE(device_id, sensor_id),
    FOREIGN KEY (device_id) REFERENCES devices(device_id),
    CHECK (device_id BETWEEN 1 AND 20),
    CHECK (sensor_id BETWEEN 1 AND 12),
    CHECK (startup_threshold > break_threshold),
    CHECK (startup_duration_seconds > 0),
    CHECK (break_duration_seconds   > 0)
);
CREATE INDEX idx_mode_switching_per_sensor_device        ON mode_switching_config_per_sensor(device_id);
CREATE INDEX idx_mode_switching_per_sensor_device_sensor ON mode_switching_config_per_sensor(device_id, sensor_id);
```

### 2.16 `avg_type_b_selection` (line 978)

```sql
CREATE TABLE avg_type_b_selection (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    device_id  INTEGER,
    sensor_id  INTEGER,
    updated_at TEXT
);
```

Singleton (`id=1`). Stores the currently-selected (device, sensor) pair for Avg Type B UI.

### 2.17 `events` (line 990) — see §3 for deep dive

### 2.18 `sensor_offsets` (line 1157)

```sql
CREATE TABLE sensor_offsets (
    device_id  INTEGER NOT NULL,
    sensor_id  INTEGER NOT NULL,
    offset     REAL    NOT NULL DEFAULT 0.0,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (device_id, sensor_id)
);
```

No CHECK on ranges. No FK to `devices`. Steady state: 0 up to 20 × 12 = 240 rows (only stored when non-default).

---

## 3. The `events` wide table (deep dive)

### 3.1 Complete column inventory (line 990)

**Base columns (3):**
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `device_id INTEGER NOT NULL` — CHECK `BETWEEN 1 AND 20`
- `timestamp REAL NOT NULL` — Unix epoch seconds (float, sub-second precision)
- `event_datetime TEXT` — local-time `'YYYY-MM-DD HH:MM:SS.fff'` (milliseconds when written by event path), `'YYYY-MM-DD HH:MM:SS'` when written by snapshot path. NULL for snapshot-only rows.

**Per-sensor columns (12 sensors × 8 cols = 96):**
For each `N` in 1..12:
- `sensorN_value REAL`
- `sensorN_variance REAL`
- `sensorN_average REAL`
- `sensorN_event_a TEXT` with `CHECK(sensorN_event_a IN ('Yes', 'No', NULL))`
- `sensorN_event_b TEXT` with same CHECK
- `sensorN_event_c TEXT` with same CHECK
- `sensorN_event_d TEXT` with same CHECK
- `sensorN_event_break TEXT` — **no CHECK** (added via ALTER, line 1143)

**Metadata / blob (2):**
- `created_at TEXT NOT NULL`
- `data_window BLOB` — added via `ALTER TABLE` (line 1137)

**Total columns: 3 + 1 + 96 + 2 = 102.** (The get_events method docstring at line 3502 says "89 columns" — this predates the addition of `data_window` and `sensorN_event_break`.)

**Referenced but undefined:** `save_event` at line 2871 appends a `notes` column in its INSERT column list, but no `notes` column exists in the CREATE TABLE. Any call to `save_event` would raise `sqlite3.OperationalError: no such column: notes`. `save_event` is apparently unreachable in the live system — the live path uses `batch_update_event_detection` + `_insert_event_fallback`, which do NOT reference `notes`.

### 3.2 CHECK constraints

- 48 CHECKs across the A/B/C/D columns (12 sensors × 4 event types), each enforcing the string literal set `('Yes', 'No', NULL)`.
- **No** CHECK on the 12 `sensorN_event_break` columns (line 1146–1151). Comment there: "No CHECK constraint here — avoids full table scan on large databases (O(1) schema-only change)".
- 1 CHECK on `device_id BETWEEN 1 AND 20`.

**Write-path cost**: every INSERT and every UPDATE that touches any of the 48 `_event_{a,b,c,d}` columns runs the respective CHECK. SQLite short-circuits if the column isn't mentioned in a SET list, but INSERTs always hit all 48.

### 3.3 Indexes (lines 1128–1133)

```sql
CREATE INDEX idx_event_device_id        ON events(device_id);
CREATE INDEX idx_event_timestamp        ON events(timestamp);
CREATE INDEX idx_event_datetime         ON events(event_datetime);
CREATE INDEX idx_event_device_timestamp ON events(device_id, timestamp);
```

Index usage:
- `idx_event_device_timestamp` is hit by `get_event_data_window`'s window reconstruction (`WHERE device_id = ? AND timestamp >= ? AND timestamp <= ?`) and by `batch_update_event_detection`'s inner SELECT (`WHERE device_id = ? AND ABS(timestamp - ?) < 0.500`).
- `idx_event_datetime` supports `get_events`' optional `event_datetime` range filter.
- `idx_event_timestamp` supports the `ORDER BY timestamp DESC` in `get_events`.
- `idx_event_device_id` is redundant with the composite index's left prefix but kept.

**Not indexed**: none of the `sensor{N}_event_*` columns. `get_events(sensor_id=N)` performs a full filter over matching `device_id` rows.

### 3.4 Write path: `batch_update_event_detection` (line 3060)

Signature: `batch_update_event_detection(event_updates: List[Dict]) -> List[Dict]`. Each item is `{device_id, timestamp, sensor_id, variance?, average?, event_flags?, data_window?, sensor_snapshot?}`.

Outer transaction (line 3073): `self.connection.execute("BEGIN IMMEDIATE")`. Held under `self.lock`.

For each event:

1. Build dynamic UPDATE columns: `sensor{N}_variance`, `sensor{N}_average`, each present `sensor{N}_event_{a,b,c,d,break}` flag, and `data_window` — only columns actually supplied are included in the SET list.
2. Always appends `event_datetime = COALESCE(event_datetime, ?)` — preserves the first timestamp written.
3. Execute:
   ```sql
   UPDATE events SET <cols>
   WHERE rowid = (
       SELECT rowid FROM events
       WHERE device_id = ? AND ABS(timestamp - ?) < 0.500
       ORDER BY ABS(timestamp - ?)
       LIMIT 1
   )
   ```
4. If `cursor.rowcount == 0` (no matching row within 500 ms), call `_insert_event_fallback(cursor, event)` which INSERTs a new row including `sensor{idx+1}_value` from `sensor_snapshot` (or NULL), the triggered sensor's variance/average, event flags for 'a','b','c','d','break', and `data_window` if present.

After the loop, `self.connection.commit()` (line 3140). On any exception inside the block, `rollback()` then re-raise (line 3147).

**Retry policy** (line 3155): on `sqlite3.OperationalError` containing "database is locked", sleeps 0.2 s and re-calls the entire function once recursively. If still failing, returns the original `event_updates` list back to the caller (worker_manager retries up to 5 times per event with increment count).

**500ms dedup semantics**: two events (same device, different sensor or same sensor different type) with timestamps within 500 ms collapse into one row. The first writer's `event_datetime` is preserved (COALESCE). `sensor{N}_event_*` flags for each sensor's type get set independently. **`data_window` is overwritten** by the later writer — the earlier writer's BLOB is lost (the column has no COALESCE protection; if `data_window is not None` on the later update, it replaces).

### 3.5 Read path: `get_events` (line 3497)

Signature: `get_events(device_id=None, sensor_id=None, algorithm_type=None, start_time=None, end_time=None, start_ts=None, end_ts=None, limit=100)`.

Base query:
```sql
SELECT * FROM events WHERE event_datetime IS NOT NULL
```

Optional predicates appended:
- `device_id` → `AND device_id = ?`
- `sensor_id` → `AND sensor{id}_event_a = 'Yes'` — **note this hard-codes event type A**. If the event was a B/C/D-only trigger with no A, the row is missing from sensor-filtered queries.
- `start_ts` / `end_ts` → `AND timestamp >= ?` / `AND timestamp <= ?` (preferred)
- Otherwise `start_time` / `end_time` → `AND event_datetime >= ?` / `AND event_datetime <= ?`

Suffix: `ORDER BY timestamp DESC LIMIT ?`.

Uses a fresh short-lived `sqlite3.connect` (line 3518) — does NOT take `self.lock`, relies on WAL to read while writers hold the persistent connection. Row factory is `sqlite3.Row`; each returned dict excludes the `data_window` BLOB (line 3559: `if k != 'data_window'`) so JSON serialization downstream doesn't break on bytes.

**Index usage**: `idx_event_datetime` or `idx_event_timestamp` serves the ORDER BY + filter; device filter uses `idx_event_device_timestamp` when both are present. `sensor_id` filter always degenerates into a scan.

### 3.6 `data_window` BLOB

**Format**: UTF-8 JSON bytes. `json.dumps(data_window_dict).encode('utf-8')`. NOT MessagePack, NOT zlib-compressed.

Schema of the decoded dict:
```
{
  "window_start":     float (event_ts - pre_seconds),
  "window_end":       float (event_ts + post_seconds),
  "event_center":     float,
  "triggered_sensors": [int, ...],
  "sensor_<N>": [{"timestamp": float, "value": float}, ...],  -- only for triggered sensors
  "raw_frames": [...],        -- optional, passed through from circular_buffer
  "bounds_B": {...}, "bounds_D": {...}  -- optional, embedded by event_detector
}
```

**Populated by**:
- `save_event` (line 2788) — unused in live flow; builds from `circular_buffer` argument.
- `batch_update_event_detection` (line 3060) and `_insert_event_fallback` (line 3410) — receive the BLOB pre-serialized from `event['data_window']`. The blob is built upstream by `event_detector._extract_data_window` and passed through as already-serialized `bytes`.
- `insert_event_direct` (line 3306) — does NOT write `data_window`.

**Size estimate at 18 s × 123 Hz × 12 sensors**: 18 × 123 ≈ 2214 samples per sensor; a JSON `{"timestamp":1733512345.123,"value":52.04}` is ~40 bytes. Worst case 2214 × 40 × 12 = ~1.06 MB per event BLOB if ALL sensors triggered. In practice only the triggered sensors appear — typical 1-2 sensor events produce ~90-180 KB blobs.

**Read path**: `get_event_data_window(event_id)` (line 3569). Fast path returns the raw bytes. Contains a **legacy repair pass** (lines 3613–3627):

```python
_dw = json.loads(data_window_blob)
for _sid in range(1, 13):
    for _pt in _dw.get(f"sensor_{_sid}", []):
        if isinstance(_pt.get('value'), list) and len(_pt['value']) == 2:
            _pt['value'] = _pt['value'][1]   # unpack (ts, val) tuple that was JSON-encoded as array
            _repaired = True
if _repaired:
    data_window_blob = json.dumps(_dw, separators=(',',':')).encode('utf-8')
```

This repairs an older malformation where sensor samples were `[ts, val]` tuples instead of scalars, produced by a previous version of `_extract_data_window` that passed `(ts, val)` directly into the JSON structure. The repair runs on **every read** — full JSON decode + scan + re-encode — while holding `self.lock`. A 1 MB blob takes 50-200 ms to decode/scan; the entire DB is blocked during that time.

**Slow path** (line 3630): if `data_window` is NULL, reconstructs the window by SELECTing all snapshot rows in `[ts-9, ts+9]` and materializing a per-sensor sample list. Only reachable for pre-migration event rows.

---

## 4. Config storage

### 4.1 `app_config` key-value table

Row shape:
```
(key TEXT PK, value TEXT, default_val TEXT, data_type TEXT,
 category TEXT, label TEXT, description TEXT,
 requires_restart INTEGER, updated_at TEXT)
```

**Counted** seeded keys in `app_config_defaults` (line 211): **96 keys** across 16 categories.

Seeding semantics (line 375): `INSERT OR IGNORE INTO app_config (...)` — existing rows (with user-modified `value`) are never overwritten on startup. Only new keys appear.

**Post-seed corrections** (line 386–421):
1. `_seed_only_keys` list (21 keys mirroring event_config tables). Each gets `UPDATE app_config SET requires_restart = 1, description = description || ' (seed value — use Event Config page to change)'` if `requires_restart = 0`.
2. `_default_corrections`: `worker_batch_size_a: 50→100`, `worker_sleep_idle_update_s: 1.0→0.05`. Only applied if both current `value == old` and `default_val == old`.

**Value is always TEXT.** Downstream code must cast via `_cast_app_config_value` (line 3918). Casts:
- `int` → `int(raw)`
- `float` → `float(raw)`
- `bool` → `raw.lower() in ('1', 'true', 'yes')`
- `str` → passthrough
- `ValueError/TypeError` → returns raw string unchanged

Public methods:
- `get_app_config() -> Dict[str, Dict]` (line 3806): returns all rows keyed by `key`, with cast `value`, `raw_value`, `default_val`, `data_type`, `category`, `label`, `description`, `requires_restart`, `updated_at`.
- `get_app_config_value(key, fallback=None)` (line 3845): single-key cast lookup.
- `set_app_config_values(updates: Dict[str, str]) -> bool` (line 3863): bulk UPDATE. Values are coerced to `str()` before storing.
- `reset_app_config_to_defaults(keys=None)` (line 3890): `UPDATE app_config SET value = default_val [WHERE key IN (...)]`.

### 4.2 Config table save methods

#### Global (singleton `id=1`) saves

- `save_type_a_config(timeframe_seconds, threshold_lower, threshold_upper, enabled=True, ttl_seconds=5.0, debounce_seconds=0.0)` (line 1544)
- `save_type_b_config(t2_seconds, threshold_lower, threshold_upper, pre_event_seconds, post_event_seconds, enabled=True, ttl_seconds=10.0, debounce_seconds=0.0)` (line 1642)
- `save_type_c_config(t3_seconds, threshold_lower, threshold_upper, enabled=True, ttl_seconds=3.0, debounce_seconds=0.0)` (line 1835)
- `save_type_d_config(t4_seconds, threshold_lower, threshold_upper, enabled=True, ttl_seconds=8.0, t5_seconds=30.0, debounce_seconds=0.0)` (line 2039)
- `save_mode_switching_config(enabled=False, startup_threshold=100.0, break_threshold=50.0, startup_duration_seconds=0.1, break_duration_seconds=2.0)` (line 2433)

Each performs `SELECT id FROM <table> WHERE id = 1`, then UPDATE or INSERT depending. No transaction wrapping beyond implicit commit.

**Silent-reset behavior**: each save method takes `ttl_seconds` and `debounce_seconds` (where applicable) with a *default value*, and on UPDATE unconditionally writes them to the DB. If a caller (e.g. `set_type_d_enabled` in `web_server.py` line 940) omits `t5_seconds` in a kwargs call, the default `30.0` is written, overwriting any UI-set value. Same for `save_type_b_config` (callers omitting `ttl_seconds`/`debounce_seconds`), `save_type_c_config`, `save_type_d_config`.

#### Per-sensor saves (row keyed by UNIQUE(device_id, sensor_id))

- `save_type_a_per_sensor_config(device_id, sensor_id, timeframe_seconds, threshold_lower, threshold_upper, enabled=True, ttl_seconds=None)` (line 2231) — branches on `ttl_seconds is None` to either include or exclude that column.
- `save_type_b_per_sensor_config(device_id, sensor_id, t2_seconds, threshold_lower, threshold_upper, pre_event_seconds, post_event_seconds, enabled=True)` (line 1712)
- `save_type_c_per_sensor_config(device_id, sensor_id, t3_seconds, threshold_lower, threshold_upper, pre_event_seconds, post_event_seconds, enabled=True)` (line 1902) — applies `threshold_upper = max(threshold_upper, threshold_lower + 0.01)` silently (line 1919).
- `save_type_d_per_sensor_config(device_id, sensor_id, t4_seconds, threshold_lower, threshold_upper, pre_event_seconds, post_event_seconds, enabled=True)` (line 2107) — applies the same `max(upper, lower+0.01)` rewrite (line 2118).
- `save_mode_switching_per_sensor_config(device_id, sensor_id, startup_threshold, break_threshold, startup_duration_seconds, break_duration_seconds)` (line 2530)

#### Bulk per-sensor saves

- `save_type_b_per_sensor_configs_bulk(configs_by_device: Dict[int, Dict[int, Dict]])` (line 1755) — ON CONFLICT upsert, no transaction wrapping.
- `save_type_c_per_sensor_configs_bulk` (line 1947) — wraps in `BEGIN IMMEDIATE`, retries up to 3 times with `2^attempt * 0.1` s backoff on "database is locked". Applies `max(upper, lower+0.01)` inline (line 1979).
- `save_type_d_per_sensor_configs_bulk` (line 2151) — ON CONFLICT upsert, no transaction wrapping. Applies `max(upper, lower+0.01)` inline (line 2180).

Type A has no bulk save method.

#### Delete / clear

- `delete_type_a_per_sensor_config(device_id, sensor_id)` (line 2397) — deletes one row, reverts to global.
- `delete_mode_switching_per_sensor_config(device_id, sensor_id)` (line 2633)

### 4.3 Config read methods

#### Global getters (all return `Optional[Dict]`)

- `get_type_a_config()` → `{timeframe_seconds, threshold_lower, threshold_upper, enabled, ttl_seconds, debounce_seconds}` (line 1604)
- `get_type_b_config()` → `{t2_seconds, threshold_lower, threshold_upper, pre_event_seconds, post_event_seconds, enabled, ttl_seconds, debounce_seconds}` (line 1680)
- `get_type_c_config()` → `{t3_seconds, threshold_lower, threshold_upper, enabled, ttl_seconds, debounce_seconds}` (line 1872)
- `get_type_d_config()` → `{t4_seconds, t5_seconds, threshold_lower, threshold_upper, enabled, ttl_seconds, debounce_seconds}` (line 2076)
- `get_mode_switching_config()` → `{enabled, startup_threshold, break_threshold, startup_duration_seconds, break_duration_seconds}` (line 2493)

All use `COALESCE(debounce_seconds, 0.0)` on SELECT to handle rows that predate the ALTER TABLE migration.

#### Per-sensor getters (all rows)

- `get_type_b_per_sensor_configs()` (line 1801) → `{device_id: {sensor_id: {T2, lower_threshold, upper_threshold, pre_event_time, post_event_time, enabled}}}`
- `get_type_c_per_sensor_configs()` (line 2005) → same shape with `T3`
- `get_type_d_per_sensor_configs()` (line 2197) → same shape with `T4`
- `get_mode_switching_per_sensor_configs()` (line 2594) → `{device_id: {sensor_id: {startup_threshold, break_threshold, startup_duration_seconds, break_duration_seconds}}}`

#### Per-sensor getters (single sensor / device-filtered)

- `get_type_a_per_sensor_config(device_id, sensor_id)` (line 2313) — single row or None
- `get_all_type_a_per_sensor_configs_for_device(device_id)` (line 2354) → `{sensor_id: config}`

Only Type A has the device-filtered variant; B/C/D fetch all rows and filter in Python downstream.

**Note divergent key naming**: global getters return keys `threshold_lower`/`threshold_upper`; per-sensor getters (via bulk/non-bulk) return `lower_threshold`/`upper_threshold` and `pre_event_time`/`post_event_time` (vs `pre_event_seconds`/`post_event_seconds` on global). Callers must handle both spellings.

---

## 5. Device CRUD

### 5.1 `create_device` (line 1380)

Signature: `create_device(device_name, topic=None, is_active=1, **kwargs)`. `**kwargs` is accepted but IGNORED.

ID allocation (line 1398):
```python
cursor.execute("SELECT device_id FROM devices ORDER BY device_id")
used_ids = [row[0] for row in cursor.fetchall()]
device_id = next((i for i in range(1, 101) if i not in used_ids), None)
```

- **Range scanned**: `range(1, 101)` — caps at 100.
- **Conflict**: per-sensor config tables all have `CHECK (device_id BETWEEN 1 AND 20)`. Creating a device with `device_id` 21+ succeeds, but any subsequent save to `event_config_type_a_per_sensor`, `event_config_type_b_per_sensor`, etc. will fail the CHECK.
- Returns dict `{device_id, device_name, is_active, topic, created_at, updated_at}` on success, `None` on "maximum reached" or SQL error.

### 5.2 `get_device` (line 1432)

Opens a **new** `sqlite3.connect(self.db_path, timeout=10.0)` per call — no `self.lock`, no WAL checkpoint optimizations. Returns `dict(zip(columns, row))` or None.

### 5.3 `get_all_devices` (line 1453)

Same pattern as `get_device` — new connection per call. Returns `List[Dict]` ordered by `device_id`.

### 5.4 `update_device` (line 1476)

```python
allowed_fields = ['device_name', 'is_active', 'topic']
```

Any kwarg NOT in `allowed_fields` is **silently dropped**. Callers that pass `sample_rate_hz`, `hardware_version`, `model`, etc. (some blueprint code does) see success return but no database change.

Builds dynamic UPDATE with the accepted fields plus `updated_at`. Returns `True` unless SQL error — even a completely empty kwarg match returns `False` (line 1502 early return on empty updates list).

### 5.5 `delete_device` (line 1524)

```sql
DELETE FROM devices WHERE device_id = ?
```

No cascade — foreign keys are declared with `ON DELETE` default (`NO ACTION`). With `PRAGMA foreign_keys = ON` (line 73), a delete of a device that has per-sensor config rows would FAIL the FK constraint. The code does not clean child rows; callers must pre-delete per-sensor configs. If none exist, deletion succeeds and returns True.

### 5.6 `set_device_per_sensor_mode` (line 1256) / `get_device_per_sensor_mode` (line 1287)

Toggle the `devices.use_per_sensor_config` BOOLEAN (stored as 0/1). `get` returns False for missing device.

---

## 6. Sensor offsets

Table per §2.18. Access methods:

- `get_all_sensor_offsets(device_id) -> Dict[int, float]` (line 2669): returns a dict pre-initialized with `{1..12: 0.0}`, then overwrites with DB rows for the device. Missing sensors stay at 0.0.
- `save_sensor_offsets(device_id, offsets: Dict)` (line 2689): `INSERT ... ON CONFLICT(device_id, sensor_id) DO UPDATE` per sensor; one transaction, commits at end.
- `get_all_offsets_for_cache() -> Dict[str, Dict[int, float]]` (line 2713): returns all non-zero offsets across all devices. **Keys are stringified** `device_id` (`str(device_id)`), not int — mismatch with other dicts that use int keys.

Steady state: up to 240 rows (20 devices × 12 sensors). `get_all_offsets_for_cache` filters `WHERE offset != 0.0`.

---

## 7. Event retrieval / BLOB repair

### 7.1 `get_event_data_window` (line 3569) — details

Holds `self.lock` for the entire duration — including JSON decode/scan/re-encode.

Fast path (line 3611): if `data_window IS NOT NULL`:
1. `json.loads(blob)` — parse.
2. For each of 12 sensors: iterate `sensor_<N>` array; if any `value` is `list` of length 2, replace with `value[1]` (unpack `[ts, val]` tuple malformation).
3. If any repair happened, `json.dumps(..., separators=(',',':'))` (compact) and re-encode to bytes.
4. Return the (possibly-repaired) bytes.

Repair failures silently fall through to returning the original blob (line 3626).

Slow path (line 3630): reads all `sensor{N}_value` from rows in `[ts-9, ts+9]`, materializes the same `{window_start, window_end, event_center, triggered_sensors, sensor_1..sensor_12}` schema, json-encodes.

### 7.2 Malformation handled

The repair specifically catches the pattern where a sample's `value` field was serialized as a 2-element list (JSON array). Origin (per comment line 3613–3615): an earlier version of `_extract_data_window` stored `(ts, val)` tuples directly. Python's `json.dumps` converts tuples to arrays — so the JSON literal looked like `"value": [1733512345.123, 52.04]` instead of the scalar `"value": 52.04`. Fix takes the second element.

**Performance**: runs on every read. For a 1 MB blob with a few hundred thousand sample dicts, `json.loads` is ~50-150 ms in CPython; the iteration adds another 20-100 ms. Re-encoding adds similar cost. The `self.lock` is held throughout — writers block during every event window fetch by the UI.

---

## 8. Migrations (ordered, as executed in `_init_database`)

All migrations run inside the same `_init_database()` invocation on every construction of `MQTTDatabase`. With 4 instances in the live system, the full chain runs 4 times at startup (subsequent runs are no-ops because `IF NOT EXISTS`, `INSERT OR IGNORE`, and try/except around `ADD COLUMN`). **There is no `schema_version` table** — migration state is inferred from schema inspection every time.

| # | Location (line) | What it does | Idempotency |
|---|-----------------|--------------|-------------|
| 1 | 92–117 | Renames legacy `devices` table containing `base_can_id`/`device_type` cols, creates the MQTT-only schema, copies `device_id, device_name, is_active, created_at, updated_at`, drops old. | One-shot — gated on presence of `base_can_id` or `device_type` |
| 2 | 120–122 | `ALTER TABLE devices ADD COLUMN topic TEXT` | try/except OperationalError (already-exists safe) |
| 3 | 123–125 | `ALTER TABLE devices ADD COLUMN use_per_sensor_config BOOLEAN DEFAULT 0` | Column-presence check |
| 4 | 462–465 | `ALTER TABLE event_config_type_a ADD COLUMN debounce_seconds REAL NOT NULL DEFAULT 0.0` | `PRAGMA table_info` check |
| 5 | 497–501 | `ALTER TABLE event_config_type_a_per_sensor ADD COLUMN ttl_seconds REAL DEFAULT 5.0` | try/except OperationalError |
| 6 | 504–563 | Rebuild event_config_type_a + event_config_type_a_per_sensor when old CHECK uses strict `<` — CREATE `_new`, INSERT ... SELECT, DROP, RENAME | Gated on `threshold_lower < threshold_upper` substring in `sqlite_master.sql` |
| 7 | 585–622 | event_config_type_b: ADD `threshold_lower/upper/pre_event/post_event` columns; if legacy `tolerance` col exists, full rebuild dropping it | Column-presence checks |
| 8 | 625–628 | event_config_type_b: ADD `debounce_seconds` | Column-presence check |
| 9 | 662–702 | event_config_type_b_per_sensor: ADD `threshold_lower/upper`, rebuild if `tolerance` col exists | Column-presence checks |
| 10 | 728–752 | event_config_type_c rebuild to drop `pre_event_seconds`/`post_event_seconds` columns | Column-presence gated |
| 11 | 755–758 | event_config_type_c: ADD `debounce_seconds` | Column-presence check |
| 12 | 786–810 | event_config_type_d rebuild to drop `pre_event_seconds`/`post_event_seconds` columns | Column-presence gated |
| 13 | 813–844 | event_config_type_d: full rebuild to add `t5_seconds` AND loosen CHECK to `threshold_lower <= threshold_upper` | Gated on absence of `t5_seconds` col |
| 14 | 847–850 | event_config_type_d: ADD `debounce_seconds` | Column-presence check |
| 15 | 968–975 | mode_switching_config + mode_switching_config_per_sensor: rename `*_duration_samples` → `*_duration_seconds`, reset values to 2.0 | Gated on presence of `startup_duration_samples` col |
| 16 | 1136–1141 | events: `ALTER TABLE events ADD COLUMN data_window BLOB` | try/except OperationalError |
| 17 | 1143–1154 | events: loop `ALTER TABLE events ADD COLUMN sensor{n}_event_break TEXT` for n=1..12 — **no CHECK constraint intentionally** to avoid full table scan | try/except each; counts successes |

No rollback mechanism exists. A partial migration (e.g., power loss during a full-table rebuild at step 6, 7, 9, 10, 12, or 13) leaves the DB with `_new` tables that the next startup will either overwrite (CREATE TABLE IF NOT EXISTS preserves them) or get stuck on — the rebuild code does `CREATE TABLE IF NOT EXISTS ..._new` then unconditionally `INSERT ... SELECT` which would double-insert rows or fail on PK conflict. No transaction wraps the rebuilds.

---

## 9. Concurrency behavior

### 9.1 Serialization model

Single `sqlite3.Connection` (the `self.connection` attribute) is shared across all caller threads with `check_same_thread=False`. SQLite's C-level mutexing on a single connection serializes all operations on that connection already; the added `self.lock: threading.Lock()` guarantees Python-level atomicity of multi-statement sequences (the common `SELECT ... UPDATE` patterns in this module).

### 9.2 Reader-writer interaction

WAL journal mode (line 69) lets readers from *separate* connections operate without blocking the writer. The main connection's read/write path always serializes through `self.lock` — long reads (e.g. `get_event_data_window` with a large BLOB, `get_app_config` during startup reseed) block all writers of the main connection.

Methods that open new connections and escape `self.lock`:
- `get_device` / `get_all_devices` — every UI poll opens a new connection.
- `get_events` — ad-hoc read connection with `PRAGMA busy_timeout = 30000`.
- `find_event_id` / `find_snapshot_id` — same pattern.
- `insert_event_direct` — ad-hoc **write** connection with `PRAGMA busy_timeout = 10000`. This is the only writer that bypasses `self.lock`.

`worker_manager.snapshot_db` and `worker_manager.update_db` are separate `MQTTDatabase` instances — each has its own `sqlite3.Connection` on the same file. Their `self.lock` instances are independent, but SQLite's WAL writer-lock is global to the file — only one writer can commit at a time file-wide.

### 9.3 Explicit transactions

Most writes rely on SQLite's implicit transaction (auto-begun on first statement, committed by `self.connection.commit()`). Explicit `BEGIN IMMEDIATE` is used only in:
- `batch_update_event_detection` (line 3073)
- `update_event_detection` (line 3196)
- `save_type_c_per_sensor_configs_bulk` (line 1954)

`BEGIN IMMEDIATE` acquires the RESERVED lock up-front to avoid deferred-lock upgrades mid-transaction.

### 9.4 Retry/backoff on lock contention

- `update_event_detection` (line 3280): up to 3 retries with exponential backoff `(2^retry) * 0.1 + random.uniform(0, 0.05)` = 100–150 ms, 200–250 ms, 400–450 ms. Recursive re-call.
- `batch_update_event_detection` (line 3155): one retry after 200 ms sleep, recursive re-call.
- `save_type_c_per_sensor_configs_bulk` (line 1992): 3 retries with `(2^attempt) * 0.1` = 100 ms, 200 ms, 400 ms.

After exhausting retries, `batch_update_event_detection` returns the failed list back to `worker_manager`, which re-queues up to 5 more times before printing and dropping.

---

## 10. Specific value semantics

### 10.1 Boolean event flags

`sensorN_event_{a,b,c,d}` columns store the TEXT literals `'Yes'`, `'No'`, or NULL. The CHECK constraint enforces exactly this set (including NULL). The break columns accept any TEXT — no constraint.

`save_event` (line 2829) writes `'N/A'` as the default for missing flags — this would **violate** the CHECK constraint and fail the INSERT. Another reason `save_event` appears unreachable in the live path.

### 10.2 `event_datetime` format

- Written by `save_event` (line 2814): `'%Y-%m-%d %H:%M:%S'` (second precision, local time — `datetime.fromtimestamp(timestamp)`).
- Written by `update_event_detection` (line 3200), `batch_update_event_detection` (line 3089), `insert_event_direct` (line 3316): `'%Y-%m-%d %H:%M:%S.fff'` (millisecond precision via `dt.microsecond // 1000`).

Both are local-time strings with NO timezone marker. Sort ordering relies on lexicographic == chronological (works for fixed format).

### 10.3 `created_at`

Python-formatted `'%Y-%m-%d %H:%M:%S'` strings (e.g. line 160, 2965, 3014, 3317, 3424). `system_config.created_at`/`updated_at` default to `datetime('now')` via SQLite (UTC). `auto_restart.py` writes `last_restart_timestamp` as `datetime.isoformat()` — that's ISO-8601 with microseconds (`'YYYY-MM-DDTHH:MM:SS.ffffff'`), inconsistent with the rest.

### 10.4 `timestamp`

Float seconds, Unix epoch (`time.time()` or equivalent). NOT milliseconds. All dedup logic (`ABS(timestamp - ?) < 0.500`) is in seconds.

### 10.5 Other enum-ish storage

- `enabled` columns: BOOLEAN, stored 0/1, read back via `bool(row[n])`.
- No explicit enum columns in the post-migration schema. `device_type` was removed in migration step 1.

---

## 11. Known quirks (current behavior — extracted from code)

### 11.1 `max(upper, lower + 0.01)` silent rewrite
Applied in `save_type_c_per_sensor_config` (line 1919), `save_type_c_per_sensor_configs_bulk` (line 1979), `save_type_d_per_sensor_config` (line 2118), `save_type_d_per_sensor_configs_bulk` (line 2180). Type A's equivalent does NOT do this (CHECK is already `<=`). User-supplied `upper == lower` is silently bumped — breaks round-trip through the UI for equal thresholds.

### 11.2 `device_id` cap mismatch
- `create_device` allocates in `range(1, 101)` (cap 100).
- `devices.device_id` has no CHECK.
- All `*_per_sensor` tables CHECK `device_id BETWEEN 1 AND 20`.
- `app_config.device_max_count` defaults to 20.

Devices 21-100 can be created but are unusable for per-sensor config.

### 11.3 `update_device` silent field drop
Only `device_name`, `is_active`, `topic` are honored. `sample_rate_hz` and similar kwargs are silently dropped; the function still returns `True` when at least one allowed field was present.

### 11.4 `save_event` references non-existent `notes` column
Line 2871 appends `'notes'` to the INSERT column list. No such column exists. Calls to `save_event` would raise `OperationalError`. Not called in the live path.

### 11.5 `sensor{id}_event_a` filter hardcoded in `get_events`
Line 3533: `query += f" AND sensor{sensor_id}_event_a = 'Yes'"`. Filtering by sensor hides B/C/D-only events. Breaks expectations when UI asks "events for sensor N".

### 11.6 500 ms event collision dedup
`batch_update_event_detection` (line 3125) matches within 500 ms. Two distinct events within that window collapse into one row. Earlier `event_datetime` survives (COALESCE), later `data_window` BLOB overwrites earlier. No configurability — 500 ms is hard-coded.

### 11.7 Legacy BLOB repair on every read
`get_event_data_window` unconditionally parses the BLOB and scans for `[ts, val]` tuple malformation on every call. Silent; no metric.

### 11.8 `_seed_only_keys` mutation of description
Line 403: each seed-only key has `' (seed value — use Event Config page to change)'` appended to `description` every startup. With `INSERT OR IGNORE` seeding, the append only happens once per key because the guard `WHERE ... AND requires_restart = 0` only matches the first time — but if `requires_restart` is ever manually reset to 0, the suffix is re-appended on the next startup.

### 11.9 `save_type_*_config` default-value clobbering
Callers that construct via kwargs and omit `ttl_seconds`/`debounce_seconds`/`t5_seconds` silently overwrite the DB value with the Python default. Affects `set_type_d_enabled` (web_server.py), `set_type_b_enabled`, etc.

### 11.10 No schema version tracking
Migrations are detected by schema inspection on every startup. A partially-migrated DB (e.g., table rename interrupted) has no marker to indicate its state.

---

## 12. Secondary DB

### 12.1 `mqtt_config.db` (separate `MQTTConfigDB`)

Path: `<module_dir>/mqtt_database.db` — **note name collision**: `_DEFAULT_DB_PATH = os.path.join(..., "mqtt_database.db")`. But `mqtt_database.py` uses `FALLBACK_DB_PATH = <module_dir>/mqtt_database.db`. Both point to the SAME file when the primary `/mnt/ssd/...` path is unavailable. When both modules run concurrently against the same file, they create disjoint tables — `mqtt_config` (schema below) plus all the main-DB tables — and share the file's WAL and locking.

Class: `MQTTConfigDB` (`/home/embed/hammer/src/database/mqtt_config.py` line 29). Uses `self.lock: threading.Lock()` and opens a fresh connection per operation (`_connect` at line 38, `sqlite3.connect(db_path, check_same_thread=False)` plus `row_factory = sqlite3.Row`). No PRAGMAs set, no WAL mode. 

Schema (line 49):
```sql
CREATE TABLE mqtt_config (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    broker        TEXT NOT NULL DEFAULT 'localhost',
    port          INTEGER NOT NULL DEFAULT 1883,
    base_topic    TEXT NOT NULL DEFAULT 'canbus/sensors/data',
    websocket_url TEXT NOT NULL DEFAULT 'ws://localhost:9001/mqtt',
    username      TEXT,
    password      TEXT,
    enabled       INTEGER NOT NULL DEFAULT 1,
    updated_at    TEXT NOT NULL
);
```

Singleton row (`id=1`). Seeded on init (line 62) if empty with the `_DEFAULTS` dict values (line 18–26). `get_config()` returns a dict; `save_config(config=None, **kwargs)` does UPSERT keyed on `id=1`.

Module-level singleton `mqtt_db = MQTTConfigDB()` (line 159) is imported by `web_server.py`.

### 12.2 Other SQLite files in the repo

- `/home/embed/hammer/can_frames.db`
- `/home/embed/hammer/can_data.db`
- `/home/embed/hammer/data/can_frames.db`

These are legacy CAN-era DBs, not written by the MQTT modules. `grep` finds only `mqtt_database.py`, `mqtt_config.py` as active writers in `src/`.

---

## 13. File:line reference index

Primary module `/home/embed/hammer/src/database/mqtt_database.py`:

| Topic | Lines |
|-------|-------|
| Path resolution | 13-40 |
| MQTTDatabase.__init__ | 45-55 |
| _init_database (all migrations inline) | 57-1178 |
| PRAGMAs (initial) | 68-80 |
| app_config defaults seed (96 rows) | 211-373 |
| _seed_only_keys post-patch | 386-406 |
| Default value corrections | 411-421 |
| events CREATE TABLE | 990-1126 |
| events indexes | 1128-1133 |
| data_window column migration | 1136-1141 |
| sensor_event_break column migration (no CHECK) | 1143-1154 |
| sensor_offsets CREATE | 1157-1165 |
| _apply_configured_pragmas | 1180-1194 |
| device_names save/get | 1196-1338 |
| authenticate_user | 1340-1378 |
| create_device (range 1..101) | 1380-1430 |
| get_device / get_all_devices (fresh conn) | 1432-1474 |
| update_device (allowed_fields) | 1476-1522 |
| delete_device | 1524-1542 |
| Type A global save/get | 1544-1640 |
| Type B global save/get | 1642-1710 |
| Type B per-sensor save/bulk/get | 1712-1833 |
| Type C global save/get | 1835-1900 |
| Type C per-sensor save (max rewrite at 1919) | 1902-1945 |
| Type C per-sensor bulk (BEGIN IMMEDIATE + retries) | 1947-2003 |
| Type C per-sensor get | 2005-2037 |
| Type D global save (with t5_seconds default) | 2039-2074 |
| Type D global get | 2076-2105 |
| Type D per-sensor save (max rewrite at 2118) | 2107-2149 |
| Type D per-sensor bulk (max rewrite at 2180) | 2151-2195 |
| Type D per-sensor get | 2197-2229 |
| Type A per-sensor save/get/delete | 2231-2427 |
| Mode switching global save/get | 2433-2528 |
| Mode switching per-sensor save/get/delete | 2530-2663 |
| Sensor offsets | 2669-2734 |
| avg_type_b_selection save/get | 2736-2786 |
| save_event (references non-existent `notes`) | 2788-2897 |
| Corruption detection/recovery | 2899-2945 |
| insert_continuous_sensor_data | 2947-2994 |
| insert_continuous_sensor_data_batch | 2996-3058 |
| batch_update_event_detection (500ms dedup) | 3060-3173 |
| update_event_detection (retry) | 3175-3304 |
| insert_event_direct (separate connection) | 3306-3353 |
| find_event_id / find_snapshot_id | 3355-3408 |
| _insert_event_fallback | 3410-3464 |
| _ensure_event_marker | 3466-3495 |
| get_events (89 cols comment, wide SELECT, event_datetime filter) | 3497-3567 |
| get_event_data_window (BLOB + legacy repair) | 3569-3676 |
| get_event_metadata | 3678-3733 |
| system_config get/update | 3735-3800 |
| app_config get_all / get_value / set / reset / cast | 3806-3930 |
| close / __del__ | 3932-3940 |

Secondary module `/home/embed/hammer/src/database/mqtt_config.py`:

| Topic | Lines |
|-------|-------|
| Defaults dict | 18-26 |
| MQTTConfigDB class | 29-155 |
| _connect / _init | 38-77 |
| get_config | 80-106 |
| save_config (UPSERT) | 109-155 |
| Module singleton | 159 |
