# CONFIG_CATALOG.md

*Complete catalog of configuration sources, keys, consumers, and inconsistencies for HERMES. Generated from source (`/home/embed/hammer/src/database/mqtt_database.py`, `/home/embed/hammer/web_server.py`, `/home/embed/hammer/CLAUDE.md`, plus grep across the repo).*

## 1. Configuration sources (in priority order)

HERMES resolves a configuration value using three layers. At process start, each consumer that wants a tunable calls `db.get_app_config_value(key, fallback)` — the DB wins over the fallback. Env-var overrides are resolved *before* the DB value is read (see `/home/embed/hammer/web_server.py:592-617`), so env wins over DB.

1. **Environment variables (runtime override)** — Read by `os.getenv(...)` at module import time. `web_server.py` uses an `if not os.getenv("X"): X = _db_cfg(...)` pattern so env wins over DB.
2. **`app_config` SQLite table (persisted, editable via UI)** — Seeded by `_init_database()` using `INSERT OR IGNORE`, so user edits are never overwritten. Read via `MQTTDatabase.get_app_config_value(key, fallback)` (`/home/embed/hammer/src/database/mqtt_database.py:3845`).
3. **Hardcoded defaults in code** — Class attributes (`SAMPLE_RATE_HZ = 100` in `EventDetector`), module-level constants (`REF_VALUE = 100.0` in `src/events/constants.py`), or literal fallbacks in `get_app_config_value(..., fallback=X)` calls.

There is also a **fourth, separate sink**: `mqtt_config` (single-row table inside `src/database/mqtt_database.db`) managed by `src/database/mqtt_config.py::MQTTConfigDB`. It stores broker/port/topic independently of `app_config`. The two stores are **not kept in sync** — see §9.

## 2. Environment variables

| Name | Default | Purpose | Consumer file:line |
|---|---|---|---|
| `DEV_HOT_RELOAD` | `0` | Enable hot reload + no-cache headers in dev | `web_server.py:60`, `src/app/main.py:87`, `src/app/__init__.py:10` |
| `WERKZEUG_RUN_MAIN` | (auto) | Detected to avoid double-init under Flask reloader | `web_server.py:61`, `src/app/main.py:88` |
| `LIVE_BUFFER_MAX_SAMPLES` | `2500` (web_server) / `2000` (services) | Ring-buffer depth per sensor feeding SSE | `web_server.py:66`, `src/app/services.py:82`, `services.py:78` |
| `LIVE_STREAM_INTERVAL_S` | `0.1` | SSE flush interval | `web_server.py:67`, `src/app/services.py:83` |
| `SENSOR_LOG_ENABLED` | `0` | Write raw sensor 1 values to `logs/sensor.log` | `web_server.py:71`, `web_server.py:2943` |
| `SENSOR` | (unset) | Filter event-avg logs to a sensor/type (e.g. `"1A"`, `"1A,3B"`) | `src/utils/logging_config.py:23` |
| `MQTT_EVENT_DEBUG` | `0` | Verbose MQTT event-publish logs | `src/workers/worker_manager.py:409`, `src/mqtt/client.py:144` |
| `LATENCY_LOG_SECONDS` | `0` | Duration to capture API latency metrics | `web_server.py:63`, `src/app/services.py:56` |
| `LATENCY_LOG_PATH` | `logs/latency.log` | Path for latency capture | `web_server.py:64` |
| `LATENCY_LOG_DEVICE` | `1` | Device to capture latency for | `web_server.py:65` |
| `OTP_EXPIRY_SECONDS` | `300` | OTP validity | `web_server.py:51`, `src/app/routes/auth.py:15` |
| `OTP_MAX_ATTEMPTS` | `5` | OTP verification attempts before lockout | `web_server.py:52`, `src/app/routes/auth.py:16` |
| `OTP_RESEND_SECONDS` | `60` | Resend cooldown | `web_server.py:53`, `src/app/routes/auth.py:17` |
| `OTP_MAX_PER_HOUR` | `5` | Hourly rate-limit per email | `web_server.py:54`, `src/app/routes/auth.py:18` |
| `ALLOWED_EMAILS_PATH` | `emails.txt` | Email allowlist for OTP login | `web_server.py:55`, `src/app/routes/auth.py:19` |
| `SMTP_HOST` | (required) | SMTP server hostname | `web_server.py:1036`, `src/app/routes/auth.py:54` |
| `SMTP_PORT` | `587` (literal fallback) | SMTP port | `web_server.py:1037`, `src/app/routes/auth.py:55` |
| `SMTP_USER` | (required) | SMTP auth username | `web_server.py:1038`, `src/app/routes/auth.py:56` |
| `SMTP_PASS` | (required) | SMTP auth password | `web_server.py:1039`, `src/app/routes/auth.py:57` |
| `SMTP_FROM` | falls back to `SMTP_USER` | Email "From" field | `web_server.py:1040`, `src/app/routes/auth.py:58` |
| `MQTT_DATABASE_PATH` | unset → `/mnt/ssd/mqtt_database/mqtt_database.db` → local fallback | Override main SQLite path | `src/database/mqtt_database.py:25` |

No env var exists for the `PYTHONPYCACHEPREFIX` path (set by `run.sh`). `DEFAULT_DB_PATH` is a module constant, not an env var (`src/database/mqtt_database.py:13`).

