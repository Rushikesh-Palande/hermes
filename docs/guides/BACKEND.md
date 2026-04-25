# BACKEND.md — every Python module

> **Audience:** developers about to change something on the Python side.
> Maps every file under `services/hermes/` to its responsibility, its
> public surface, and the other modules it interacts with.
>
> **Companion docs:**
> - [`WORKFLOW.md`](./WORKFLOW.md) — narrative data flow
> - [`EVENTS.md`](./EVENTS.md) — detector mechanics in detail
> - [`UI.md`](./UI.md) — the TypeScript counterpart
> - [`../design/REST_API.md`](../design/REST_API.md) — REST surface
> - [`../design/DATABASE_SCHEMA.md`](../design/DATABASE_SCHEMA.md) — schema reference

---

## Top-level package layout

```
services/hermes/
├── __init__.py              version lookup (importlib.metadata)
├── config.py                pydantic Settings — every env-var-backed knob
├── logging.py               structlog wiring
├── metrics.py               Prometheus counters, gauges, histograms
│
├── api/                     FastAPI app + every HTTP route
│   ├── __init__.py
│   ├── __main__.py          `python -m hermes.api` entry (uvicorn launcher)
│   ├── main.py              create_app() factory + lifespan (startup/shutdown)
│   ├── deps.py              CurrentUser, DbSession FastAPI dependencies
│   └── routes/              one file per resource (see §3)
│
├── auth/                    OTP + JWT + at-rest secret encryption
│   ├── allowlist.py         email allowlist for OTP gating
│   ├── email.py             SMTP send (aiosmtplib, no-op fallback)
│   ├── jwt.py               HS256 issue + verify
│   ├── otp.py               argon2 hash, generation, verify, rate limits
│   └── secret_box.py        Fernet at-rest encryption (broker passwords)
│
├── db/                      SQLAlchemy 2.0 async engine + models
│   ├── engine.py            engine + async_session() context manager
│   └── models.py            every ORM class (mirrors migrations/)
│
├── detection/               event detection pipeline
│   ├── config.py            TypeA/B/C/D + ModeSwitching dataclasses + provider Protocol
│   ├── db_config.py         DbConfigProvider — DB-backed config + LISTEN
│   ├── db_sink.py           DbEventSink — async events + windows writer
│   ├── encoding.py          encode/decode_window — sample BLOB codec
│   ├── engine.py            DetectionEngine — fan-out + mode gating
│   ├── mode_switching.py    ModeStateMachine — POWER_ON/STARTUP/BREAK
│   ├── mqtt_sink.py         MqttEventSink — outbound publish
│   ├── session.py           ensure_default_session() — bootstrap helper
│   ├── sink.py              LoggingEventSink, MultiplexEventSink
│   ├── sliding.py           IncrementalSlidingWindow — shared by Types A/B/C/D
│   ├── ttl_gate.py          TtlGateSink — dedup + priority + BREAK bypass
│   ├── type_a.py            CV% (variance) detector
│   ├── type_b.py            tolerance-band-around-T2-mean detector
│   ├── type_c.py            absolute-bound-on-T3-mean detector
│   ├── type_d.py            band-around-T5-of-T4 detector
│   ├── types.py             Sample, DetectedEvent, Protocol definitions
│   └── window_buffer.py     EventWindowBuffer — 30 s ring for ±9 s slicing
│
└── ingest/                  MQTT consumer + Modbus poller + writers
    ├── __main__.py          `python -m hermes.ingest` entry
    ├── clock.py             ClockRegistry — STM32 counter ↔ wall time anchor
    ├── live_data.py         LiveDataHub — per-device ring buffer for SSE
    ├── main.py              IngestPipeline + _consume + run() entry
    ├── modbus.py            ModbusManager + ModbusPoller (gap 7)
    ├── offsets.py           OffsetCache — per-sensor calibration
    ├── parser.py            parse_stm32_adc_payload — JSON → {sid: float}
    └── session_samples.py   SessionSampleWriter — opt-in raw archive
```

---

## 1. Top-level files

### `__init__.py`

```python
__version__ = importlib.metadata.version("hermes")
```

