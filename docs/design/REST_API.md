# REST_API.md — every endpoint

> **Audience:** anyone integrating with HERMES, debugging a UI request,
> or planning a new endpoint. Catalogs every route currently registered
> by `create_app()` in `services/hermes/api/main.py` with request /
> response shapes, status codes, and notes.
>
> **Companion docs:**
> - [`DATABASE_SCHEMA.md`](./DATABASE_SCHEMA.md) — the row shapes
> - [`../guides/BACKEND.md`](../guides/BACKEND.md) — file-level overview
> - [`../guides/EVENTS.md`](../guides/EVENTS.md) — what's in `events.metadata`

---

## Conventions

- All routes are mounted under `/api/...`.
- Auth: every route requires a `Bearer <jwt>` header except those
  marked **public** below. In `HERMES_DEV_MODE=1` the auth guard
  short-circuits to a stub user.
- Error shape (FastAPI default):
  ```json
  {"detail": "human-readable string"}
  ```
  or for validation:
  ```json
  {"detail": [{"loc": [...], "msg": "...", "type": "..."}]}
  ```
- Times are ISO-8601 with timezone (e.g. `"2026-04-26T14:30:00.123Z"`).
- IDs: `device_id` is `int`; `session_id`, `package_id`, etc. are UUID strings.
- 422 vs 409:
  - `422` = the request shape is wrong / a referenced object doesn't exist
  - `409` = a domain invariant blocks the change (e.g. another active session, broker active-row collision)

---

## Endpoint index

| Group | Method | Path | Auth | Notes |
|-------|--------|------|------|-------|
| Health | GET | `/api/health` | public | Liveness |
| Health | GET | `/api/health/ready` | public | Readiness (DB + MQTT) |
| Auth | POST | `/api/auth/otp/request` | public | Issue OTP via email |
| Auth | POST | `/api/auth/otp/verify` | public | Exchange OTP for JWT |
| Auth | POST | `/api/auth/logout` | required | Clear session (best-effort) |
| Devices | GET | `/api/devices` | required | List |
| Devices | POST | `/api/devices` | required | Create |
| Devices | GET | `/api/devices/{id}` | required | Get one |
| Devices | PATCH | `/api/devices/{id}` | required | Partial update |
| Devices | DELETE | `/api/devices/{id}` | required | Delete (cascade offsets) |
| Offsets | GET | `/api/devices/{id}/offsets` | required | All 12 sensor offsets |
| Offsets | PUT | `/api/devices/{id}/offsets` | required | Bulk replace |
| Offsets | PUT | `/api/devices/{id}/offsets/{sensor_id}` | required | Upsert one |
| Offsets | DELETE | `/api/devices/{id}/offsets/{sensor_id}` | required | Reset to 0.0 |
| Events | GET | `/api/events` | required | List with filters |
| Events | GET | `/api/events/export` | required | Stream CSV/NDJSON |
| Events | GET | `/api/events/{id}` | required | Get one |
| Events | GET | `/api/events/{id}/window` | required | ±9 s decoded samples |
| Config | GET | `/api/config/type_{a\|b\|c\|d}` | required | Global threshold for type |
| Config | PUT | `/api/config/type_{a\|b\|c\|d}` | required | Replace global threshold |
| Config | GET | `/api/config/{type}/overrides` | required | Per-device + per-sensor overrides |
| Config | PUT | `/api/config/{type}/devices/{device_id}` | required | Set device-scope override |
| Config | DELETE | `/api/config/{type}/devices/{device_id}` | required | Clear device-scope override |
| Config | PUT | `/api/config/{type}/devices/{device_id}/sensors/{sensor_id}` | required | Set sensor-scope override |
| Config | DELETE | `/api/config/{type}/devices/{device_id}/sensors/{sensor_id}` | required | Clear sensor-scope override |
| Sessions | GET | `/api/sessions` | required | List with filters |
| Sessions | GET | `/api/sessions/current` | required | Active GLOBAL + LOCALs |
| Sessions | GET | `/api/sessions/{id}` | required | Get one |
| Sessions | POST | `/api/sessions` | required | Start |
| Sessions | POST | `/api/sessions/{id}/stop` | required | Close (idempotent) |
| Sessions | GET | `/api/sessions/{id}/logs` | required | Audit trail |
| Packages | GET | `/api/packages` | required | List newest first |
| Packages | POST | `/api/packages` | required | Create blank |
| Packages | GET | `/api/packages/{id}` | required | Get one |
| Packages | POST | `/api/packages/{id}/clone` | required | Fork copying parameters |
| MQTT brokers | GET | `/api/mqtt-brokers` | required | List |
| MQTT brokers | POST | `/api/mqtt-brokers` | required | Create |
| MQTT brokers | GET | `/api/mqtt-brokers/{id}` | required | Get one |
| MQTT brokers | PATCH | `/api/mqtt-brokers/{id}` | required | Partial update |
| MQTT brokers | DELETE | `/api/mqtt-brokers/{id}` | required | Delete |
| MQTT brokers | POST | `/api/mqtt-brokers/{id}/activate` | required | Atomic activate |
| System | GET | `/api/system-tunables` | required | Live state + boot-time tunables |
| Live | GET | `/api/live_stream/{device_id}` | public¹ | SSE stream |
| Metrics | GET | `/api/metrics` | public² | Prometheus text format |