## 3. app_config keys — complete catalog

Values below come from the `app_config_defaults` list at `src/database/mqtt_database.py:211-373`. All values persist as TEXT and must be cast by consumers. Live-apply handlers are branches inside `apply_app_config_live` in `web_server.py:621-717`, `src/detection/event_detector.py:1983-2059`, and `src/workers/worker_manager.py:74-109`. "Restart" = `requires_restart` flag column; "Live?" = live-apply handler exists.

### 3.1 Acquisition

| Key | Type | Default | Restart | Live? | Consumer | Description |
|---|---|---|---|---|---|---|
| `sample_rate_hz` | int | `100` | 1 | Yes — detector branch `event_detector.py:1992` | `event_detector.py:74` | ADC sample rate; buffer sizes derived from this |
| `total_sensors` | int | `12` | 1 | Partial — `web_server.py:650` | `web_server.py:605` | Per-device sensor count (see §5) |
| `stm32_adc_topic` | str | `stm32/adc` | 0 | Yes — triggers unsubscribe/resubscribe `web_server.py:703,711` | `web_server.py:607` | MQTT topic for STM32 payload |
| `timestamp_drift_threshold_s` | float | `5.0` | 0 | Yes — `web_server.py:648` | `web_server.py:608` | Max drift before STM32 anchor resync |

### 3.2 Detection — General

| Key | Type | Default | Restart | Live? | Consumer | Description |
|---|---|---|---|---|---|---|
| `event_pre_window_s` | float | `9.0` | 1 | Yes — `event_detector.py:2010` | `event_detector.py:76` | Seconds before trigger |
| `event_post_window_s` | float | `9.0` | 1 | Yes — `event_detector.py:2008` | `event_detector.py:75` | Seconds after trigger |
| `buffer_duration_a_s` | int | `60` | 1 | Yes — `event_detector.py:1997` | `event_detector.py:82` | Type A circular buffer depth |
| `buffer_duration_bcd_s` | int | `20` | 1 | Yes — `event_detector.py:2000` | `event_detector.py:83` | Type B/C/D circular buffer depth |
| `data_gap_reset_s` | float | `2.0` | 0 | Yes — `event_detector.py:2023` | `event_detector.py:87` | Gap that resets running sums |
| `ref_value` | float | `100.0` | 1 (forced by seed-only list) | No | **Unused at runtime** | UI knob; the actual value is `REF_VALUE = 100.0` literal in `src/events/constants.py:6` (see §5, §10) |
| `event_history_maxlen` | int | `200` | 1 | No | `event_detector.py` (passed to detectors at construction) | In-memory per-detector event history depth |
| `variance_window_deque_secs` | int | `78` | 1 | No | `event_detector.py:86` | Per-sensor variance deque window |

### 3.3 Detection — Mode Switching

All four keys are marked `requires_restart=1` by the `_seed_only_keys` override (`mqtt_database.py:386-406`) because they are superseded by `event_config_mode_switching` table at runtime.

| Key | Type | Default | Restart | Live? | Consumer | Description |
|---|---|---|---|---|---|---|
| `startup_threshold` | float | `100.0` | 1 | Yes — `event_detector.py:2046` | `event_detector.py` (seed-only) | Leave break state threshold |
| `break_threshold` | float | `50.0` | 1 | Yes — `event_detector.py:2048` | `event_detector.py` (seed-only) | Enter break threshold |
| `startup_duration_s` | float | `0.1` | 1 | Yes — `event_detector.py:2050` | `event_detector.py` (seed-only) | Above-threshold dwell |
| `break_duration_s` | float | `2.0` | 1 | Yes — `event_detector.py:2052` | `event_detector.py` (seed-only) | Below-threshold dwell |

### 3.4 Detection — Type A

All fields below were downgraded to `requires_restart=1` by the seed-only override because the live source is `event_config_type_a` + per-sensor tables.

| Key | Type | Default | Restart | Live? | Consumer | Description |
|---|---|---|---|---|---|---|
| `type_a_ttl_s` | float | `5.0` | 1 | Indirect (`reload_ttl_config()` in `apply_app_config_live`) | Seed-only | Type A graph marker TTL |
| `type_a_debounce_s` | float | `0.0` | 1 | Indirect | Seed-only | Type A persistence debounce |

### 3.5 Detection — Type B

| Key | Type | Default | Restart | Live? | Consumer | Description |
|---|---|---|---|---|---|---|
| `type_b_t2_s` | float | `2.0` | 1 (seed-only) | Indirect | DB `event_config_type_b` wins | T2 rolling average window |
| `type_b_lower_pct` | float | `40.0` | 1 (seed-only) | — | Seed-only | Lower tolerance % |
| `type_b_upper_pct` | float | `60.0` | 1 (seed-only) | — | Seed-only | Upper tolerance % |
| `type_b_ttl_s` | float | `10.0` | 1 (seed-only) | Indirect | Seed-only | B marker TTL |
| `type_b_debounce_s` | float | `0.0` | 1 (seed-only) | — | Seed-only | B debounce |
| `type_b_init_fill_ratio` | float | `0.9` | 0 | Yes — `event_detector.py:2029` | `event_detector.py:90` | Fraction of T2 needed before first fire |
| `type_b_window_headroom` | float | `1.2` | 1 | No | `event_detector.py:91` | Deque sizing multiplier |