Reads from installed package metadata so it never drifts from
`pyproject.toml`. Falls back to `"0.0.0+unknown"` for non-installed
checkouts. Surfaced by `/api/system-tunables` and the FastAPI app
metadata.

### `config.py`

`pydantic_settings.BaseSettings` subclass `Settings`. Every operator-
level configuration knob that's read at process start lives here.
Loaded once per process via `@lru_cache get_settings()`.

Field groups:

| Group | Fields | Used by |
|-------|--------|---------|
| Database | `database_url`, `migrate_database_url` | `db.engine`, alembic-style `db-migrate.sh` |
| API server | `hermes_api_host/port/workers/log_level` | `hermes.api.__main__` |
| Auth | `hermes_jwt_secret`, `hermes_jwt_expiry_seconds` | `auth.jwt`, `auth.secret_box` |
| MQTT | `mqtt_host/port/username/password`, `mqtt_topic_adc`, `mqtt_topic_events_prefix` | `ingest.main`, `detection.mqtt_sink` |
| SMTP / OTP | `smtp_*`, `otp_*`, `allowed_emails_path` | `auth.email`, `auth.otp` |
| Observability | `hermes_log_format`, `hermes_metrics_*` | `logging`, `metrics`, `api.routes.metrics` |
| Ingest | `live_buffer_max_samples`, `mqtt_drift_threshold_s`, `event_ttl_seconds` | `ingest.live_data`, `ingest.clock`, `detection.ttl_gate` |
| Multi-shard (Layer 3) | `hermes_ingest_mode`, `hermes_shard_count`, `hermes_shard_index` | `ingest.main`, manager-aware code |
| Dev | `hermes_dev_mode` | `api.deps` (auth bypass) |

A `model_validator` guards shard-math invariants (count ≥ 1, index in
range, mode='shard' implies count > 1) so a misconfigured deployment
fails fast at process start.

Full reference: [`CONFIGURATION.md`](./CONFIGURATION.md).

### `logging.py`

`structlog` wiring. Two output modes:

- `json` (production) — newline-delimited JSON for log aggregators
  (Loki, Elastic, journald-with-json-formatter).
- `console` (dev) — human-readable timestamps + key-value pairs.

`get_logger(__name__, component=...)` is the only API the rest of the
codebase uses. The `component` kwarg becomes a top-level field
(`component=ingest|api|detection|auth|modbus`) so log queries can
filter by subsystem without parsing the module name.

`configure_logging()` is called once at process start by both
`api.__main__` and `ingest.__main__`.

### `metrics.py`

Prometheus counters / gauges / histograms on the default registry.
The full list is in [`METRICS.md`](./METRICS.md). Key API:

| Name | Used for |
|------|----------|
| `MSGS_RECEIVED_TOTAL{device_id}` | inbound message count |
| `MSGS_INVALID_TOTAL` | JSON decode failures |
| `SAMPLES_PROCESSED_TOTAL{device_id}` | per-sensor reading count |
| `EVENTS_DETECTED_TOTAL{event_type, device_id}` | post-detection, pre-sink |
| `EVENTS_PERSISTED_TOTAL{event_type}` | DB-write success |
| `EVENTS_PUBLISHED_TOTAL{event_type}` | MQTT-publish success |
| `CONSUME_QUEUE_DEPTH` (gauge) | asyncio.Queue lag |
| `DB_WRITER_PENDING` (gauge) | DbEventSink internal queue |
| `MQTT_CONNECTED` (gauge) | 1 / 0 |
| `STAGE_DURATION{stage}` (histogram) | per-stage latency, sampled 1/100 |
| `SESSION_SAMPLES_*` | gap 6 writer counters/gauges |
| `MODBUS_*` | gap 7 poller counters/gauges |

Helper `time_stage("name")` is a context manager that records into
`STAGE_DURATION` once per N invocations (default 1/100). Cheap enough
to wrap every per-sample stage in `_consume`.

Three test helpers (`counter_value`, `gauge_value`, `histogram_count`)
read via the public `.collect()` API rather than touching private
internals — survives prometheus_client upgrades.

---

## 2. `api/` — FastAPI app

### `api/__main__.py`