¹ Will move behind auth in a follow-up; today the auth bypass in dev mode + nginx ACLs gate access.
² By design — firewall / nginx in front in production.

---

## 1. Health

### `GET /api/health`

Liveness probe. No DB hit. Always 200 if the process is alive.

**Response:**
```json
{"status": "ok", "version": "0.1.0a25"}
```

### `GET /api/health/ready`

Readiness probe. Hits Postgres + checks `MQTT_CONNECTED` gauge.

**Response (200, healthy):**
```json
{"status": "ready", "version": "0.1.0a25"}
```

**Response (503, not ready):** plain text body with the failure reason.

---

## 2. Auth

### `POST /api/auth/otp/request`

Issue a 6-digit OTP and email it to the user. Email must be in
`Settings.allowed_emails_path`.

**Body:**
```json
{"email": "operator@example.com"}
```

**Responses:**
- `204` — OTP issued (or rate-limited silently to prevent enumeration)
- `429` — rate-limited, `Retry-After` header
- `422` — body doesn't validate (e.g. not an email)

The route deliberately returns the same shape whether the email is in
the allowlist or not; the email is silently dropped if not allowlisted.
Prevents enumeration via response timing.

### `POST /api/auth/otp/verify`

Exchange the 6-digit code for a JWT.

**Body:**
```json
{"email": "operator@example.com", "code": "123456"}
```

**Response (200):**
```json
{
  "access_token": "<JWT>",
  "token_type": "bearer",
  "expires_in": 3600
}
```

**Errors:**
- `401` — wrong code, expired, or user disabled
- `429` — too many failed attempts (`OTP_MAX_ATTEMPTS`)

### `POST /api/auth/logout`

Best-effort logout. JWTs are stateless so this just signals the client
to drop the token; the server doesn't blacklist.

**Response: 204.**

---

## 3. Devices

### `GET /api/devices`

List all devices, ordered by `device_id` ascending.

**Response:**
```json
[
  {
    "device_id": 1,
    "name": "stm32-line-A",
    "protocol": "mqtt",
    "topic": null,
    "is_active": true,
    "created_at": "2026-04-25T...",
    "updated_at": "2026-04-25T..."
  }
]
```

The Modbus-specific `modbus_config` JSONB is NOT projected here — too
large for list views. It's surfaced on `GET /api/devices/{id}` (the
single-row view) which the UI uses for the edit form.

### `POST /api/devices`

Create a device row.