### 3.6 Detection — Type C

| Key | Type | Default | Restart | Live? | Consumer | Description |
|---|---|---|---|---|---|---|
| `type_c_t3_s` | float | `5.0` | 1 (seed-only) | — | Seed-only | T3 averaging window |
| `type_c_lower` | float | `40.0` | 1 (seed-only) | — | Seed-only | Absolute lower |
| `type_c_upper` | float | `60.0` | 1 (seed-only) | — | Seed-only | Absolute upper |
| `type_c_ttl_s` | float | `10.0` | 1 (seed-only) | Indirect | Seed-only | C marker TTL |
| `type_c_debounce_s` | float | `0.0` | 1 (seed-only) | — | Seed-only | C debounce |
| `type_c_init_fill_ratio` | float | `0.9` | 0 | Yes — `event_detector.py:2034` | `event_detector.py:92` | Fraction of T3 needed before first fire |

### 3.7 Detection — Type D

| Key | Type | Default | Restart | Live? | Consumer | Description |
|---|---|---|---|---|---|---|
| `type_d_t4_s` | float | `10.0` | 1 (seed-only) | — | Seed-only | T4 short window |
| `type_d_t5_s` | float | `30.0` | 1 (seed-only) | — | Seed-only | T5 baseline window |
| `type_d_lower_pct` | float | `5.0` | 1 (seed-only) | — | Seed-only | Lower tolerance % |
| `type_d_upper_pct` | float | `5.0` | 1 (seed-only) | — | Seed-only | Upper tolerance % |
| `type_d_ttl_s` | float | `10.0` | 1 (seed-only) | Indirect | Seed-only | D marker TTL |
| `type_d_debounce_s` | float | `0.0` | 1 (seed-only) | — | Seed-only | D debounce |
| `type_d_init_fill_ratio` | float | `0.9` | 0 | Yes — `event_detector.py:2039` | `event_detector.py:93` | Fraction of T4 needed |
| `type_d_1sec_buffer_slots` | int | `1800` | 1 | No | `event_detector.py:94` | 30 min × 60 = 1800 one-sec averages |
| `type_d_per_sec_buffer_secs` | int | `2` | 1 | No | `event_detector.py` (constructor) | Rolling sub-second buffer for D |

### 3.8 Live Streaming

| Key | Type | Default | Restart | Live? | Consumer | Description |
|---|---|---|---|---|---|---|
| `live_buffer_max_samples` | int | `2500` | 1 | Yes — `web_server.py:636` | `web_server.py:593` | Per-device ring buffer depth |
| `live_stream_interval_s` | float | `0.1` | 0 | Yes — `web_server.py:634` | `web_server.py:595` | SSE flush interval |
| `live_wait_timeout_s` | float | `1.0` | 0 | Yes — `web_server.py:638` | `web_server.py:609` | Max wait in `wait_for_data` |
| `devices_cache_ttl_s` | float | `5.0` | 0 | Yes — `web_server.py:652` | `web_server.py:617` | Devices list cache TTL |
| `hardware_sample_rate_hz` | int | `123` | 0 | No | Frontend only (device_detail.html) | Hint for JS max_samples calc |

### 3.9 UI / Frontend

All UI keys are read by the frontend via `/api/system/app-config` (`src/app/routes/system.py:136-160`) and applied in JS. No backend live-apply needed; the page reloads from the API each visit.

| Key | Type | Default | Restart | Live? | Description |
|---|---|---|---|---|---|
| `ui_default_live_window_ms` | int | `1000` | 0 | Frontend | Initial live graph window |
| `ui_rolling_win_secs` | float | `4.0` | 0 | Frontend | Rolling stats window for live table |
| `ui_change_epsilon` | int | `0` | 0 | Frontend | ECharts deadband |
| `ui_max_buffer_points` | int | `2000` | 1 | Frontend | ECharts buffer cap |
| `ui_live_export_max` | int | `200000` | 0 | Frontend | CSV export client buffer |
| `ui_live_table_update_ms` | int | `500` | 0 | Frontend | Live-table re-render interval |
| `ui_zoom_min_ms` | int | `100` | 0 | Frontend | Min zoom window |
| `ui_zoom_max_ms` | int | `30000` | 0 | Frontend | Max zoom window |
| `ui_echarts_large_threshold` | int | `500` | 0 | Frontend | Progressive-render threshold |
| `ui_fetch_timeout_ms` | int | `10000` | 0 | Frontend | fetchWithRetry timeout |
| `ui_fetch_retries` | int | `3` | 0 | Frontend | retry count |
| `ui_event_list_max_fetch` | int | `5000` | 0 | Frontend | Max events fetched per UI call |
| `ui_status_update_interval_ms` | int | `500` | 0 | Frontend | Stats-panel refresh |
| `ui_event_poll_interval_ms` | int | `1000` | 0 | Frontend | Event chart poll |
| `ui_toast_auto_close_ms` | int | `3500` | 0 | Frontend | Toast dwell time |
| `ui_api_event_fetch_timeout_ms` | int | `15000` | 0 | Frontend | Event history fetch timeout |
| `ui_api_live_data_timeout_ms` | int | `5000` | 0 | Frontend | Live-data fetch timeout |
| `ui_api_backoff_base_ms` | int | `1000` | 0 | Frontend | Exponential backoff base |
| `ui_pattern_time_window_ms` | int | `30000` | 0 | Frontend | Pattern visualizer window |

