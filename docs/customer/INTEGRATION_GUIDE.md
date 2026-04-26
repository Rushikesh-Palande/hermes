# HERMES — Customer Integration Guide

**Version**: 0.1.0-alpha.29 · **Audience**: External developers integrating with the HERMES backend API

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Getting Started](#3-getting-started)
4. [Authentication](#4-authentication)
5. [REST API Reference](#5-rest-api-reference)
   - [Health](#51-health)
   - [Authentication endpoints](#52-authentication-endpoints)
   - [Devices](#53-devices)
   - [Events](#54-events)
   - [Detector Configuration](#55-detector-configuration)
   - [Sessions](#56-sessions)
   - [Packages](#57-packages)
   - [MQTT Brokers](#58-mqtt-brokers)
   - [Sensor Offsets](#59-sensor-offsets)
   - [Live Stream (SSE)](#510-live-stream-sse)
   - [System Tunables](#511-system-tunables)
   - [Prometheus Metrics](#512-prometheus-metrics)
6. [Database Schema](#6-database-schema)
7. [Event Detection System](#7-event-detection-system)
8. [MQTT Integration](#8-mqtt-integration)
9. [Configuration Reference](#9-configuration-reference)
10. [Prometheus Metrics Reference](#10-prometheus-metrics-reference)
11. [Error Reference](#11-error-reference)
12. [Quick-Start Code Examples](#12-quick-start-code-examples)

---

## 1. System Overview

HERMES is an industrial sensor monitoring platform that:

- **Ingests** 12-channel ADC telemetry from STM32 hardware over MQTT at ~100 Hz per sensor (~2 000 msg/s on a 20-device deployment)
- **Detects** anomalies in real time using four parallel algorithms (A: variance, B: tolerance band, C: absolute bound, D: two-stage drift) plus a BREAK state for sensor disconnect
- **Persists** every detected event with a ±9-second sample window to TimescaleDB (PostgreSQL extension)
- **Re-publishes** detected events back over MQTT for downstream PLC / SCADA consumers
- **Exposes** the full dataset — devices, events, sessions, configuration — via a REST API on port 8080

The `hermes-backend` package ships **only the API and ingest services**. You bring your own frontend.

---

## 2. Architecture

```
STM32 devices
     │  MQTT (stm32/adc)
     ▼
┌──────────────┐     raw samples      ┌──────────────────────────────────┐
│  Mosquitto   │ ───────────────────► │     hermes-ingest (Python)       │
│  MQTT broker │                      │                                  │
└──────────────┘                      │  per-sensor pipeline:            │
                                      │    offset correction             │
                                      │    sliding window                │
                                      │    Type A / B / C / D detectors  │
                                      │    BREAK state machine           │
                                      │                                  │
                                      │  outputs:                        │
     MQTT (stm32/events/*)  ◄─────── │    events → TimescaleDB          │
                                      │    events → MQTT re-publish      │
                                      └──────────────────────────────────┘
                                               │  async SQLAlchemy
                                               ▼
                                      ┌────────────────────┐
                                      │   TimescaleDB      │
                                      │   (PostgreSQL 16)  │
                                      └────────────────────┘
                                               │
                                               ▼
                                      ┌────────────────────┐
                                      │  hermes-api        │
                                      │  FastAPI : 8080    │
                                      └────────────────────┘
                                               │  REST / SSE
                                               ▼
                                         YOUR FRONTEND
```

### Key processes

| Process | systemd unit | Port | Description |
|---------|-------------|------|-------------|
| `hermes-api` | `hermes-api.service` | 8080 | FastAPI REST API + SSE |
| `hermes-ingest` | `hermes-ingest.service` | — | MQTT consumer + detector pipeline |

---

## 3. Getting Started

### Install

```bash
# From the .deb (recommended)
sudo dpkg -i hermes-backend_<version>_all.deb
sudo apt install -f          # resolve declared Depends

# Or from source
sudo ./packaging/install.sh --skip-ui --operator-email you@your-org.com
```

After install:
- API is live at `http://<host>:8080`
- Swagger UI at `http://<host>:8080/docs`
- ReDoc at `http://<host>:8080/redoc`

### Verify the API is up

```bash
curl http://localhost:8080/api/health
# → {"status":"ok","version":"0.1.0-alpha.29"}

curl http://localhost:8080/api/health/ready
# → {"status":"ready"} (503 if DB unreachable)
```

### First login (get a JWT)

```bash
# Step 1 — request a one-time password emailed to the operator address
curl -X POST http://localhost:8080/api/auth/otp/request \
     -H "Content-Type: application/json" \
     -d '{"email":"you@your-org.com"}'
# → 204 No Content

# Step 2 — exchange the OTP for a JWT
curl -X POST http://localhost:8080/api/auth/otp/verify \
     -H "Content-Type: application/json" \
     -d '{"email":"you@your-org.com","otp":"123456"}'
# → {"access_token":"eyJ...","token_type":"bearer","expires_in":3600}

TOKEN="eyJ..."

# Step 3 — use the token
curl http://localhost:8080/api/devices \
     -H "Authorization: Bearer $TOKEN"
```

---

## 4. Authentication

### Flow

HERMES uses **email OTP + JWT bearer tokens**.

```
Client                              HERMES API
  │                                      │
  │  POST /api/auth/otp/request          │
  │  {"email":"you@company.com"}         │
  │ ─────────────────────────────────►  │
  │                                      │  generates 6-digit code
  │                                      │  hashes with argon2id
  │  204 No Content                      │  emails plaintext code
  │ ◄─────────────────────────────────  │
  │                                      │
  │  POST /api/auth/otp/verify           │
  │  {"email":"...","otp":"123456"}      │
  │ ─────────────────────────────────►  │
  │                                      │  verifies hash
  │  {"access_token":"eyJ..."}           │  issues HS256 JWT
  │ ◄─────────────────────────────────  │
  │                                      │
  │  GET /api/events                     │
  │  Authorization: Bearer eyJ...        │
  │ ─────────────────────────────────►  │
  │                                      │  validates JWT
  │  [{"event_id":1,...}]                │  loads user from DB
  │ ◄─────────────────────────────────  │
```

### JWT details

| Field | Value |
|-------|-------|
| Algorithm | HS256 |
| Default TTL | 3600 s (1 hour) |
| Header | `Authorization: Bearer <token>` |
| Claim `sub` | User UUID (string) |

The token is **stateless** — there is no server-side revocation. Tokens expire automatically after `expires_in` seconds.

### OTP rate limits

| Limit | Default |
|-------|---------|
| OTP TTL | 300 s |
| Max wrong attempts before lockout | 5 |
| Resend cooldown per email | 60 s |
| Max OTP requests per hour | 5 |

### Email allowlist

Only emails in `/etc/hermes/allowed_emails.txt` can receive OTPs. Add your integration user's address there:

```bash
echo "integration@your-org.com" | sudo tee -a /etc/hermes/allowed_emails.txt
```

### Development bypass

If `HERMES_DEV_MODE=1` is set **and** no `Authorization` header is sent, the API returns a synthetic admin user without verifying a token. **Never use in production.**

---

## 5. REST API Reference

Base URL: `http://<host>:8080`

All authenticated endpoints require `Authorization: Bearer <token>`.

All request/response bodies are `application/json` unless noted.

---

### 5.1 Health

#### `GET /api/health`

Liveness check. Always returns 200 if the process is running.

**No auth required.**

**Response 200**
```json
{"status": "ok", "version": "0.1.0-alpha.29"}
```

---

#### `GET /api/health/ready`

Readiness check. Verifies DB connectivity.

**No auth required.**

**Response 200** — DB reachable
```json
{"status": "ready"}
```

**Response 503** — DB unreachable
```json
{"detail": "database unavailable"}
```

---

### 5.2 Authentication Endpoints

#### `POST /api/auth/otp/request`

Generate and email a one-time password. Always returns 204 regardless of whether the email is on the allowlist (prevents enumeration).

**No auth required.**

**Request body**
```json
{"email": "operator@your-org.com"}
```

**Response 204** — OTP sent (or silently dropped if not allowlisted)

**Response 429** — Rate limit exceeded

---

#### `POST /api/auth/otp/verify`

Verify a 6-digit OTP and issue a JWT.

**No auth required.**

**Request body**
```json
{"email": "operator@your-org.com", "otp": "123456"}
```

**Response 200**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

**Response 401** — Invalid or expired OTP

---

#### `POST /api/auth/logout`

Client-side logout hint. Returns 204; there is no server-side token revocation (JWT expiry handles it). Call this to signal the client should discard its token.

**Auth required.**

**Response 204**

---

### 5.3 Devices

A **device** represents one physical STM32 sensor unit. `device_id` is a 1–999 integer chosen by the operator and matches the ID embedded in MQTT payloads.

#### `GET /api/devices`

List all devices, sorted by `device_id` ascending.

**Auth required.**

**Response 200**
```json
[
  {
    "device_id": 1,
    "name": "Pump A",
    "protocol": "mqtt",
    "topic": null,
    "is_active": true,
    "created_at": "2026-01-15T10:00:00Z",
    "updated_at": "2026-01-15T10:00:00Z"
  }
]
```

---

#### `POST /api/devices`

Create a new device.

**Auth required.**

**Request body**
```json
{
  "device_id": 1,
  "name": "Pump A",
  "protocol": "mqtt",
  "topic": null
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `device_id` | int | ✓ | 1–999, unique |
| `name` | str | ✓ | 1–120 chars |
| `protocol` | enum | ✓ | `mqtt` or `modbus_tcp` |
| `topic` | str\|null | — | Custom MQTT topic; null uses default |

**Response 201** — created device object

**Response 409** — `device_id` already exists

---

#### `GET /api/devices/{device_id}`

Get a single device.

**Response 200** — device object · **Response 404** — not found

---

#### `PATCH /api/devices/{device_id}`

Partial update. Only provided fields are changed.

**Request body** (all fields optional)
```json
{"name": "Pump A — Line 2", "is_active": true, "topic": "plant/sensors/1"}
```

**Response 200** — updated device object

---

#### `DELETE /api/devices/{device_id}`

Hard delete. Cascades to `sensor_offsets`. Events referencing the device are NOT deleted (historical record).

**Response 204** · **Response 404**

---

### 5.4 Events

An **event** is a timestamped anomaly detected by one of the four algorithms or the BREAK state machine. Every event stores the exact sensor value that triggered it and links to a ±9-second raw sample window.

#### `GET /api/events`

Paginated, filtered list of events.

**Auth required.**

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `device_id` | int | — | Filter by device |
| `sensor_id` | int | — | Filter by sensor (1–12) |
| `event_type` | str | — | `A`, `B`, `C`, `D`, or `BREAK` |
| `after` | ISO 8601 datetime | — | `triggered_at > after` |
| `before` | ISO 8601 datetime | — | `triggered_at < before` |
| `limit` | int | 100 | 1–500 |
| `offset` | int | 0 | Pagination offset |

**Response 200**
```json
[
  {
    "event_id": 42,
    "triggered_at": "2026-04-10T14:23:11.042Z",
    "fired_at": "2026-04-10T14:23:11.051Z",
    "session_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "device_id": 1,
    "sensor_id": 3,
    "event_type": "A",
    "triggered_value": 0.847,
    "metadata": {},
    "window_id": 38
  }
]
```

---

#### `GET /api/events/export`

Stream events as CSV or NDJSON. Accepts the same filter parameters as `GET /api/events` (no `limit`/`offset` — streams all matching rows).

**Query parameters** (in addition to filters above)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `format` | enum | `csv` | `csv` or `ndjson` |

**Response 200** — `text/csv` or `application/x-ndjson`, streamed

CSV columns: `event_id, triggered_at, fired_at, session_id, device_id, sensor_id, event_type, triggered_value`

---

#### `GET /api/events/{event_id}`

Get a single event.

**Response 200** — event object · **Response 404**

---

#### `GET /api/events/{event_id}/window`

Retrieve the decoded ±9-second raw sample window for an event. This gives you the exact sensor values before and after the threshold crossing.

**Response 200**
```json
{
  "window_id": 38,
  "event_id": 42,
  "start_ts": "2026-04-10T14:23:02.000Z",
  "end_ts":   "2026-04-10T14:23:20.000Z",
  "sample_rate_hz": 123.0,
  "sample_count": 2214,
  "encoding": "zstd+delta-f32",
  "samples": [
    ["2026-04-10T14:23:02.000Z", 0.321],
    ["2026-04-10T14:23:02.008Z", 0.329],
    "..."
  ]
}
```

**Response 404** — event not found or no window attached

---

### 5.5 Detector Configuration

HERMES runs four detector types on every incoming sensor value. Each type has a global config and per-device / per-sensor overrides.

**Scope resolution**: sensor override → device override → global config

#### Type A — Variance detector

Fires when the rolling coefficient of variation (CV) over window `T1` samples exceeds `threshold_cv`.

#### `GET /api/config/type_a` / `PUT /api/config/type_a`

**GET Response 200** / **PUT Request body**
```json
{
  "enabled": true,
  "T1": 100,
  "threshold_cv": 0.05,
  "debounce_seconds": 2.0,
  "init_fill_ratio": 0.8,
  "expected_sample_rate_hz": 100.0
}
```

| Field | Description |
|-------|-------------|
| `enabled` | Toggle detector on/off |
| `T1` | Rolling window size (samples) |
| `threshold_cv` | CV threshold (0–1). CV = stddev/mean |
| `debounce_seconds` | Min seconds between events on same sensor |
| `init_fill_ratio` | Fraction of window that must be populated before detection starts (0–1) |
| `expected_sample_rate_hz` | Used for debounce conversion; set to match your hardware |

---

#### Type B — Tolerance-band detector

Fires when the value drifts outside `[mean × (1 - lower_pct), mean × (1 + upper_pct)]` over window `T2`.

#### `GET /api/config/type_b` / `PUT /api/config/type_b`

```json
{
  "enabled": true,
  "T2": 200,
  "lower_threshold_pct": 0.10,
  "upper_threshold_pct": 0.10,
  "debounce_seconds": 1.0,
  "init_fill_ratio": 0.8,
  "expected_sample_rate_hz": 100.0
}
```

---

#### Type C — Absolute bound detector

Fires when the value falls outside the absolute range `[threshold_lower, threshold_upper]`.

#### `GET /api/config/type_c` / `PUT /api/config/type_c`

```json
{
  "enabled": true,
  "T3": 1,
  "threshold_lower": -0.5,
  "threshold_upper":  3.0,
  "debounce_seconds": 0.5,
  "init_fill_ratio": 0.0,
  "expected_sample_rate_hz": 100.0
}
```

---

#### Type D — Two-stage drift detector

Fires when the short-term mean (window `T4`) deviates from the long-term mean (window `T5`) by more than `tolerance_pct`.

#### `GET /api/config/type_d` / `PUT /api/config/type_d`

```json
{
  "enabled": true,
  "T4": 50,
  "T5": 500,
  "tolerance_pct": 0.08,
  "debounce_seconds": 3.0,
  "init_fill_ratio": 0.8,
  "expected_sample_rate_hz": 100.0
}
```

---

#### Per-device and per-sensor overrides

```
GET  /api/config/{type_name}/overrides
PUT  /api/config/{type_name}/overrides/device/{device_id}
DELETE /api/config/{type_name}/overrides/device/{device_id}
PUT  /api/config/{type_name}/overrides/sensor/{device_id}/{sensor_id}
DELETE /api/config/{type_name}/overrides/sensor/{device_id}/{sensor_id}
```

`{type_name}` is `type_a`, `type_b`, `type_c`, or `type_d`.

Override PUT body is the same schema as the global config. To disable detection on one device:
```bash
curl -X PUT http://localhost:8080/api/config/type_a/overrides/device/3 \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"enabled":false,"T1":100,"threshold_cv":0.05,"debounce_seconds":2,"init_fill_ratio":0.8,"expected_sample_rate_hz":100}'
```

---

### 5.6 Sessions

A **session** is a named measurement run. All events are associated with the active session at the time they were detected. Sessions are the primary grouping mechanism for data analysis.

**Session scopes**:
- `GLOBAL` — system-wide; one active at a time; parent of all local sessions
- `LOCAL` — per-device child of the active global session

#### `GET /api/sessions/current`

Get the currently active global session and all active local sessions.

**Auth required.**

**Response 200**
```json
{
  "global_session": {
    "session_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "scope": "global",
    "package_id": "c8a6b2d1-...",
    "started_at": "2026-04-10T08:00:00Z",
    "ended_at": null,
    "notes": "Morning production run"
  },
  "local_sessions": [
    {"session_id": "...", "scope": "local", "device_id": 1, ...}
  ]
}
```

---

#### `POST /api/sessions`

Start a new session.

**Request body**
```json
{
  "scope": "global",
  "package_id": "c8a6b2d1-...",
  "notes": "Production run 2026-04-10",
  "record_raw_samples": false
}
```

For a `local` session, also provide `device_id`.

`record_raw_samples: true` stores every raw sensor reading in `session_samples` (high storage cost; ~400 KB/device/hour at 100 Hz).

**Response 201** — session object · **Response 409** — a session of this scope is already active

---

#### `POST /api/sessions/{session_id}/stop`

Close a session. Idempotent.

**Request body** (optional)
```json
{"ended_reason": "shift ended"}
```

**Response 200** — updated session object

---

#### `GET /api/sessions/{session_id}/logs`

Audit trail for a session — every start/stop/reconfigure action.

**Response 200**
```json
[
  {"log_id": 1, "event": "start", "ts": "...", "actor": "api", "details": null},
  {"log_id": 2, "event": "stop",  "ts": "...", "actor": "api", "details": {"reason":"shift ended"}}
]
```

---

### 5.7 Packages

A **package** holds a named set of detector configurations. Sessions reference a package; when a session ends, its package is locked to preserve the historical record. To change config for a new session, clone the package.

#### `POST /api/packages`

```json
{"name": "Production thresholds v2", "description": "Tighter CV for summer campaign"}
```

**Response 201** — package object

---

#### `POST /api/packages/{package_id}/clone`

Clone a locked or active package. Returns a new unlocked package with all parameters copied.

**Response 201** — new package object with `parent_package_id` set

---

### 5.8 MQTT Brokers

Manage the Mosquitto connection. At most one broker is active at a time.

#### `GET /api/mqtt-brokers`

```json
[{
  "broker_id": 1,
  "host": "localhost",
  "port": 1883,
  "username": "hermes",
  "has_password": true,
  "use_tls": false,
  "is_active": true
}]
```

#### `POST /api/mqtt-brokers`

```json
{"host": "mqtt.your-broker.com", "port": 8883, "username": "user", "password": "secret", "use_tls": true, "is_active": false}
```

Passwords are encrypted at rest (Fernet symmetric encryption). The plaintext password is never returned in responses — `has_password: true/false` is returned instead.

#### `POST /api/mqtt-brokers/{broker_id}/activate`

Atomically activates this broker and deactivates all others.

> **Note**: The `hermes-ingest` process must be restarted for a broker switch to take effect: `sudo systemctl restart hermes-ingest`.

---

### 5.9 Sensor Offsets

Calibration offsets applied to raw sensor values before detection. Offset-corrected value = `raw_value + offset`.

#### `GET /api/devices/{device_id}/offsets`

Returns all 12 sensors (missing sensors return `offset_value: 0.0`).

```json
{
  "device_id": 1,
  "offsets": [
    {"sensor_id": 1, "offset_value": 0.0,  "updated_at": "..."},
    {"sensor_id": 2, "offset_value": -0.05,"updated_at": "..."},
    "..."
  ]
}
```

#### `PUT /api/devices/{device_id}/offsets/{sensor_id}`

```json
{"offset_value": -0.05}
```

#### `PUT /api/devices/{device_id}/offsets`

Replace all offsets at once. Any sensor not in the payload has its offset deleted (reset to 0.0).

```json
{"offsets": {"1": 0.0, "2": -0.05, "3": 0.02}}
```

---

### 5.10 Live Stream (SSE)

Real-time sensor values delivered as Server-Sent Events. No auth required in the current release.

#### `GET /api/live_stream/{device_id}`

```
GET /api/live_stream/1?interval=0.1&max_samples=500
Accept: text/event-stream
```

**Query parameters**

| Parameter | Type | Default | Range |
|-----------|------|---------|-------|
| `interval` | float | 0.1 | 0.02–2.0 s |
| `max_samples` | int | 500 | 1–500 |

**SSE event format**
```
data: {"ts":"2026-04-10T14:23:11.042Z","device_id":1,"sensor_id":3,"value":0.847}

data: {"ts":"2026-04-10T14:23:11.051Z","device_id":1,"sensor_id":7,"value":1.203}
```

The stream ends after `max_samples` events or when the client disconnects.

**JavaScript example**
```javascript
const es = new EventSource('/api/live_stream/1?interval=0.1');
es.onmessage = e => {
  const { ts, sensor_id, value } = JSON.parse(e.data);
  chart.update(sensor_id, ts, value);
};
```

---

### 5.11 System Tunables

Read the runtime configuration and live system state.

#### `GET /api/system-tunables`

**Auth required.**

```json
{
  "state": {
    "version": "0.1.0-alpha.29",
    "ingest_mode": "mqtt",
    "shard_count": 1,
    "shard_index": 0,
    "dev_mode": false,
    "active_global_session_id": "3fa85f64-...",
    "active_local_session_count": 2,
    "sessions_recording_count": 0,
    "mqtt_devices_active": 5,
    "modbus_devices_active": 0
  },
  "tunables": [
    {
      "key": "hermes_jwt_expiry_seconds",
      "value": "3600",
      "description": "JWT token TTL in seconds",
      "editable": "restart",
      "edit_hint": "Set HERMES_JWT_EXPIRY_SECONDS in /etc/hermes/env"
    }
  ]
}
```

---

### 5.12 Prometheus Metrics

**No auth required.** Returns Prometheus text-format exposition.

```
GET /api/metrics
```

See [Section 10](#10-prometheus-metrics-reference) for the full metrics catalogue.

---

## 6. Database Schema

HERMES uses PostgreSQL 16 with the TimescaleDB extension. The database name is `hermes` (configurable).

### Tables

```
┌─────────────────┐     ┌────────────────────┐
│    devices      │     │     packages       │
│─────────────────│     │────────────────────│
│ device_id  PK   │     │ package_id UUID PK │
│ name            │     │ name               │
│ protocol        │     │ description        │
│ topic           │     │ is_default         │
│ is_active       │     │ is_locked          │
│ created_at      │     │ parent_package_id  │◄─┐
│ updated_at      │     │ created_at         │  │
└────────┬────────┘     └────────────────────┘  │ self-ref
         │                       ▲               │ (clone chain)
         │                       │               │
         │              ┌────────────────────┐  │
         │              │     parameters     │  │
         │              │────────────────────│  │
         │              │ parameter_id  PK   │  │
         │              │ package_id FK      │──┘
         │              │ scope (global/     │
         │              │   device/sensor)   │
         │              │ device_id nullable │
         │              │ sensor_id nullable │
         │              │ key (type_a...d)   │
         │              │ value JSONB        │
         │              └────────────────────┘
         │
         │         ┌─────────────────────────────┐
         │         │          sessions            │
         │         │─────────────────────────────│
         │         │ session_id UUID PK           │
         │         │ scope (global/local)         │
         │         │ parent_session_id nullable   │◄─┐ self-ref
         │         │ device_id nullable     FK ───┘  │ (local→global)
         │         │ package_id             FK        │
         │         │ started_at, ended_at            │
         │         │ record_raw_samples              │
         │         └────────────┬────────────────────┘
         │                      │
         │              ┌───────┴──────────┐
         │              │  session_logs    │
         │              │──────────────────│
         │              │ log_id PK        │
         │              │ session_id FK    │
         │              │ event (start/    │
         │              │   stop/reconfig) │
         │              │ ts, actor,details│
         │              └──────────────────┘
         │
    ┌────┴──────────────────────────────────┐
    │              events                    │
    │  (TimescaleDB hypertable on           │
    │   triggered_at — auto-partitioned     │
    │   by time for fast range queries)     │
    │───────────────────────────────────────│
    │ event_id  PK (composite)              │
    │ triggered_at TIMESTAMPTZ PK           │
    │ session_id FK                         │
    │ device_id  FK                         │
    │ sensor_id  SMALLINT (1–12)            │
    │ event_type ENUM (A/B/C/D/BREAK)      │
    │ fired_at   TIMESTAMPTZ                │
    │ triggered_value FLOAT                 │
    │ metadata JSONB                        │
    │ window_id FK → event_windows nullable │
    └───────────────────────────────────────┘
                       │
              ┌────────┴─────────────────┐
              │      event_windows        │
              │──────────────────────────│
              │ window_id PK              │
              │ event_id BIGINT           │
              │ start_ts, end_ts          │
              │ sample_rate_hz            │
              │ sample_count              │
              │ encoding (zstd+delta-f32) │
              │ data LARGEBINARY          │
              └──────────────────────────┘

Additional tables:
  sensor_offsets  (device_id PK, sensor_id PK, offset_value)
  session_samples (TimescaleDB hypertable — only populated when
                   record_raw_samples=true on a session)
  users           (user_id UUID PK, email unique, is_admin, is_enabled)
  user_otps       (one-time passwords, argon2id hashed)
  mqtt_brokers    (at most one is_active=true, enforced by partial unique index)
```

### Key design decisions

- **`events` is a TimescaleDB hypertable** partitioned on `triggered_at`. Queries with a time range filter are automatically routed to the relevant partition chunks. A `CREATE INDEX ON events (device_id, triggered_at DESC)` covers the most common access pattern.

- **Compressed window data** — `event_windows.data` stores raw float32 samples as a `zstd + delta` encoded byte buffer. The API decodes it to `[timestamp, value]` tuples before returning. Compression ratio is typically 6–10× over raw floats.

- **Package locking** — When the last session referencing a package ends, a database trigger sets `packages.is_locked = TRUE`. This preserves the exact detector configuration that was active during that session. To edit, clone the package first.

---

## 7. Event Detection System

### Overview

For each incoming sensor sample, the ingest pipeline applies offset correction, then passes the value to four independent detectors running in parallel. Each detector maintains a sliding window of recent samples.

```
raw_value → + offset → corrected_value
                              │
               ┌──────────────┼──────────────┐
               ▼              ▼              ▼         ▼
          Type A CV     Type B band    Type C bound  Type D drift
               │              │              │         │
               └──────────────┴──────────────┴─────────┘
                                     │
                              event fired if
                             threshold crossed
                             (after debounce)
                                     │
                              BREAK machine
                              (monitors arrival
                               rate; fires BREAK
                               if sensor goes silent)
```

### Event types

| Type | Algorithm | Fires when |
|------|-----------|-----------|
| `A` | Variance (CV) | Coefficient of variation over `T1` samples exceeds `threshold_cv` |
| `B` | Tolerance band | Value drifts outside `mean × (1 ± threshold_pct)` over `T2` samples |
| `C` | Absolute bound | Value falls outside `[threshold_lower, threshold_upper]` |
| `D` | Two-stage drift | Short-term mean (window `T4`) deviates from long-term mean (window `T5`) by more than `tolerance_pct` |
| `BREAK` | Silence detector | No samples received for a device within the expected inter-sample interval |

### Debounce

After an event fires, the same detector will not fire again for that sensor for `debounce_seconds`. This prevents a single transient anomaly from generating hundreds of events.

### Window capture

Every event triggers a capture of the raw sample buffer: `±9 seconds` around the event timestamp. The captured samples are stored compressed in `event_windows.data` and returned decoded by `GET /api/events/{event_id}/window`.

### MQTT re-publish

After persisting to the database, each event is also published to the MQTT broker on the topic `stm32/events/{event_type}` (e.g., `stm32/events/A`). Downstream PLCs and SCADA systems can subscribe to receive real-time event notifications.

---

## 8. MQTT Integration

### Inbound (STM32 → HERMES)

HERMES subscribes to the topic `stm32/adc` (configurable via `MQTT_TOPIC_ADC`).

**Expected payload format** (your STM32 firmware must produce this):
```json
{
  "device_id": 1,
  "ts": 1712756591042,
  "ch": {
    "1": 0.847,
    "2": 1.203,
    "3": 0.012,
    "4": 2.991,
    "5": 0.543,
    "6": 0.321,
    "7": 1.100,
    "8": 0.987,
    "9": 0.654,
    "10": 0.123,
    "11": 0.456,
    "12": 0.789
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `device_id` | int | Must match a registered device |
| `ts` | int | Unix milliseconds |
| `ch` | object | Channel (sensor) readings; keys are string integers 1–12 |

### Outbound (HERMES → downstream systems)

HERMES publishes to:

| Topic | Fires on |
|-------|----------|
| `stm32/events/A` | Type A event detected |
| `stm32/events/B` | Type B event detected |
| `stm32/events/C` | Type C event detected |
| `stm32/events/D` | Type D event detected |
| `stm32/events/BREAK` | BREAK event detected |

**Outbound event payload**:
```json
{
  "event_id": 42,
  "event_type": "A",
  "device_id": 1,
  "sensor_id": 3,
  "triggered_at": "2026-04-10T14:23:11.042Z",
  "triggered_value": 0.847,
  "session_id": "3fa85f64-..."
}
```

### Broker management

Configure the MQTT broker via the REST API (`/api/mqtt-brokers`). The ingest process reads broker credentials from the database at startup. After changing the active broker, restart `hermes-ingest`:

```bash
sudo systemctl restart hermes-ingest
```

---

## 9. Configuration Reference

HERMES is configured via environment variables in `/etc/hermes/env` and `/etc/hermes/secrets.env`.

### Core settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://hermes_app:...@localhost/hermes` | Async SQLAlchemy URL (API) |
| `MIGRATE_DATABASE_URL` | `postgresql://hermes_migrate:...@localhost/hermes` | Sync URL for Alembic migrations |
| `HERMES_JWT_SECRET` | Generated at install time | HS256 signing key (32+ chars) |
| `HERMES_JWT_EXPIRY_SECONDS` | `3600` | JWT token TTL |
| `HERMES_DEV_MODE` | `0` | `1` disables auth (development only) |
| `HERMES_LOG_FORMAT` | `json` | `json` or `text` |
| `HERMES_ALLOWED_EMAILS_PATH` | `/etc/hermes/allowed_emails.txt` | OTP allowlist file |

### Ingest settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_HOST` | `localhost` | MQTT broker hostname |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USERNAME` | `""` | MQTT username |
| `MQTT_PASSWORD` | `""` | MQTT password |
| `MQTT_TOPIC_ADC` | `stm32/adc` | Topic HERMES subscribes to |
| `MQTT_TOPIC_EVENTS_PREFIX` | `stm32/events` | Prefix for outbound event topics |
| `HERMES_SHARD_COUNT` | `1` | Number of ingest processes (multi-core mode) |
| `HERMES_SHARD_INDEX` | `0` | Shard index for this process |

### OTP / email settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SMTP_HOST` | `localhost` | SMTP server |
| `SMTP_PORT` | `25` | SMTP port |
| `SMTP_USERNAME` | `""` | SMTP auth username |
| `SMTP_PASSWORD` | `""` | SMTP auth password |
| `SMTP_FROM` | `hermes@localhost` | From address for OTP emails |
| `OTP_EXPIRY_SECONDS` | `300` | OTP TTL (5 minutes) |
| `OTP_MAX_ATTEMPTS` | `5` | Wrong attempts before lockout |
| `OTP_MAX_PER_HOUR` | `5` | Max OTP requests per email per hour |

---

## 10. Prometheus Metrics Reference

All metrics are exposed at `GET /api/metrics` in Prometheus text format.

### Counters

| Metric | Labels | Description |
|--------|--------|-------------|
| `hermes_msgs_received_total` | `device_id` | MQTT messages processed |
| `hermes_msgs_invalid_total` | — | JSON decode failures |
| `hermes_samples_processed_total` | `device_id` | Samples after offset correction |
| `hermes_events_detected_total` | `event_type`, `device_id` | Events fired by detectors |
| `hermes_events_persisted_total` | `event_type` | Events written to DB |
| `hermes_events_published_total` | `event_type` | Events re-published to MQTT |
| `hermes_session_samples_written_total` | — | Raw rows persisted (if recording) |
| `hermes_session_samples_dropped_total` | — | Rows dropped (queue full) |
| `hermes_modbus_reads_ok_total` | `device_id` | Successful Modbus TCP reads |
| `hermes_modbus_reads_failed_total` | `device_id` | Failed Modbus TCP reads |

### Gauges

| Metric | Description |
|--------|-------------|
| `hermes_consume_queue_depth` | Pending MQTT messages in handoff queue |
| `hermes_db_writer_pending` | Events waiting for DB write |
| `hermes_session_samples_queue_depth` | Buffered raw sensor rows |
| `hermes_session_samples_recording_active` | 1 if any session is recording raw samples |
| `hermes_modbus_pollers_active` | Active Modbus TCP pollers |
| `hermes_mqtt_connected` | 1 if MQTT client is connected |

### Histograms

| Metric | Labels | Description |
|--------|--------|-------------|
| `hermes_pipeline_stage_duration_seconds` | `stage` | Per-stage pipeline latency (sampled 1/100) |

**Buckets**: 0.1 ms, 0.5 ms, 1 ms, 5 ms, 10 ms, 50 ms, 100 ms, 500 ms, 1 s

**Example Prometheus query** — events per second (5-minute window):
```promql
rate(hermes_events_detected_total[5m])
```

**Example** — pipeline 99th-percentile latency:
```promql
histogram_quantile(0.99, rate(hermes_pipeline_stage_duration_seconds_bucket[5m]))
```

---

## 11. Error Reference

| HTTP Status | When |
|-------------|------|
| `400 Bad Request` | Malformed JSON body |
| `401 Unauthorized` | Missing, expired, or invalid JWT |
| `404 Not Found` | Resource with the given ID doesn't exist |
| `409 Conflict` | Duplicate `device_id`, active session already running |
| `422 Unprocessable Entity` | Validation failure (missing field, out-of-range value) |
| `429 Too Many Requests` | OTP rate limit exceeded |
| `503 Service Unavailable` | Database unreachable, or ingest pipeline not yet initialised |

**Error body format**:
```json
{"detail": "human-readable description"}
```

For validation errors (422), `detail` is a list:
```json
{
  "detail": [
    {"loc": ["body", "device_id"], "msg": "value is not a valid integer", "type": "type_error.integer"}
  ]
}
```

---

## 12. Quick-Start Code Examples

### Python (httpx)

```python
import httpx

BASE = "http://localhost:8080"

with httpx.Client(base_url=BASE) as client:
    # 1. Request OTP
    client.post("/api/auth/otp/request", json={"email": "ops@your-org.com"})

    # 2. Verify OTP (check email for the code)
    otp = input("Enter OTP: ")
    r = client.post("/api/auth/otp/verify", json={"email": "ops@your-org.com", "otp": otp})
    token = r.json()["access_token"]

    # 3. Query events
    headers = {"Authorization": f"Bearer {token}"}
    events = client.get("/api/events", params={"device_id": 1, "limit": 50}, headers=headers)
    for e in events.json():
        print(e["event_id"], e["event_type"], e["triggered_at"])
```

### Python — async SSE live stream

```python
import asyncio, httpx, json

async def stream_device(device_id: int):
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", f"http://localhost:8080/api/live_stream/{device_id}") as r:
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    sample = json.loads(line[6:])
                    print(sample["sensor_id"], sample["value"])

asyncio.run(stream_device(1))
```

### JavaScript / Node.js (fetch)

```javascript
const BASE = 'http://localhost:8080';

// Login
const { access_token } = await fetch(`${BASE}/api/auth/otp/verify`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email: 'ops@your-org.com', otp: '123456' }),
}).then(r => r.json());

// Query last 100 events for device 1
const events = await fetch(`${BASE}/api/events?device_id=1&limit=100`, {
  headers: { Authorization: `Bearer ${access_token}` },
}).then(r => r.json());

// Export all events as CSV
const csv = await fetch(`${BASE}/api/events/export?format=csv`, {
  headers: { Authorization: `Bearer ${access_token}` },
}).then(r => r.text());
```

### curl — start a session and capture events

```bash
TOKEN="eyJ..."
API="http://localhost:8080"

# Start a global session
SESSION=$(curl -s -X POST "$API/api/sessions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"scope":"global","package_id":"<your-package-id>","notes":"Integration test"}' \
  | jq -r .session_id)

echo "Session: $SESSION"

# ... run your process ...

# Stop the session
curl -s -X POST "$API/api/sessions/$SESSION/stop" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ended_reason":"test complete"}' | jq .

# Pull all events from this session
curl -s "$API/api/events?limit=500" \
  -H "Authorization: Bearer $TOKEN" | jq '.[] | {id:.event_id,type:.event_type,value:.triggered_value}'
```

---

*For installation instructions, system operations, and infrastructure runbooks see [`docs/operations/INSTALLATION.md`](../operations/INSTALLATION.md).*

*For architecture internals and backend development see [`docs/guides/BACKEND.md`](../guides/BACKEND.md).*