**Body:**
```json
{
  "device_id": 1,
  "name": "stm32-line-A",
  "protocol": "mqtt",
  "topic": null
}
```

`device_id` is operator-assigned (1..999). Creating a Modbus device
requires `protocol="modbus_tcp"` AND `modbus_config` (validated by
`ModbusConfig` pydantic model).

**Responses:**
- `201` — created
- `409` — `device_id` already exists
- `422` — schema mismatch (e.g. range violation)

### `GET /api/devices/{device_id}`

Get one. Returns 404 on miss.

### `PATCH /api/devices/{device_id}`

Partial update. Fields omitted are unchanged.

**Body (all optional):**
```json
{
  "name": "new-name",
  "is_active": false,
  "topic": "stm32/adc/special",
  "modbus_config": {...}
}
```

`updated_at` is auto-touched by the trigger.

### `DELETE /api/devices/{device_id}`

Hard delete. Cascades `sensor_offsets`. Will block (FK constraint) if
any `events` reference the device — soft-disable via `PATCH is_active=false`
is the operator-friendly alternative.

**Response: 204.**

---

## 4. Offsets

Per-sensor calibration. `engineering_value = raw_value − offset_value`.
Mounted under `/api/devices/{device_id}/offsets`.

### `GET /api/devices/{device_id}/offsets`

Always returns 12 entries (sensors 1..12). Sensors with no row default
to `offset_value=0.0` and `updated_at=null`.

**Response:**
```json
{
  "device_id": 1,
  "offsets": [
    {"sensor_id": 1, "offset_value": 1.25, "updated_at": "2026-04-25T..."},
    {"sensor_id": 2, "offset_value": 0.0, "updated_at": null},
    ...
  ]
}
```

### `PUT /api/devices/{device_id}/offsets/{sensor_id}`

Upsert one sensor's offset.

**Body:**
```json
{"offset_value": 1.25}
```

**Response (200):** the new `SensorOffsetOut`.

### `DELETE /api/devices/{device_id}/offsets/{sensor_id}`

Reset to 0.0 (deletes the row). Re-reading via `GET .../{sensor_id}`
returns 404; `GET .../offsets` returns the default-zero entry.

**Response: 204** (then a subsequent DELETE returns 404).

### `PUT /api/devices/{device_id}/offsets`

Bulk replace all 12 offsets.

**Body:**
```json
{
  "offsets": [
    {"sensor_id": 1, "offset_value": 1.25},
    {"sensor_id": 2, "offset_value": 0.5},
    ...
  ]
}
```

Empty list clears all offsets for the device.

---

## 5. Events

### `GET /api/events`

List. Supports filtering by:

| Query param | Type | Meaning |
|-------------|------|---------|
| `device_id` | `int` | Single device |
| `sensor_id` | `int` | Combine with `device_id` for per-sensor |
| `event_type` | `A`/`B`/`C`/`D`/`BREAK` | Filter to one type |
| `since` | ISO-8601 | `triggered_at >= since` |
| `until` | ISO-8601 | `triggered_at < until` |
| `limit` | `int` | Default 100, max 500 |
| `offset` | `int` | Pagination |

**Response:**
```json
[
  {
    "event_id": 12345,
    "triggered_at": "2026-04-25T14:30:01.123Z",
    "fired_at": "2026-04-25T14:30:10.456Z",
    "session_id": "uuid",
    "device_id": 1,
    "sensor_id": 5,
    "event_type": "A",
    "triggered_value": 47.83,
    "metadata": {"cv_percent": 8.7, "average": 50.0, "std": 4.4, ...},
    "window_id": 9876
  }
]
```

Newest first (`ORDER BY triggered_at DESC`). Hits one of the
`events_by_*_ts` indexes depending on filters.

### `GET /api/events/export`

Stream the same query result as CSV or NDJSON.

**Query params:** same as list, plus:
- `format`: `csv` (default) or `ndjson`