```python
uvicorn.run(create_app, ...)
```

Entry point of the `hermes-api` console script. Reads host/port/
workers/log_level from `Settings`. Doesn't reload code in production;
that's a Vite-side concern in dev.

### `api/main.py` — `create_app()` factory + lifespan

`lifespan` is the FastAPI startup/shutdown context manager:

```
startup
  ├── configure_logging()
  ├── settings = get_settings()
  ├── ensure_default_session() ──► (session_id, package_id)
  ├── DbConfigProvider(package_id).reload()
  ├── IngestPipeline(settings, session_id, config_provider).start()
  └── app.state.{live_data, ingest_pipeline, config_provider} populated

shutdown
  ├── pipeline.stop() — drain queue, flush TTL gate, close MQTT/DB
  └── dispose_engine()
```

Failures in any startup step are caught and logged; the API still
serves `/api/health` so an outage in (say) MQTT doesn't blackhole HTTP.

`create_app()` registers all routers explicitly (no auto-discovery)
so route prefixes are greppable from one place. Current set:

```
/api/health           — public, no auth
/api/auth/*           — public; issues OTPs + JWTs
/api/devices/*        — auth required
/api/events/*         — auth required
/api/config/*         — auth required (commits + emits NOTIFY)
/api/mqtt-brokers/*   — auth required
/api/packages/*       — auth required
/api/sessions/*       — auth required
/api/system-tunables  — auth required (read-only)
/api/devices/{id}/offsets/* — auth required
/api/live_stream/{id} — public for now (auth lands when JWT flow finishes)
/api/metrics          — public; firewall/nginx in front in prod
```

CORS is intentionally NOT enabled. Production runs UI same-origin via
nginx; Vite dev server proxies `/api` to FastAPI port.

### `api/deps.py` — FastAPI dependencies

Two reusable dependencies:

```python
DbSession   = Annotated[AsyncSession, Depends(_db_session)]
CurrentUser = Annotated[User, Depends(_current_user)]
```

`_db_session` opens an `async_session()` per request and closes it on
return. `_current_user` checks the JWT bearer token; in `dev_mode`
returns a stub user so integration tests don't need a real OTP flow.

### `api/routes/` — one file per resource

Each file exports `router = APIRouter()`. Conventions:

- Pydantic input models named `XIn` / `XPatch` / `XStop` etc.
- Pydantic output models named `XOut`.
- `_get_or_404(session, id)` per file — returns row or raises 404.
- Validation errors produce 422 (FastAPI default).
- Constraint conflicts produce 409 (caught from `IntegrityError`).
- Mutating routes commit explicitly before returning.

| File | Surface | Notable behaviour |
|------|---------|-------------------|
| `auth.py` | `POST /login`, `POST /verify`, `POST /logout` | Email allowlist gate, argon2 OTP, rate limits |
| `config.py` | `GET/PUT /config/{type}/{scope}/...` | `_commit_and_reload()` then `pg_notify('hermes_config_changed', package_id)` |
| `devices.py` | CRUD + `is_active` toggle | `device_id` 1..999 operator-assigned, not auto |
| `events.py` | List, get, get window, export CSV/NDJSON | Streaming export; window decoded via `encoding.py` |
| `health.py` | `GET /health` | Lightweight liveness, no DB hit |
| `live_stream.py` | `GET /live_stream/{device_id}` | SSE `EventSource` semantics, 100 ms tick, 500-sample cap |
| `metrics.py` | `GET /metrics` | `prometheus_client.generate_latest()` text format |
| `mqtt_brokers.py` | CRUD + `POST /{id}/activate` | Atomic active-row swap; password Fernet-encrypted |
| `offsets.py` | Per-device GET / PUT (single + bulk) / DELETE | 12 sensors, defaults to 0.0 |
| `packages.py` | List, get, create, clone | Clone copies every parameter row + sets `parent_package_id` |
| `sessions.py` | List, get, current, start, stop, logs | Triggers cascade close + lock package |
| `system_tunables.py` | `GET /system-tunables` | Read-only state + tunables list with editability |

Shape detail in [`../design/REST_API.md`](../design/REST_API.md).

---