### 3.10 MQTT

| Key | Type | Default | Restart | Live? | Consumer | Description |
|---|---|---|---|---|---|---|
| `mqtt_broker` | str | `localhost` | 0 | Yes — reconnects (`web_server.py:687-710`) | `web_server.py:610` | Broker host |
| `mqtt_port` | int | `1883` | 0 | Yes — reconnects | `web_server.py:611` | Broker port |
| `mqtt_keepalive_s` | int | `60` | 0 | Yes — reconnects | `web_server.py:612` | Keepalive |
| `mqtt_qos` | int | `0` | 0 | Yes — resubscribes | `web_server.py:613` | QoS level |
| `mqtt_consumer_queue_timeout_s` | float | `0.1` | 0 | Yes — `web_server.py:697` | `web_server.py:614` | Internal consumer thread queue timeout |
| `mqtt_base_topic` | str | `stm32/sensors/data` | 0 | Yes — `web_server.py:699` | `web_server.py:615` | Base outbound topic |
| `mqtt_websocket_url` | str | `ws://192.168.1.115:9001/mqtt` | 0 | Yes (via reconnect flag) | `web_server.py:616` | Browser WebSocket URL |

### 3.11 Database

| Key | Type | Default | Restart | Live? | Consumer | Description |
|---|---|---|---|---|---|---|
| `db_cache_size_kb` | int | `64000` | 1 | No | `mqtt_database.py:1185` | PRAGMA cache_size |
| `db_wal_checkpoint_pages` | int | `500` | 1 | No | `mqtt_database.py:1186` | PRAGMA wal_autocheckpoint |
| `db_journal_size_limit_bytes` | int | `4194304` | 1 | No | `mqtt_database.py:1187` | PRAGMA journal_size_limit |
| `db_busy_timeout_ms` | int | `60000` | 1 | No | `mqtt_database.py:1184` | PRAGMA busy_timeout (main) |
| `db_busy_timeout_read_ms` | int | `30000` | 1 | No | (read connections) | PRAGMA busy_timeout (read) |
| `db_recovery_cooldown_s` | float | `30.0` | 0 | Yes — `web_server.py:658` | `mqtt_database.py:55` | Min seconds between recovery retries |
| `db_connection_timeout_s` | float | `60.0` | 1 | No | `mqtt_database.py:64` (via literal) | sqlite3.connect timeout |

### 3.12 Server

| Key | Type | Default | Restart | Live? | Description |
|---|---|---|---|---|---|
| `server_port` | int | `8080` | 1 | No | Gunicorn bind port (read at startup) |
| `server_workers` | int | `1` | 1 | No | Gunicorn workers |
| `server_threads` | int | `8` | 1 | No | Gthread worker threads |
| `server_timeout_s` | int | `120` | 1 | No | Request timeout |

(All four are consumed by `run.sh`/`wsgi.py` only at process start; no runtime reader found — see §4.)

### 3.13 Workers

| Key | Type | Default | Restart | Live? | Consumer | Description |
|---|---|---|---|---|---|---|
| `worker_batch_size` | int | `100` | 1 | Yes — `worker_manager.py:78` | `worker_manager.py:37` | Batch for B/C/D workers |
| `worker_batch_size_a` | int | `100` (corrected from `50`) | 1 | Yes — `worker_manager.py:76` | `worker_manager.py:36` | Type A batch |
| `worker_batch_size_snapshot` | int | `200` | 1 | Yes — `worker_manager.py:80` | `worker_manager.py:38` | Snapshot rows per txn |
| `worker_batch_size_update` | int | `200` | 1 | Yes — `worker_manager.py:82` | `worker_manager.py:39` | Update rows per txn |
| `worker_db_batch_size` | int | `200` | 1 | No | (seed only, unused) | Legacy — identical to `worker_batch_size_update` |
| `worker_queue_timeout_s` | float | `0.5` | 1 | Yes — `worker_manager.py:84` | `worker_manager.py:40` | cv.wait timeout |
| `worker_sleep_on_error_s` | float | `0.05` | 1 | Yes — `worker_manager.py:90` | `worker_manager.py:43` | Sleep on DB error |
| `worker_sleep_idle_snapshot_s` | float | `0.01` | 1 | Yes — `worker_manager.py:86` | `worker_manager.py:41` | Idle sleep, snapshot |
| `worker_sleep_idle_update_s` | float | `0.05` (corrected from `1.0`) | 1 | Yes — `worker_manager.py:88` | `worker_manager.py:42` | Idle sleep, update |
| `worker_batch_flush_interval_s` | float | `0.1` | 1 | Yes — `event_detector.py:2025` | `event_detector.py` | Max time before partial flush |
| `queue_maxlen_a` | int | `2000` | 1 | Yes — `worker_manager.py:94` + detector:2017 | `worker_manager.py:46` | Type A deque length |
| `queue_maxlen_bcd` | int | `500` | 1 | Yes — `worker_manager.py:98` | `worker_manager.py:47-49` | Type B/C/D deque length |
| `queue_snapshot_maxlen` | int | `10000` | 1 | Yes — `worker_manager.py:106` + detector:2019 | `worker_manager.py:60` | Snapshot deque length |

### 3.14 API