**Response:** `text/csv` or `application/x-ndjson`. Streamed in
chunks; each chunk opens its own DB session so a long export doesn't
hold a connection.

CSV columns: `event_id, triggered_at, fired_at, device_id, sensor_id,
event_type, triggered_value, window_id, metadata` (the metadata column
is JSON-encoded inline).

### `GET /api/events/{event_id}`

Get one. 404 on miss.

**Response:** same shape as a list entry.

### `GET /api/events/{event_id}/window`

Returns the ±9 s sample window with `data` decoded into a list of
`(ts, value)` pairs.

**Response:**
```json
{
  "window_id": 9876,
  "event_id": 12345,
  "start_ts": "2026-04-25T14:29:52.123Z",
  "end_ts": "2026-04-25T14:30:10.123Z",
  "sample_rate_hz": 100.0,
  "sample_count": 1820,
  "encoding": "json-utf8",
  "samples": [[1700000000.123, 50.0], [1700000000.133, 50.1], ...]
}
```

**Errors:**
- `404` if the event doesn't exist
- `404` if `event.window_id IS NULL` (still flushing or write failed)
- `404` if the window row was deleted manually

> **Note:** the SvelteKit events page expands a row to show window
> metadata but doesn't yet plot the samples. A "window chart" UI is
> tracked as a follow-up.

---

## 6. Config

Detector thresholds. Configs are stored in `parameters` and resolved
SENSOR > DEVICE > GLOBAL by `DbConfigProvider`. Mutating routes call
`_commit_and_reload()` and emit `pg_notify('hermes_config_changed', package_id)`.

### `GET /api/config/type_a`

Return the GLOBAL Type A config.

**Response:**
```json
{
  "enabled": true,
  "T1": 1.0,
  "threshold_cv": 5.0,
  "debounce_seconds": 0.0,
  "init_fill_ratio": 0.9,
  "expected_sample_rate_hz": 100.0
}
```

### `PUT /api/config/type_a`

Replace the GLOBAL Type A config. Same body shape as the GET response.

**Behaviour:** writes a `parameters` row with `scope='global'`,
`key='type_a.config'`. If a row already exists, it's updated (the
unique index spans `(package_id, key, scope, COALESCE(device_id, 0),
COALESCE(sensor_id, 0))`). After commit, calls `provider.reload()`,
resets the detection engine for every cached device, and emits NOTIFY.

`type_b`, `type_c`, `type_d`, and `mode_switching` follow the same
pattern with the corresponding fields documented in
[`../guides/EVENTS.md`](../guides/EVENTS.md).

### `GET /api/config/{type_name}/overrides`

`type_name` ∈ {`type_a`, `type_b`, `type_c`, `type_d`, `mode_switching`}.
Returns every device-level and sensor-level override for that type.

**Response:**
```json
{
  "devices": {
    "1": {"enabled": true, "T1": 0.5, ...},
    "3": {"enabled": false, ...}
  },
  "sensors": [
    {"device_id": 1, "sensor_id": 5, "config": {...}}
  ]
}
```

### `PUT /api/config/{type_name}/devices/{device_id}`

Set a device-scope override.

**Body:** the full config dataclass for that type.

**Behaviour:** upserts a `parameters` row with `scope='device'`,
`device_id=<id>`. Triggers reload + NOTIFY.

### `DELETE /api/config/{type_name}/devices/{device_id}`

Clear the device-scope override. Reverts to GLOBAL.

**Response: 204.**

### `PUT /api/config/{type_name}/devices/{device_id}/sensors/{sensor_id}`

Set a sensor-scope override. Same body shape; `scope='sensor'`,
`(device_id, sensor_id)` set.

### `DELETE /api/config/{type_name}/devices/{device_id}/sensors/{sensor_id}`

Clear the sensor-scope override. Reverts to DEVICE-level if set, else GLOBAL.

---

## 7. Sessions

Operator-driven session lifecycle.