## 3. `auth/` — security primitives

### `auth/jwt.py`

HS256 with `Settings.hermes_jwt_secret` (32+ byte SecretStr). Default
expiry 1 h. `issue(user)` returns the token; `verify(token)` returns
the claims dict or raises. Rotating the secret invalidates every
active session by design.

### `auth/otp.py`

Six-digit numeric OTP. `secrets.choice` for entropy. argon2-cffi for
storage hash. Per-user rate limits enforced from `Settings.otp_*`:

- `otp_expiry_seconds` (default 300) — rejected if older.
- `otp_max_attempts` (default 5) — bumps `attempt_count` on each verify.
- `otp_resend_cooldown_seconds` (default 60) — minimum gap between issues.
- `otp_max_per_hour` (default 5) — sliding hour window.

### `auth/email.py`

`aiosmtplib` async send. If SMTP env vars are blank, falls back to a
no-op that just logs the OTP — useful for local dev where you don't
want a real SMTP server.

### `auth/allowlist.py`

Reads `Settings.allowed_emails_path` (default `./config/allowed_emails.txt`)
on every login attempt. Prevents account creation by random email
addresses; HERMES has no public sign-up flow.

### `auth/secret_box.py`

`cryptography.Fernet` (AES-128-CBC + HMAC-SHA256). Used for at-rest
encryption of operator-typed secrets — currently the MQTT broker
password (gap 4). Fernet key is derived from `HERMES_JWT_SECRET` via
HKDF-SHA256 with a domain separator so a leaked Fernet key cannot
forge JWTs and vice versa.

```
HERMES_JWT_SECRET ──► HKDF-SHA256(salt="hermes/secret_box/v1",
                                  info="hermes:secret_box.v1",
                                  length=32)
                  ──► base64url-encoded Fernet key
                  ──► Fernet(key) → encrypt/decrypt
```

Rotating `HERMES_JWT_SECRET` invalidates JWTs AND requires re-entry
of stored broker passwords. Same "reset everything" mental model.

---

## 4. `db/` — SQLAlchemy + ORM models

### `db/engine.py`

```python
engine = create_async_engine(settings.database_url, ...)

@asynccontextmanager
async def async_session(): ...

async def dispose_engine(): ...
```

Single shared engine across the process. asyncpg dialect under the
hood. Pool size unset = SQLAlchemy default (5 + overflow); the
session-samples writer holds its OWN dedicated asyncpg connection
because `copy_records_to_table` needs the raw API.

### `db/models.py`

Mirror of `migrations/0002_core_tables.sql`. Hand-maintained — the
SQL is the source of truth, models follow. The `_pg_enum()` helper
bridges Python `StrEnum` (uppercase NAME) to Postgres enums (lowercase
values). Full schema reference in
[`../design/DATABASE_SCHEMA.md`](../design/DATABASE_SCHEMA.md).

Quick lookup of class → table:

```
Device           → devices
Package          → packages
Parameter        → parameters
Session          → sessions
SessionLog       → session_logs
Event            → events                (TimescaleDB hypertable)
EventWindow      → event_windows         (TimescaleDB hypertable)
SessionSample    → session_samples       (TimescaleDB hypertable)
SensorOffset     → sensor_offsets
User             → users
UserOtp          → user_otps
MqttBroker       → mqtt_brokers
```

---

## 5. `detection/` — the event detection pipeline

### `detection/types.py`

Three frozen dataclasses + two Protocols:

```
Sample(ts, device_id, sensor_id, value)
DetectedEvent(event_type, device_id, sensor_id, triggered_at, metadata)

class SensorDetector(Protocol):
    def feed(sample) -> DetectedEvent | None
    def reset() -> None

class EventSink(Protocol):
    def publish(event) -> None
```

Everything in `detection/` either implements one of these protocols or
composes implementations of them.

### `detection/config.py`

Dataclasses for each detector's tunable parameters:

| Class | Key parameters |
|-------|----------------|
| `TypeAConfig` | `T1`, `threshold_cv`, `debounce_seconds`, `init_fill_ratio`, `expected_sample_rate_hz` |
| `TypeBConfig` | `T2`, `lower_threshold_pct`, `upper_threshold_pct`, ditto |
| `TypeCConfig` | `T3`, `threshold_lower`, `threshold_upper`, ditto |
| `TypeDConfig` | `T4`, `T5`, `tolerance_pct`, ditto |
| `ModeSwitchingConfig` | `enabled`, `startup_threshold`, `break_threshold`, `startup_duration_seconds`, `break_duration_seconds`, `startup_reset_grace_s` |

Plus `DetectorConfigProvider` Protocol (5 methods, one per type +
`mode_switching_for`) and `StaticConfigProvider` (constant-config
implementation used in tests).

### `detection/db_config.py`

`DbConfigProvider` — production implementation. Holds three caches:

```
_global  : _ConfigCache         ← parameters with scope=GLOBAL
_devices : dict[int, _ConfigCache]  ← parameters with scope=DEVICE
_sensors : dict[(int, int), _ConfigCache]  ← parameters with scope=SENSOR
```

`type_X_for(device_id, sensor_id)` walks SENSOR → DEVICE → GLOBAL
returning the first hit. `reload()` refetches every parameter row for
the package. Plus the LISTEN/NOTIFY plumbing (Layer 3, alpha.15):

```
start_listener(dsn, engine)
  ├── opens dedicated asyncpg.connect(dsn)
  ├── add_listener('hermes_config_changed', _on_notify)
  └── stores reference to the engine for reset

_on_notify(_, _, _, payload)
  └── if payload == str(self._package_id):
          asyncio.create_task(_reload_and_reset())

_reload_and_reset()
  ├── reload() — refetch parameter rows
  └── engine.reset_device(device_id) for each cached device
```

### `detection/engine.py`

`DetectionEngine` — fans samples to per-(device, sensor, type) detectors.

```
for sensor_id, value in values.items():
    decision = mode_machine.feed(device_id, sensor_id, value, ts)
    if decision.break_event:
        sink.publish(decision.break_event)

    sample = Sample(...)
    for event_type in (A, B, C, D):
        if not decision.active and event_type is not A:
            continue
        detector = self._detector_for(device_id, sensor_id, event_type)
        event = detector.feed(sample)
        if event is None:
            continue
        if not decision.active:  # Type A only
            continue  # window stays primed but suppressed
        sink.publish(event)
```

Type A always feeds (window stays warm during POWER_ON / BREAK); B/C/D
are skipped while inactive. Type D depends on Type C's `current_avg`
for the same sample, so detection order is fixed (`_EVENT_TYPE_ORDER`).

`reset_device(device_id)` drops all cached detectors for that device
AND resets the mode state machine. Called on config reload.

### `detection/mode_switching.py`

`ModeStateMachine` (gap 3, alpha.17). Per-(device, sensor) tracking of
POWER_ON / STARTUP / BREAK. `feed(...)` returns a `ModeDecision`:

```
ModeDecision(active: bool, break_event: DetectedEvent | None)
```

Fast path (mode switching disabled by default): returns the
pre-allocated `_DECISION_ACTIVE` singleton — no allocation, no state.

When enabled: STARTUP → BREAK on sustained `value < break_threshold`
emits a BREAK event with `triggered_at` = the FIRST below-threshold
sample's wall time (NOT the duration boundary — operators have alarms
wired to that earlier timestamp). See [`EVENTS.md`](./EVENTS.md) §6.

### `detection/sliding.py`

`IncrementalSlidingWindow` — shared by the four detectors. Maintains
running sum + sum-of-squares in O(1) per sample so variance / mean
queries are constant-time even on a 30-second window at 100 Hz.

```
push(ts, value):
    while window_deque[0].ts < ts - T:
        old = window_deque.popleft()
        running_sum    -= old.value
        running_sum_sq -= old.value²
        window_count   -= 1
    window_deque.append((ts, value))
    running_sum    += value
    running_sum_sq += value²
    window_count   += 1
```

Plus an `init_fill_ratio` (default 0.9) — detectors don't fire until
the window is at least this fraction full of samples, so a startup
burst doesn't produce false-positives from a half-warmed average.

### `detection/type_a.py` … `type_d.py`