| Key | Type | Default | Restart | Live? | Consumer |
|---|---|---|---|---|---|
| `api_event_list_default_limit` | int | `100` | 0 | Implicit (read on each call) | `src/app/routes/events.py:91` |
| `api_live_sensor_max_samples` | int | `1000` | 0 | Implicit | `src/app/routes/sensors.py:70,110` |
| `api_live_sensor_cap` | int | `1500` | 0 | Implicit | `src/app/routes/sensors.py:213` |

### 3.15 Device

| Key | Type | Default | Restart | Live? | Consumer |
|---|---|---|---|---|---|
| `device_active_timeout_s` | float | `0.5` | 0 | Implicit (re-read per stats call) | `web_server.py:1497` |
| `device_poll_interval_s` | float | `1.0` | 0 | No | (legacy Modbus) |
| `device_thread_join_timeout_s` | float | `5.0` | 0 | Yes — `web_server.py:654` | `web_server.py:606` |
| `device_max_count` | int | `20` | 1 | No | (validation bound) |
| `device_max_sensors` | int | `12` | 1 | No | (validation bound) |
| `stm32_device_name` | str | `STM32 MQTT` | 1 | No | `web_server.py:956` (literal — not actually read from DB; see §10) |

### 3.16 Auth / OTP

| Key | Type | Default | Restart | Live? | Consumer |
|---|---|---|---|---|---|
| `otp_expiry_s` | int | `300` | 0 | Yes — `web_server.py:640` | `web_server.py:597` |
| `otp_max_attempts` | int | `5` | 0 | Yes — `web_server.py:642` | `web_server.py:599` |
| `otp_resend_s` | int | `60` | 0 | Yes — `web_server.py:644` | `web_server.py:601` |
| `otp_max_per_hour` | int | `5` | 0 | Yes — `web_server.py:646` | `web_server.py:603` |
| `otp_rate_limit_window_s` | int | `3600` | 0 | Implicit | `src/app/routes/auth.py:45` |

### 3.17 Logging

| Key | Type | Default | Restart | Live? | Consumer |
|---|---|---|---|---|---|
| `log_max_bytes` | int | `5242880` | 0 | Yes — `web_server.py:666` | `src/utils/event_logger.py:54` |
| `log_backup_count` | int | `1` | 0 | Yes — `web_server.py:666` | `src/utils/event_logger.py:55` |
| `auto_restart_check_interval_s` | int | `60` | 0 | Yes — `web_server.py:677` | `src/utils/auto_restart.py:39` |
| `auto_restart_default_hour` | int | `3` | 0 | Yes — `web_server.py:681` | `src/utils/auto_restart.py:41` |
| `auto_restart_default_minute` | int | `0` | 0 | Yes — `web_server.py:683` | `src/utils/auto_restart.py:42` |
| `auto_restart_thread_join_s` | int | `5` | 0 | Yes — `web_server.py:679` | `src/utils/auto_restart.py:40` |

### 3.18 Validation

| Key | Type | Default | Restart | Live? | Consumer |
|---|---|---|---|---|---|
| `type_a_timeframe_min_s` | int | `1` | 0 | Implicit | `src/app/routes/events.py:53,151` |
| `type_a_timeframe_max_s` | int | `60` | 0 | Implicit | `src/app/routes/events.py:54,152` |

## 4. Keys marked `requires_restart=1` — justification

| Key | Real reason |
|---|---|
| `sample_rate_hz` | Buffer sizes, variance window deques, all downstream `int(sec × rate)` multiplications depend on it. Detector *does* live-resize (see §5) but worker queues and per-second D buffers keep their old shape. |
| `total_sensors` | Used to size `sensor_buffers_a/short` dicts keyed 1..12. The live-apply only updates the web_server global; see §5. |
| `buffer_duration_*` | Deque `maxlen` is fixed at detector construction per-sensor. Live resize exists but replaces deque instances — safe at runtime. |
| `event_pre_window_s` / `event_post_window_s` | Combined with rate → `EVENT_WINDOW_SAMPLES`. Live-apply recomputes these (§3.2) so flag is **overly strict**. |
| `type_b_window_headroom` | Passed to `AvgTypeB.__init__` only. No live path. |
| `type_d_1sec_buffer_slots`, `type_d_per_sec_buffer_secs` | Allocated once at detector start. |
| `variance_window_deque_secs` | Allocated once per sensor at detector start. |
| `event_history_maxlen` | Applied to detector `deque(maxlen=…)` at construction. |
| `ui_max_buffer_points` | Frontend reads once at page load. |
| `live_buffer_max_samples` | Flag marks restart but **live handler exists** (`web_server.py:636`). Only affects *new* device entries in `LiveDataHub`; existing buffers keep their old maxlen until recreated. |
| `db_*` tuning keys | Applied inside `_init_database` only. SQLite PRAGMAs can be changed at runtime, but there is no hook that re-issues them. |
| `server_port`, `server_workers`, `server_threads`, `server_timeout_s` | Read by `run.sh`/`wsgi.py` at process start; gunicorn cannot hot-reload these. |
| `worker_*` + `queue_maxlen_*` | Live-apply **does exist** for these — `requires_restart=1` is *wrong* for most of them (see §5). |
| `device_max_count`, `device_max_sensors` | Used as validation bounds; callers import the numeric cap at module import. |
| `stm32_device_name` | `_ensure_stm32_device` runs once at startup. |
| `ref_value`, all `type_*_ttl_s`/`debounce_s`/`*_pct`/bounds | Marked restart by `_seed_only_keys` override — they are advisory/seed defaults; real values live in `event_config_*` tables. |
| `startup_threshold`, `break_threshold`, `startup_duration_s`, `break_duration_s` | Same seed-only rationale, though detectors *do* expose live-apply handlers. |