### `GET /api/sessions`

List with filters.

**Query params:**

| Param | Type | Meaning |
|-------|------|---------|
| `active` | `true`/`false` | Filter to open / closed |
| `scope` | `global`/`local` | Filter to scope |
| `device_id` | `int` | LOCAL sessions for this device |
| `limit` | `int` | Default 100, max 500 |

Response: array of `SessionOut`, newest `started_at` first.

### `GET /api/sessions/current`

Convenience endpoint: the active GLOBAL plus every active LOCAL in one call.

**Response:**
```json
{
  "global_session": {...} | null,
  "local_sessions": [...]
}
```

### `GET /api/sessions/{session_id}`

Get one. 404 on miss.

### `POST /api/sessions`

Start a new session.

**Body:**
```json
{
  "scope": "global" | "local",
  "package_id": "uuid",
  "device_id": 1,           // required for LOCAL, must be omitted for GLOBAL
  "notes": "shift A start", // optional
  "record_raw_samples": false
}
```

**Server-side:**
- For LOCAL, `parent_session_id` is set to the current active GLOBAL
  (422 if no active GLOBAL exists).
- Atomic insert; the partial unique indexes enforce single active
  GLOBAL and single active LOCAL per device.

**Responses:**
- `201` — created
- `409` — another active session already holds this scope
- `422` — scope-shape mismatch, missing parent GLOBAL, or unknown package

### `POST /api/sessions/{session_id}/stop`

Close the session. Idempotent: closing an already-closed session
returns the row unchanged.

**Body:**
```json
{"ended_reason": "operator stopped" }
```

**Side effects** (via DB triggers):
- Closing a GLOBAL cascades-close every LOCAL child via `end_local_children`.
- Closes the package via `lock_package_on_session_end` (sets
  `packages.is_locked = TRUE`).
- Writes a `session_logs` row with `event='stop'`.

### `GET /api/sessions/{session_id}/logs`

Audit trail.

**Query params:**
- `order`: `asc` (default, chronological) or `desc`