The four detectors. Each implements `SensorDetector`. Detailed
mechanics in [`EVENTS.md`](./EVENTS.md). Quick summary:

| Detector | Window length | Fires when |
|----------|---------------|------------|
| Type A | `T1` (default 1.0 s) | `CV%(t) > threshold_cv` |
| Type B | `T2` (default 5.0 s) | latest sample outside avg_T2 ± `tolerance_pct` |
| Type C | `T3` (default 10.0 s) | `avg_T3 < lower OR avg_T3 > upper` |
| Type D | `T4` (10) + `T5` (30) | `avg_T3` outside `avg_T5 ± tolerance_pct` |

All four reset state on a `> data_gap_reset_s` (default 2.0 s) gap
between samples — see `IncrementalSlidingWindow.gap_reset()`.

### `detection/sink.py`, `mqtt_sink.py`, `db_sink.py`, `ttl_gate.py`

Sink chain (gap 1 → gap 2):

```
DetectionEngine ──► TtlGateSink ──► MultiplexEventSink ──┬──► DbEventSink
                                                          │
                                                          └──► MqttEventSink
```

- **`sink.py`**: `LoggingEventSink` (no-op logger) and
  `MultiplexEventSink` (fan-out with per-child failure isolation).
- **`mqtt_sink.py`**: paho-mqtt-backed publish to
  `stm32/events/<dev>/<sid>/<TYPE>`. Idempotent attach/detach so the
  ingest can connect/reconnect without losing the sink.
- **`db_sink.py`**: queues events; background `_writer_loop` waits 9 s
  past `triggered_at`, slices `EventWindowBuffer`, writes
  `events`+`event_windows` rows in one transaction.
- **`ttl_gate.py`**: 4-rule dedup gate with BREAK bypass and `flush()`
  on shutdown. Documented in [`WORKFLOW.md`](./WORKFLOW.md) §7.

### `detection/window_buffer.py`

`EventWindowBuffer` — 30 s ring buffer per device, written by
`_consume`, read by `DbEventSink` to slice ±9 s windows. Uses the same
`SensorSnapshot` shape as `LiveDataHub` but with a longer retention.

### `detection/encoding.py`

`encode_window` / `decode_window`. Today's implementation is JSON
(`encoding="json-utf8"` stored on the row). The contract anticipates
swapping to `zstd+delta-f32` for ~100× smaller BLOBs; the `encoding`
column makes the transition non-breaking — old rows still decode via
the JSON path.

### `detection/session.py`

`ensure_default_session()` — bootstrap helper used by both API and
ingest at process start. Idempotent: finds the active GLOBAL session
or creates one. Returns `(session_id, package_id)`.

This is the seam between "fresh deployment" and "session lifecycle
managed by the operator via /api/sessions" — the bootstrap creates a
session with `started_by="ingest-bootstrap"` if none exists.

---

## 6. `ingest/` — MQTT consumer + Modbus poller

### `ingest/__main__.py`

```python
asyncio.run(run())
```

`run()` is in `main.py`. SIGTERM handler signals stop; pipeline drains
gracefully. Console script `hermes-ingest` declared in `pyproject.toml`.

### `ingest/main.py`

Most-touched file in the codebase. Three top-level objects:

1. **`_consume(...)` coroutine** — the hot loop. Pre-binds attribute
   lookups to locals (Layer 1 perf), drops per-sample log, applies
   shard filter, runs the parse → anchor → offset → buffers → detect
   chain. See [`WORKFLOW.md`](./WORKFLOW.md) §4.

2. **`IngestPipeline` class** — owns the asyncio.Queue, the paho client,
   the consumer task, and references to all the singletons:
   - `live_data: LiveDataHub`
   - `window_buffer: EventWindowBuffer`
   - `offset_cache: OffsetCache`
   - `_clocks: ClockRegistry`
   - `session_sample_writer: SessionSampleWriter | None`
   - `modbus_manager: ModbusManager | None`
   - `_db_sink: DbEventSink | None`
   - `mqtt_event_sink: MqttEventSink | None`
   - `ttl_gate: TtlGateSink | None`
   - `detection_engine: DetectionEngine | None`

   Construction respects the `hermes_ingest_mode`: in `live_only` mode
   (multi-shard API process) detection-side components are None.