## 5. Live-apply inconsistencies

These are keys where the `requires_restart` flag contradicts actual runtime behavior:

1. **`total_sensors`** — `requires_restart=1` yet `web_server.py:650-651` updates the `TOTAL_SENSORS` global *live*. However, neither `LiveDataHub` nor `EventDetector.sensor_buffers_a/short` gets resized (they are hard-indexed 1..12 in `event_detector.py:102-110`). Setting this above 12 does nothing; setting it below 12 leaves stale dict entries. **Bug**: flag and live-handler disagree; reality is the detector can't handle it either way.
2. **`ref_value`** — UI knob exists at `app_config.html` and seed-only list forces `requires_restart=1`, but `src/events/avg_type_b.py:192-193` and `src/events/avg_type_d.py:224-226,353-355` all reference the module-level `REF_VALUE = 100.0` literal from `src/events/constants.py:6`. **The value stored in `app_config` is never read.** This is an **actively broken config knob** — editing it silently does nothing.
3. **`event_pre_window_s` / `event_post_window_s`** — Flag says restart; `event_detector.py:2008-2014` handles them live. Flag should be `0`.
4. **Worker-thread keys** (`worker_batch_size*`, `worker_sleep_*`, `worker_queue_timeout_s`, `queue_maxlen_*`, `queue_snapshot_maxlen`) — Flag `requires_restart=1`, but `worker_manager.py:74-109` implements live-apply. Flag should be `0`.
5. **`type_b_init_fill_ratio` / `type_c_init_fill_ratio` / `type_d_init_fill_ratio`** — Flag is `0` and detector live-applies to all 12 per-sensor detectors (`event_detector.py:2029-2043`) — correct.
6. **`data_gap_reset_s`** — Flag `0`, handler live-applies (`event_detector.py:2023`) — correct.
7. **`worker_batch_flush_interval_s`** — Flag `1`, but detector *does* live-apply (`event_detector.py:2025`). Flag is wrong.
8. **`stm32_adc_topic`** — Flag `0` (correct), handler unsubscribes + resubscribes (`web_server.py:703,711-715`).
9. **`mqtt_base_topic`** — Flag `0`; handler updates the global (`web_server.py:699`) but no re-publish of any retained topic is performed. Functionally a variable bind with no re-init. Users changing this at runtime may find older cached topics still active elsewhere.
10. **`stm32_device_name`** — Seed is `STM32 MQTT`. The runtime code at `web_server.py:955-970` uses a *local* `STM32_DEVICE_NAME = "STM32 MQTT"` literal; the `app_config` value is not read by `_ensure_stm32_device`.
11. **`hardware_sample_rate_hz`** — Present as a config key but only used by the frontend template. Not enforced anywhere on the backend.
12. **`server_*` keys** — Declared as app_config but read only from shell args in `run.sh`. Editing from UI has no effect on gunicorn.
13. **Seed-only keys** (mode switching, all type_*_ttl/debounce/threshold) — Carry `requires_restart=1` with a marker appended to their description (`(seed value — use Event Config page to change)`). These are *display* copies of values that actually live in `event_config_*` tables — a split-brain config.

## 6. Consumer map

| Module | app_config keys read |
|---|---|
| `src/detection/event_detector.py` | `sample_rate_hz`, `event_pre_window_s`, `event_post_window_s`, `queue_maxlen_a`, `queue_snapshot_maxlen`, `buffer_duration_a_s`, `buffer_duration_bcd_s`, `variance_window_deque_secs`, `data_gap_reset_s`, `type_b_init_fill_ratio`, `type_b_window_headroom`, `type_c_init_fill_ratio`, `type_d_init_fill_ratio`, `type_d_1sec_buffer_slots` (plus live: `startup_threshold`, `break_threshold`, `startup_duration_s`, `break_duration_s`, `worker_batch_flush_interval_s`). Type B/C/D thresholds come from the dedicated `event_config_*` tables (see constructor lines 162-191). |
| `src/workers/worker_manager.py` | `worker_batch_size_a`, `worker_batch_size`, `worker_batch_size_snapshot`, `worker_batch_size_update`, `worker_queue_timeout_s`, `worker_sleep_idle_snapshot_s`, `worker_sleep_idle_update_s`, `worker_sleep_on_error_s`, `queue_maxlen_a`, `queue_maxlen_bcd`, `queue_snapshot_maxlen`. |
| `web_server.py` | `live_buffer_max_samples`, `live_stream_interval_s`, `otp_expiry_s`, `otp_max_attempts`, `otp_resend_s`, `otp_max_per_hour`, `total_sensors`, `device_thread_join_timeout_s`, `stm32_adc_topic`, `timestamp_drift_threshold_s`, `live_wait_timeout_s`, `mqtt_broker`, `mqtt_port`, `mqtt_keepalive_s`, `mqtt_qos`, `mqtt_consumer_queue_timeout_s`, `mqtt_base_topic`, `mqtt_websocket_url`, `devices_cache_ttl_s`, `device_active_timeout_s`. |
| `src/database/mqtt_database.py` | `db_recovery_cooldown_s`, `db_busy_timeout_ms`, `db_cache_size_kb`, `db_wal_checkpoint_pages`, `db_journal_size_limit_bytes`. |
| `src/utils/event_logger.py` | `log_max_bytes`, `log_backup_count`. |
| `src/utils/auto_restart.py` | `auto_restart_check_interval_s`, `auto_restart_thread_join_s`, `auto_restart_default_hour`, `auto_restart_default_minute`. |
| `src/app/routes/auth.py` | `otp_rate_limit_window_s`. |
| `src/app/routes/sensors.py` | `api_live_sensor_max_samples`, `api_live_sensor_cap`. |
| `src/app/routes/events.py` | `type_a_timeframe_min_s`, `type_a_timeframe_max_s`, `api_event_list_default_limit`. |
| `src/events/avg_type_b.py` / `avg_type_c.py` / `avg_type_d.py` / `event_a.py` | **None directly** — they receive their parameters via the detector constructor. They only reference the literal `REF_VALUE` from `src/events/constants.py`. |