**Response:** array of `SessionLogOut`. Empty if the session has no
log rows yet (404 if the session itself doesn't exist).

---

## 8. Packages

### `GET /api/packages`

List all, newest `created_at` first.

### `POST /api/packages`

Create a fresh, unlocked package with no parameter rows.

**Body:**
```json
{"name": "tuning-2026Q2", "description": "..."}
```

`is_default` cannot be flipped through this route; it's owned by the
bootstrap helper.

### `GET /api/packages/{package_id}`

Get one.

### `POST /api/packages/{package_id}/clone`

Fork the package: copy every `parameters` row to a new package, set
`parent_package_id` to the source. The clone is unlocked even if the
source is locked.

**Body:** `{"name": "...", "description": "..."}` for the clone.

This is the canonical way to "edit a locked package" — clone it, edit
the clone via `/api/config`, then start a new session against the clone.

---

## 9. MQTT brokers

Broker registry (gap 4, alpha.18). The partial unique index
`mqtt_brokers_one_active` enforces single active row.

### `GET /api/mqtt-brokers`

List, ordered by `broker_id` ascending.

### `POST /api/mqtt-brokers`

Create a broker row.

**Body:**
```json
{
  "host": "broker.example.com",
  "port": 1883,
  "username": "iot",
  "password": "hunter2",
  "use_tls": false,
  "is_active": true
}
```

If `is_active=true` (default), every other row is deactivated atomically.

**Password handling:**
- `password` is encrypted via Fernet at-rest (key derived from
  `HERMES_JWT_SECRET`) and stored in `mqtt_brokers.password_enc`.
- Plaintext is **NEVER** returned in any response.
- Empty string ≡ "no password".

### `GET /api/mqtt-brokers/{broker_id}`

Get one. The response carries `has_password: bool`, never the value.

### `PATCH /api/mqtt-brokers/{broker_id}`

Partial update. Password semantics:

| `password` value | Effect |
|------------------|--------|
| field omitted | unchanged |
| `""` (empty string) | cleared |
| non-empty string | re-encrypted and stored |

`is_active=true` triggers the deactivate-others swap.

### `DELETE /api/mqtt-brokers/{broker_id}`

Delete the row. 204; subsequent DELETE on same id returns 404.

### `POST /api/mqtt-brokers/{broker_id}/activate`

Atomic activate. Idempotent: activating an already-active broker
returns the row unchanged.

> **Important:** flipping the active row does NOT reconnect a running
> `hermes-ingest`. Operators must `systemctl restart hermes-ingest`
> after activating a different broker. Live broker switchover is a
> tracked follow-up.

---

## 10. System tunables

### `GET /api/system-tunables`

Read-only system status + boot-time tunable values.

**Response:**
```json
{
  "state": {
    "version": "0.1.0a25",
    "ingest_mode": "all",
    "shard_count": 1,
    "shard_index": 0,
    "dev_mode": false,
    "log_format": "json",
    "active_global_session_id": "uuid" | null,
    "active_local_session_count": 0,
    "sessions_recording_count": 0,
    "modbus_devices_active": 0,
    "mqtt_devices_active": 5
  },
  "tunables": [
    {
      "key": "event_ttl_seconds",
      "value": 5.0,
      "description": "TtlGateSink dedup window. ...",
      "editable": "restart",
      "edit_hint": "EVENT_TTL_SECONDS in /etc/hermes/ingest.env, then systemctl restart hermes-ingest"
    },
    ...
  ]
}
```

`editable` is one of:
- `live` — editable today (none currently; reserved for future runtime-tunable knobs)
- `via_other_route` — editable via another endpoint (`/api/config`, `/api/mqtt-brokers`)
- `restart` — needs editing the env file + service restart

Sensitive fields (JWT secret, DB URL, SMTP password) are explicitly
NEVER included; an integration test fails if they leak.

---

## 11. Live stream

### `GET /api/live_stream/{device_id}`

Server-Sent Events feed of the device's live samples.

**Query params:**
- `interval` — server poll tick (seconds), default 0.1, min 0.02, max 2.0
- `max_samples` — cap on samples per SSE frame, default 500

**Response:** `text/event-stream`. Frame format:
```
data: {"device_id": 1, "samples": [{"ts": 1700000000.123, "values": {"1": 50.1, "2": 49.8, ..., "12": 52.3}}, ...]}

```

Empty `samples: []` arrives as a keepalive on otherwise-quiet sensors
every ~15 s. Initial frame includes a `retry: 3000` hint so EventSource
reconnects within 3 s.

The endpoint is currently unauthenticated (auth lands when the JWT
flow finishes wiring through the UI). nginx ACLs gate access in
production.

---

## 12. Metrics

### `GET /api/metrics`

Prometheus text-format scrape. Returns the default registry's serialised state.

```
# HELP hermes_msgs_received_total ...
# TYPE hermes_msgs_received_total counter
hermes_msgs_received_total{device_id="1"} 12345
...
```

By design unauthenticated — scrape from inside the trusted network or
firewall it. See [`../guides/METRICS.md`](../guides/METRICS.md) for the
full metric catalog.

---

## 13. Adding a new endpoint — the checklist

1. Pick the route file (one resource per file under `services/hermes/api/routes/`).
2. Define `XIn` / `XOut` Pydantic models at the top.
3. Use `CurrentUser` and `DbSession` deps for protected routes.
4. Validate at the boundary; let FastAPI return 422 on shape errors.
5. Return 409 on `IntegrityError` from a constraint violation.
6. Register the router in `services/hermes/api/main.py:create_app()`.
7. Add a row to the index in this doc.
8. Write integration tests against a real Postgres (no mocking of the DB).
9. Update [`BACKEND.md`](../guides/BACKEND.md) §3 if the route file is new.