3. **`run()` async function** — standalone entry. Bootstraps default
   session + config provider, constructs IngestPipeline, starts it,
   subscribes to `hermes_config_changed` via the provider's listener,
   waits for SIGTERM.

### `ingest/clock.py`

`ClockRegistry` — per-device offset table. Anchors STM32 ms-since-boot
to wall time; re-anchors on drift. See [`WORKFLOW.md`](./WORKFLOW.md)
§4 for the math.

### `ingest/offsets.py`

`OffsetCache` — `dict[device_id, dict[sensor_id, offset_value]]`.
`apply(device_id, sensor_values)` subtracts the offset per sensor.
Loaded at start from `sensor_offsets` table; updated on
`PUT /api/devices/<id>/offsets/...`.

### `ingest/parser.py`

`parse_stm32_adc_payload(payload)` — converts the `{adc1: [...],
adc2: [...]}` structure to a flat `{sensor_id: float}` dict for
sensors 1..12. Single function, ~30 LOC, frequently changes shape so
keep the tests at `tests/unit/test_parser.py` aligned.

### `ingest/live_data.py`

`LiveDataHub` — per-device `deque(maxlen=N)` of `SensorSnapshot`. API:

| Method | Purpose |
|--------|---------|
| `push(device_id, ts, values)` | Append a snapshot; called from `_consume` |
| `since(device_id, after_ts)` | Snapshots newer than cursor; SSE drains |
| `latest_ts(device_id)` | Most-recent timestamp; UI initial state |
| `devices()` | Current device IDs with data |

### `ingest/session_samples.py`

`SessionSampleWriter` (gap 6, alpha.20). Hot-path is a single dict
lookup + early return. Background tasks refresh recording set every
5 s, flush buffer every 1 s via `copy_records_to_table`. Detailed in
[`WORKFLOW.md`](./WORKFLOW.md) §5.3.

### `ingest/modbus.py`

`ModbusManager` + `ModbusPoller` (gap 7, alpha.21). Manager polls the
DB every 5 s for `protocol=modbus_tcp + active` devices and reconciles
the in-memory poller set. Each poller owns an `AsyncModbusTcpClient`
and reads `register_count` input registers every `poll_interval_ms`.

The downstream callback (`IngestPipeline._on_modbus_snapshot`) re-runs
offset correction → live + window buffers → detection → sample writer
— **the same downstream chain MQTT samples take**. Operators can mix
sources on the same detection thresholds.

`ModbusConfig` (pydantic) validates the `modbus_config` JSONB on
device-row create/update.

---

## 7. Where each gap landed in the file tree

Cross-reference for "alpha.X added what":

```
alpha.11  outbound MQTT      detection/mqtt_sink.py, detection/sink.py (Multiplex)
alpha.12  metrics + bench    metrics.py, api/routes/metrics.py, tests/bench/
alpha.13  TTL gate           detection/ttl_gate.py
alpha.14  Layer 1 perf       (ingest/main.py + detection/encoding.py + mqtt_sink.py
                              swap to orjson; pre-bound locals; drop debug log)
alpha.15  Layer 3 shard      ingest/main.py (mode + filter), detection/db_config.py
                             (LISTEN), api/routes/config.py (NOTIFY emit),
                             config.py (shard fields), packaging/systemd/*
alpha.16  doc overhaul       README.md, docs/design/ARCHITECTURE.md
alpha.17  mode switching     detection/mode_switching.py + engine.py wiring +
                             config.py ModeSwitchingConfig
alpha.18  MQTT broker UI     api/routes/mqtt_brokers.py, auth/secret_box.py,
                             ui/src/routes/mqtt-brokers/
alpha.19  sessions UI        api/routes/sessions.py + packages.py,
                             ui/src/routes/sessions/
alpha.20  session_samples    ingest/session_samples.py + IngestPipeline wiring
alpha.21  Modbus TCP         ingest/modbus.py + IngestPipeline wiring
alpha.22  /settings          api/routes/system_tunables.py, ui/src/routes/settings/
alpha.23  golden harness     tests/golden/
alpha.24  CI hotfix          .gitattributes (un-LFS synthetic corpora)
```