## 7. Config UI mapping

| Template | Scope | Keys edited |
|---|---|---|
| `templates/system_config.html` | Auto-restart schedule only | `system_config` table row (NOT `app_config`): `auto_restart_enabled`, `restart_time_hour`, `restart_time_minute` |
| `templates/app_config.html` | Every `app_config` key, grouped by `category` | All 100+ keys listed in §3 |
| `templates/ttl_config.html` | Event-marker TTLs | `event_config_type_a.ttl_seconds`, `event_config_type_b.ttl_seconds`, `event_config_type_c.ttl_seconds`, `event_config_type_d.ttl_seconds` (dedicated tables, not app_config) |
| `templates/event_config.html` | Live detection configs | `event_config_type_a/b/c/d` + their per-sensor variants, `event_config_mode_switching` |
| `templates/device_config.html` | Device entries | `devices` table (`device_name`, `is_active`, `topic`, `use_per_sensor_config`) |
| `templates/sensor_offsets.html` | Per-sensor calibration | `sensor_offsets` table |

The `app_config` page is the only surface that writes to the `app_config` table; every other page writes to its own dedicated table. This is why the seed-only keys (`ref_value`, `type_*_*`, mode switching) are marked `requires_restart=1` — the UI for editing them is elsewhere.

## 8. DB persistence

**Table**: `app_config` — defined at `src/database/mqtt_database.py:195-207`.

Columns:

| Column | Type | Notes |
|---|---|---|
| `key` | TEXT PRIMARY KEY | Unique identifier |
| `value` | TEXT NOT NULL | Current value (always stored as string) |
| `default_val` | TEXT NOT NULL | Factory default (used by reset-to-defaults) |
| `data_type` | TEXT CHECK IN ('int','float','str','bool') | Advisory metadata — NOT enforced by SQLite |
| `category` | TEXT NOT NULL | Grouping for UI (Acquisition, Detection, MQTT, …) |
| `label` | TEXT NOT NULL | Human-facing label |
| `description` | TEXT NOT NULL DEFAULT '' | Help text for UI tooltip |
| `requires_restart` | INTEGER NOT NULL DEFAULT 0 | 1 = hint to UI that change needs restart |
| `updated_at` | TEXT NOT NULL DEFAULT (datetime('now')) | Timestamp |

Index: `idx_app_config_category ON app_config(category)` for UI grouping.

Key characteristics:
- **Values are always TEXT.** Consumers cast downstream (`int(db.get_app_config_value(...))`).
- **Type validation is advisory only.** The `data_type` column is metadata; nothing in SQLite enforces "int" stays int. Validation happens at the API layer in `src/app/routes/system.py:217-231` (`int(str(raw_val))`, `float(str(raw_val))`) — values are rejected before they persist.
- **Seed is idempotent.** `INSERT OR IGNORE` preserves user-edited rows across schema revisions.
- **Reset-to-default** works by copying `default_val` → `value` for the requested keys (`reset_app_config_to_defaults`). Two corrections in `_default_corrections` (`mqtt_database.py:411-421`) retroactively rewrite legacy seeded defaults (`worker_batch_size_a: 50 → 100`, `worker_sleep_idle_update_s: 1.0 → 0.05`) only if the user had not already changed them.
- **Seed-only downgrade** (`mqtt_database.py:386-406`): for ~20 keys whose runtime source is a dedicated `event_config_*` table, the seeder forces `requires_restart=1` and appends `(seed value — use Event Config page to change)` to the description.

## 9. mqtt_config DB (separate file)

**File:** `src/database/mqtt_database.db` — distinct from the main data DB at `/mnt/ssd/mqtt_database/mqtt_database.db`. Class: `MQTTConfigDB` at `src/database/mqtt_config.py:29`.

Schema (single row, `id=1`):

| Column | Default | Purpose |
|---|---|---|
| `broker` | `'localhost'` | Broker host |
| `port` | `1883` | Broker port |
| `base_topic` | `'canbus/sensors/data'` | Publish base topic |
| `websocket_url` | `'ws://localhost:9001/mqtt'` | Browser WS URL |
| `username` | NULL | Auth username |
| `password` | NULL | Auth password |
| `enabled` | `1` | Master MQTT toggle |
| `updated_at` | (required) | Last save |

