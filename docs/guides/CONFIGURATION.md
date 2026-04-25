# CONFIGURATION.md — every knob, end-to-end

> **Audience:** anyone deploying HERMES, debugging "why is it doing X?",
> or planning a new tunable. Catalogs every environment variable + every
> DB-backed setting + every operator-editable value, with where to set
> it and what it affects.
>
> **Companion docs:**
> - [`../design/DATABASE_SCHEMA.md`](../design/DATABASE_SCHEMA.md) — the `parameters` table that holds DB-backed settings
> - [`EVENTS.md`](./EVENTS.md) — what each detector threshold means
> - [`../design/REST_API.md`](../design/REST_API.md) — endpoints that mutate config
> - [`BACKEND.md`](./BACKEND.md) §1.2 — the `Settings` class

---

## Table of contents

1. [Two layers of configuration](#1-two-layers-of-configuration)
2. [Environment variables (boot-time)](#2-environment-variables-boot-time)
3. [DB-backed settings (runtime)](#3-db-backed-settings-runtime)
4. [Where to set what](#4-where-to-set-what)
5. [.env file template](#5-env-file-template)
6. [Adding a new environment variable](#6-adding-a-new-environment-variable)
7. [Adding a new DB-backed parameter](#7-adding-a-new-db-backed-parameter)

---

## 1. Two layers of configuration

```
┌─────────────────────────────────────────────────────────────┐
│  Layer A: environment variables                             │
│  ─────────────────────────────────────                      │
│  Read once at process start by Settings (pydantic-settings) │
│  Changes require a service restart                          │
│  Set via /etc/hermes/*.env (production) or .env (dev)       │
│  Examples: DATABASE_URL, HERMES_JWT_SECRET, MQTT_HOST       │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ used by
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  HERMES processes — read at start, cached for life          │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ + reads continually from
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer B: DB-backed settings                                │
│  ─────────────────────────────────                          │
│  Stored in `parameters` table, scoped by package            │
│  Changes propagate via pg_notify('hermes_config_changed')   │
│  Edit live via /api/config + UI                             │
│  Examples: detector thresholds, mode-switching parameters    │
└─────────────────────────────────────────────────────────────┘
```

Layer A is set by **ops** (env files, systemd units). Layer B is set
by **operators** (the `/config` page, REST API). Knowing which is
which matters because Layer A changes need a restart and Layer B
changes don't.

---

## 2. Environment variables (boot-time)

Read by `services/hermes/config.py:Settings`. All variables are
case-insensitive (pydantic-settings normalises) — write them as upper-
case in your env files for readability.

### 2.1 Required (process won't start without them)

| Variable | Type | Used by | Notes |
|----------|------|---------|-------|
| `DATABASE_URL` | string | Both API + ingest | asyncpg URL: `postgresql+asyncpg://hermes_app:<pw>@<host>:5432/hermes`. Application role with read+write on data tables but no DDL |
| `MIGRATE_DATABASE_URL` | string | `db-migrate.sh`, `DbConfigProvider.start_listener` | psycopg URL: `postgresql://hermes_migrate:<pw>@<host>:5432/hermes`. Separate role with DDL privileges. Also used for the LISTEN connection because asyncpg parses the libpq form |
| `HERMES_JWT_SECRET` | secret string | `auth.jwt`, `auth.secret_box` | 32+ bytes. Rotating invalidates every JWT and every Fernet-encrypted broker password (re-entry needed) |

### 2.2 API server

| Variable | Default | Notes |
|----------|---------|-------|
| `HERMES_API_HOST` | `0.0.0.0` | Bind address. Set to `127.0.0.1` if nginx is the only client |
| `HERMES_API_PORT` | `8080` | Bind port |
| `HERMES_API_WORKERS` | `1` | uvicorn worker count. Multiple workers don't share `LiveDataHub` memory — keep at 1 unless behind a sticky load balancer |
| `HERMES_API_LOG_LEVEL` | `info` | `debug` / `info` / `warning` / `error` |
| `HERMES_JWT_EXPIRY_SECONDS` | `3600` | JWT lifetime. After expiry the user is bounced to `/login` |
| `HERMES_DEV_MODE` | `false` | `true` enables the auth bypass (mints a stub user) and disables some prod-only safety checks. **Never set in production** |

### 2.3 MQTT

| Variable | Default | Notes |
|----------|---------|-------|
| `MQTT_HOST` | `localhost` | Broker hostname/IP |
| `MQTT_PORT` | `1883` | Broker port |
| `MQTT_USERNAME` | `""` | Empty = anonymous connect |
| `MQTT_PASSWORD` | `""` | SecretStr |
| `MQTT_TOPIC_ADC` | `stm32/adc` | Inbound STM32 topic. Don't change without updating firmware |
| `MQTT_TOPIC_EVENTS_PREFIX` | `stm32/events` | Outbound prefix; full topic is `<prefix>/<dev>/<sid>/<TYPE>` |

> **Note:** the `mqtt_brokers` table (gap 4, alpha.18) lets operators
> register additional broker entries in the UI, but the active row
> doesn't yet override these env vars at runtime — operators must
> still restart `hermes-ingest` after activating a different broker.

### 2.4 Detection / ingest

| Variable | Default | Notes |
|----------|---------|-------|
| `EVENT_TTL_SECONDS` | `5.0` | TTL gate dedup window. Within this window same-type events merge, lower-priority types are blocked, BREAK bypasses |
| `LIVE_BUFFER_MAX_SAMPLES` | `2000` | Per-device `LiveDataHub` ring buffer depth. At 100 Hz this is ~20 s of history available to SSE |
| `MQTT_DRIFT_THRESHOLD_S` | `5.0` | `ClockRegistry` re-anchors STM32 wall time when computed drift exceeds this |

### 2.5 Multi-shard (Layer 3, alpha.15)

| Variable | Default | Notes |
|----------|---------|-------|
| `HERMES_INGEST_MODE` | `all` | `all` (single-process), `shard` (one of N detection processes), or `live_only` (API process keeping live ring buffer warm) |
| `HERMES_SHARD_COUNT` | `1` | Number of detection shards. Must be > 1 when mode = `shard` |
| `HERMES_SHARD_INDEX` | `0` | This process's shard index. Must satisfy `0 ≤ index < shard_count` |

A `model_validator` enforces these — mis-configured shard math fails
fast at process start. See
[`../design/MULTI_SHARD.md`](../design/MULTI_SHARD.md) §7 for the
deployment runbook.

### 2.6 OTP / email

| Variable | Default | Notes |
|----------|---------|-------|
| `SMTP_HOST` | `smtp.gmail.com` | OTP delivery server |
| `SMTP_PORT` | `587` | STARTTLS submission port |
| `SMTP_USER` | `""` | If empty, OTP is logged instead of sent — useful for dev |
| `SMTP_PASS` | `""` | SecretStr |
| `SMTP_FROM` | `""` | "From" address. Falls back to `SMTP_USER` if blank |
| `OTP_EXPIRY_SECONDS` | `300` | OTP lifetime |
| `OTP_MAX_ATTEMPTS` | `5` | Max failed verifies before the OTP is locked |
| `OTP_RESEND_COOLDOWN_SECONDS` | `60` | Per-user minimum gap between OTP requests |
| `OTP_MAX_PER_HOUR` | `5` | Per-user sliding hour rate limit |
| `ALLOWED_EMAILS_PATH` | `./config/allowed_emails.txt` | Operator allowlist; one email per line |

### 2.7 Observability

| Variable | Default | Notes |
|----------|---------|-------|
| `HERMES_LOG_FORMAT` | `json` | `json` for log aggregators; `console` for human-readable dev |
| `HERMES_METRICS_ENABLED` | `true` | Toggle `/api/metrics` (it's still served if `false`, just stops updating) |
| `HERMES_METRICS_PORT` | `9090` | Reserved for a future dedicated metrics listener |

---

## 3. DB-backed settings (runtime)

Stored in the `parameters` table, scoped by package. Edited via
`/api/config` (which calls `_commit_and_reload` and emits
`pg_notify('hermes_config_changed', package_id)`).

Each row is `(package_id, scope, device_id?, sensor_id?, key, value)`.
Resolution order on read: SENSOR > DEVICE > GLOBAL.

### 3.1 Detector configs

Each row's `value` is a JSONB blob mirroring a Pydantic dataclass.
Keys currently in use:

| `key` value | Pydantic shape | Default | Doc |
|-------------|----------------|---------|-----|
| `type_a.config` | `TypeAConfig` | all-disabled | [`EVENTS.md`](./EVENTS.md) §3 |
| `type_b.config` | `TypeBConfig` | all-disabled | [`EVENTS.md`](./EVENTS.md) §4 |
| `type_c.config` | `TypeCConfig` | all-disabled | [`EVENTS.md`](./EVENTS.md) §5 |
| `type_d.config` | `TypeDConfig` | all-disabled | [`EVENTS.md`](./EVENTS.md) §6 |
| `mode_switching.config` | `ModeSwitchingConfig` | all-disabled | [`EVENTS.md`](./EVENTS.md) §7 |

Adding a new detector type means registering its key in
`services/hermes/detection/db_config.py:KEY_TO_CLS` AND the
corresponding Pydantic class in `detection/config.py`.

### 3.2 Scope resolution

```
                  request: get config for (device=3, sensor=5)
                                │
                                ▼
                  ┌─────────────────────────────────────┐
                  │ parameters_lookup index              │
                  │  (package_id, scope, device_id,     │
                  │   sensor_id)                        │
                  └─────────────────────────────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            ▼                                       ▼
   key=type_a.config      key=type_a.config     key=type_a.config
   scope=sensor           scope=device          scope=global
   device_id=3            device_id=3            (no device or
   sensor_id=5            sensor_id IS NULL       sensor)
   ↓ FIRST WINS           ↓ used if SENSOR        ↓ used if both
                            row missing             above missing
                                                   (default: all-disabled)
```

**Important consequence:** resetting a sensor-scope override doesn't
"reset to default" — it falls back to the device-scope, then global,
then dataclass defaults. The UI's `Clear` button on a sensor-scope
override deletes the row, which is what the operator usually wants.

### 3.3 Active session + recording flag

Two related runtime settings on the `sessions` table (NOT in `parameters`):

| Column | Where set | Effect |
|--------|-----------|--------|
| `record_raw_samples` | POST `/api/sessions` (set on start) | While this session is active, `SessionSampleWriter` writes every sensor reading to `session_samples`. Per-session, not editable mid-flight |
| `ended_at` | POST `/api/sessions/{id}/stop` | Closes the session; cascades to LOCAL children; locks the package |

### 3.4 MQTT broker registry

`mqtt_brokers` table (gap 4, alpha.18). Editable via `/api/mqtt-brokers`.

Per-row settings: `host`, `port`, `username`, `password_enc` (Fernet-
encrypted), `use_tls`, `is_active`. The partial unique index enforces
at most one active broker.

> Active-row swap doesn't yet drive a runtime broker reconnect — the
> ingest reads broker config from env vars at process start. Tracked
> follow-up to wire this end-to-end.

### 3.5 Per-sensor calibration

`sensor_offsets` table. Editable via `/api/devices/{id}/offsets`.

`engineering_value = raw_value − offset_value`. Loaded into
`OffsetCache` at ingest startup; in-memory state must be refreshed
after a change (currently via service restart; the API endpoint will
trigger a NOTIFY-based reload in a follow-up).

---

## 4. Where to set what

Quick lookup — "I want to change X, where do I go?"

| What | Where | Effect | Restart? |
|------|-------|--------|----------|
| Database connection | `/etc/hermes/secrets.env` (`DATABASE_URL`) | All processes | Yes — both `hermes-api` + `hermes-ingest` |
| JWT secret | `/etc/hermes/secrets.env` (`HERMES_JWT_SECRET`) | All sessions invalidated; broker passwords need re-entry | Yes |
| MQTT broker host (env) | `/etc/hermes/ingest.env` (`MQTT_HOST`) | Ingest only | Yes — `systemctl restart hermes-ingest` |
| MQTT broker (UI) | `/mqtt-brokers` page | Stored in DB; **not yet applied to running ingest** | Yes (today) |
| TTL gate window | `/etc/hermes/ingest.env` (`EVENT_TTL_SECONDS`) | Ingest detection chain | Yes |
| Detection thresholds (Type A/B/C/D) | `/config` page | Live — provider reloads + engine resets | No |
| Mode switching parameters | `/config` page (Mode tab) | Live | No |
| Sensor calibration offsets | `/devices/{id}` (planned UI; today: `PUT /api/devices/{id}/offsets`) | Loaded at ingest start; runtime reload pending | Yes (today) |
| Number of shards | `/etc/hermes/ingest.env` (`HERMES_SHARD_COUNT`) + systemd template instances | All shards | Yes — coordinated restart |
| Live buffer depth | `/etc/hermes/api.env` (`LIVE_BUFFER_MAX_SAMPLES`) | API process only | Yes — `systemctl restart hermes-api` |
| OTP rate limits | `/etc/hermes/api.env` (`OTP_*`) | API process | Yes |
| Email allowlist | File at `ALLOWED_EMAILS_PATH` | Re-read on every login attempt | No |
| Modbus device config | `PUT /api/devices/{id}` (with `modbus_config`) | Picked up by `ModbusManager` on next refresh (5 s) | No |
| Active session + recording flag | `/sessions` page | Live | No |

---

## 5. .env file template

For a fresh dev install, copy `.env.example` to `.env` and fill in:

```bash
# Database (required)
DATABASE_URL=postgresql+asyncpg://hermes_app:devpw@localhost:5432/hermes
MIGRATE_DATABASE_URL=postgresql://hermes_migrate:devpw@localhost:5432/hermes

# Auth (required, 32+ bytes)
HERMES_JWT_SECRET=replace-this-with-a-long-random-string-32-plus-bytes

# Dev mode — bypass OTP gate; do NOT set in production
HERMES_DEV_MODE=1

# Logging — console for humans, json for prod
HERMES_LOG_FORMAT=console

# MQTT (defaults work with `docker compose -f docker-compose.dev.yml up -d`)
MQTT_HOST=localhost
MQTT_PORT=1883

# OTP — leave SMTP_USER blank for dev (OTP is logged to console instead)
SMTP_USER=
SMTP_PASS=
```

For production (`/etc/hermes/secrets.env` + `/etc/hermes/api.env` +
`/etc/hermes/ingest.env`), split secrets from non-secrets:

```bash
# /etc/hermes/secrets.env  (chmod 0640, owned by root:hermes)
DATABASE_URL=postgresql+asyncpg://hermes_app:...@/hermes
MIGRATE_DATABASE_URL=postgresql://hermes_migrate:...@/hermes
HERMES_JWT_SECRET=<48-random-bytes-base64>
SMTP_PASS=<smtp-app-password>

# /etc/hermes/api.env  (world-readable OK)
HERMES_API_PORT=8080
HERMES_LOG_FORMAT=json
HERMES_INGEST_MODE=all
HERMES_JWT_EXPIRY_SECONDS=3600
SMTP_HOST=smtp.example.com
SMTP_USER=hermes-otp@example.com
SMTP_FROM=hermes@example.com
ALLOWED_EMAILS_PATH=/etc/hermes/allowed_emails.txt

# /etc/hermes/ingest.env  (world-readable OK)
MQTT_HOST=localhost
MQTT_TOPIC_ADC=stm32/adc
MQTT_TOPIC_EVENTS_PREFIX=stm32/events
EVENT_TTL_SECONDS=5.0
LIVE_BUFFER_MAX_SAMPLES=2000
MQTT_DRIFT_THRESHOLD_S=5.0
HERMES_INGEST_MODE=all
```

The systemd units (`packaging/systemd/*.service`) include an
`EnvironmentFile=` directive for each so the process inherits the
right vars without exposing secrets in `systemctl show` output.

---

## 6. Adding a new environment variable

1. Add the field to `services/hermes/config.py:Settings` with a
   sensible default and a `description=` arg.
2. Use it in code via `get_settings().<field>`.
3. Add a row to §2 above with default + meaning.
4. Add a row to the system-tunables endpoint
   (`services/hermes/api/routes/system_tunables.py:_build_tunables`)
   so operators see it on the `/settings` page.
5. Document in `.env.example` if it's required for fresh installs.
6. If it affects the production deployment, update the relevant
   `/etc/hermes/*.env` example in §5 above.

A `model_validator` is a good safety net for cross-field invariants
(see `_validate_shard_config` for an example).

---

## 7. Adding a new DB-backed parameter

For a new threshold / runtime knob editable via UI:

1. Define a Pydantic dataclass in
   `services/hermes/detection/config.py` (e.g. `class TypeEConfig(BaseModel)`).
2. Add it to the `DetectorConfigProvider` Protocol with a
   `type_e_for(device_id, sensor_id) -> TypeEConfig` method.
3. Implement on `StaticConfigProvider` and `DbConfigProvider`.
4. Register a key constant in `db_config.py:KEY_TO_CLS`:
   `KEY_TYPE_E = "type_e.config"` and add the entry to the `KEY_TO_CLS`
   dict.
5. Extend `_ConfigCache` with a `type_e: TypeEConfig` field.
6. Update `_build_cache()` to decode it.
7. Add `type_e` arms to the `/api/config/...` route in
   `services/hermes/api/routes/config.py`.
8. Add a tab to the `/config` SvelteKit page.
9. Document the threshold meaning in [`EVENTS.md`](./EVENTS.md).
10. Add an integration test that round-trips the threshold via the
    API and reads it back from `DbConfigProvider`.

The cross-shard NOTIFY plumbing is already wired — your new key
participates in the `hermes_config_changed` channel automatically
because it goes through `_commit_and_reload`.