Each release's CHANGELOG entry has the per-file diff summary.

---

## 8. Concurrency and threading rules

**One asyncio loop per process.** Almost all HERMES code lives on it.
Two exceptions:

1. **paho-mqtt callback thread** (background) — runs `on_message`. The
   only thing it does is `loop.call_soon_threadsafe(queue.put_nowait,
   ...)`. Never touch shared state from this thread.
2. **DbEventSink writer** — its own `asyncio.Task` on the same loop.
   Doesn't share mutable state with `_consume`; communicates via an
   `asyncio.Queue`.

**No locks.** All state mutations happen on the asyncio loop, which is
single-threaded by construction. The `LiveDataHub`, `EventWindowBuffer`,
`OffsetCache`, `ClockRegistry`, and detection caches are all
loop-local — no `threading.Lock`, no `asyncio.Lock`. Add a lock only
if you're crossing a thread boundary (and you almost never should).

**Background tasks** — created via `asyncio.create_task(...)`. Each is
named (the `name=` kwarg) so `asyncio.all_tasks()` is greppable in
debugging:

| Task name | Owner | Lifetime |
|-----------|-------|----------|
| `mqtt-consumer` | `IngestPipeline` | start → stop |
| `db-event-writer` | `DbEventSink` | start → stop |
| `session-samples-refresh` | `SessionSampleWriter` | start → stop |
| `session-samples-flush` | `SessionSampleWriter` | start → stop |
| `modbus-manager-refresh` | `ModbusManager` | start → stop |
| `modbus-poll-{device_id}` | `ModbusPoller` | manager lifecycle |
| `config-reload` | `DbConfigProvider._on_notify` | one-shot per NOTIFY |

---

## 9. How to add a new feature without breaking things

A checklist that the rewrite has internalised:

1. **Read the contract.** If touching detection, `EVENT_DETECTION_CONTRACT.md`.
   If touching the schema, `DATABASE_CONTRACT.md`. If touching the
   wire format, `HARDWARE_INTERFACE.md`.
2. **Decide if it's a divergence.** If yes, log it in
   `BUG_DECISION_LOG.md` BEFORE writing code (`PRESERVE` / `FIX` /
   `FIX+FLAG`).
3. **Migration first.** Schema change? `0NNN_<slug>.sql` in `migrations/`
   plus a matching SQLAlchemy model update.
4. **Settings first.** New env var? `Settings` field with description
   + a row in [`CONFIGURATION.md`](./CONFIGURATION.md).
5. **Metric first.** New observable? `metrics.py` entry + a row in
   [`METRICS.md`](./METRICS.md).
6. **Tests cover it.** Unit (no I/O), integration (real DB), maybe
   golden if it touches detection output.
7. **Doc the same release.** Update README gap table + relevant guide.
   Don't ship a feature whose doc lands the next sprint.

---

## 10. Known places where the code is non-obvious

For people scanning the codebase: these spots have rationale that's
worth reading the docstring before changing.

| File | Why look |
|------|----------|
| `ingest/main.py:_consume` | 14 pre-bound locals at the top. Don't refactor without bench |
| `detection/ttl_gate.py:publish` | Order of rule checks matters — preempt comes after dedup |
| `detection/db_sink.py:_writer_loop` | 9 s wait is intentional. The legacy contract requires it |
| `detection/db_config.py:start_listener` | Dedicated asyncpg connection (NOT the SQLAlchemy pool) |
| `auth/secret_box.py:_box` | `lru_cache(maxsize=1)` — Fernet construction is HMAC schedule |
| `api/routes/config.py:_commit_and_reload` | Commit MUST precede reload (provider opens fresh session) |
| `db/models.py:_pg_enum` | `values_callable` bridges StrEnum NAME→value |
| `tests/integration/conftest.py:_reset_schema` | TRUNCATE-not-DROP-SCHEMA (Timescale ext loaded once) |

Each of those has an in-file docstring explaining the why; ARCHITECTURE.md
§9 ("Things that look weird but are correct") has additional examples.