**No TLS columns** (`enable_tls`, `tls_ca_cert`, `tls_client_cert`, `tls_client_key`) exist despite being listed in the spec. TLS is not configurable through this DB.

**Second identical copy**: `/home/embed/hammer/mqtt_config_db.py` (legacy root-level module) defines the same `MQTTConfigDB` class and also creates `mqtt_database.db` next to it. Two module-level singletons exist — the one imported by live code is `src/database/mqtt_config.py::mqtt_db`.

**Drift risk:** `app_config` stores `mqtt_broker`, `mqtt_port`, `mqtt_base_topic`, `mqtt_websocket_url` as well. These two stores are NOT synchronised — `web_server.py:610-616` reads from `app_config`, so `MQTTConfigDB` appears to be dead code unless something imports `mqtt_db.get_config()`.

## 10. Hardcoded constants (not configurable despite UI implying otherwise)

| Constant | Location | Why it matters |
|---|---|---|
| `REF_VALUE = 100.0` | `src/events/constants.py:6` | Used by `avg_type_b.py:192-193` and `avg_type_d.py:224-226,353-355`. The `ref_value` row in `app_config` is never read — editing it silently does nothing. |
| `POST_WINDOW_SECONDS = 9.0` (class default) | `src/detection/event_detector.py:44` | Live-overridable via `event_post_window_s`, but older references such as the 18-s default event window are documented as `9s pre + 9s post` assuming this value. |
| `STM32_DEVICE_ID = 1` | `web_server.py:955`, `src/app/services.py:52` | Device ID 1 is *always* the STM32; no config. |
| `STM32_DEVICE_NAME = "STM32 MQTT"` | `web_server.py:956` | `_ensure_stm32_device` uses this literal. The `stm32_device_name` `app_config` row is not consulted. |
| `STM32_ADC_TOPIC = "stm32/adc"` | `web_server.py:48` (initial) | The variable is then overwritten by `_db_cfg('stm32_adc_topic', …)` at line 607, so this one IS live. Flagged only because a bare literal exists above the override. |
| `500 ms` lock-contention note | Comment at `event_detector.py:1566` | Refers to observed hold time, not a knob. |
| `BATCH_FLUSH_INTERVAL` | `event_detector.py` class-level default | Overridable live via `worker_batch_flush_interval_s`, but the class still carries a hard default. |
| `ROLLING_WIN_SECS = 4.0` | `templates/device_detail.html` (frontend) | Duplicated as `ui_rolling_win_secs` in `app_config`; the backend value is the source of truth for new page loads, but hot JS may still use the old constant if not re-fetched. |
| `CHANGE_EPSILON = 0` | `templates/device_detail.html` | Mirrored as `ui_change_epsilon`. Same caveat. |
| Local `DEFAULT_DB_PATH = "/mnt/ssd/mqtt_database/mqtt_database.db"` | `src/database/mqtt_database.py:13` | Env override is `MQTT_DATABASE_PATH`, not `app_config`. |
| `FALLBACK_DB_PATH` | `src/database/mqtt_database.py:14` | Computed as sibling file of `mqtt_database.py`; not exposed. |
| SMTP port fallback `587` | `web_server.py:1037` | Literal inside `int(os.getenv("SMTP_PORT", "587"))`; no DB fallback. |
| `mqtt_config.py` singleton `mqtt_db` base_topic default `'canbus/sensors/data'` | `src/database/mqtt_config.py:23,70` | Inherits stale CAN-era default distinct from `app_config`'s `mqtt_base_topic = 'stm32/sensors/data'`. |

### Summary of critical findings

- **`ref_value` is the biggest footgun**: every tolerance-based Event B/D detection uses the literal `100.0`, not the persisted config. Fixing requires patching `src/events/avg_type_b.py`, `src/events/avg_type_d.py`, and constructor-wiring through `event_detector.py`.
- **Seed-only duplicates** (`type_*_*`, mode switching) create a double-edit surface: users change them on `app_config.html`, observe no effect, find the real controls on `event_config.html`. A better UX would be to hide the seed-only group from the `app_config` page.
- **Worker-related `requires_restart` flags are almost all wrong** — the live handlers exist (`worker_manager.py:74-109`) but the UI insists on a restart.
- **`total_sensors` is a two-way lie**: the flag says restart, the web server applies it live, but the detector can't actually change its per-sensor dict shape (hard-coded `range(1, 13)`). The knob is non-functional in either direction.
- **`stm32_device_name` is a three-way lie**: listed in `app_config`, accepted by the API, displayed in the UI, but `_ensure_stm32_device` at `web_server.py:956-970` uses a module-level literal.
- **`MQTTConfigDB`** appears to be effectively dead code — the live MQTT client path reads from `app_config` exclusively. Consolidate or remove.
- **Two `services.py` files** (`/home/embed/hammer/services.py` and `/home/embed/hammer/src/app/services.py`) both declare their own env-var reads for `LIVE_BUFFER_MAX_SAMPLES`, `LIVE_STREAM_INTERVAL_S`, `SENSOR_LOG_ENABLED`, `LATENCY_LOG_*` with slightly different defaults (root uses `2000`, src uses `2500`). Consumers only import one, but the drift is a maintenance hazard.
