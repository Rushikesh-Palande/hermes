# ARCHITECTURE.md — HERMES rewrite, end-to-end

> **Audience:** a developer who has cloned the repo and read the
> [`README.md`](../../README.md), and now wants to actually change
> something. By the end of this document you'll know which files to
> open for any common task, what each component owns, and what the
> tricky invariants are.
>
> **Companion docs:**
> - [`docs/contracts/`](../contracts/) — what the **legacy** system
>   does (frozen, append-only)
> - [`docs/design/DATABASE_REDESIGN.md`](./DATABASE_REDESIGN.md) — the
>   rewrite's data model
> - [`docs/design/MULTI_SHARD.md`](./MULTI_SHARD.md) — Layer 3 horizontal
>   scaling
> - [`README.md`](../../README.md) — the entry point: status, quick
>   start, configuration reference

---

## 1. Design philosophy

### 1.1 What the rewrite is for

The legacy HERMES (SQLite + Flask, in `/home/embed/hammer/`) is
production code that works. The rewrite exists to:

1. **Replace SQLite with TimescaleDB** so historical queries don't lock
   the writer. Continuous aggregates make minute/hour rollups cheap.
2. **Replace Flask with FastAPI + asyncio** so SSE, MQTT, and HTTP can
   all share one event loop without thread juggling.
3. **Replace the Jinja UI with SvelteKit** so the dashboard feels like
   2026, not 2014.
4. **Document every legacy invariant before changing it**, so we don't
   silently regress operators who rely on a 12-year-old behaviour.

The rewrite is NOT a fresh rewrite-from-the-Wikipedia-article. Detector
output is byte-identical to the legacy system except where
[`BUG_DECISION_LOG.md`](../contracts/BUG_DECISION_LOG.md) explicitly
records a divergence with rationale. This is enforced by the golden
traffic harness.

### 1.2 Operating constraints

| Constraint                         | Implication                                                         |
| ---------------------------------- | ------------------------------------------------------------------- |
| **Pi 4, 2 GB RAM, 4 cores**        | Memory and CPU budgets are tight. No JVM. No Docker on the Pi.      |
| **20 devices × 12 sensors × 100 Hz** | 2 000 msg/s = 24 000 readings/s = 96 000 detector updates/s.      |
| **24×7 unattended operation**       | Restart-policy correctness, log rotation, observability matter more than they would in a SaaS app. |
| **Industrial network**              | Mosquitto local; broker is on the same Pi. No cloud round-trips.   |
| **Operator is not a sysadmin**      | systemd, journald, and a one-page operator runbook. No kubectl.    |

### 1.3 Non-goals

- Multi-tenant. One HERMES install serves one factory floor.
- Horizontal scale across machines. The Pi is the unit of deployment.
  (Multi-process within the Pi is supported via Layer 3.)
- Web-scale anything. This is industrial monitoring at 100 Hz, not
  100 kHz.

---

## 2. Component map

```
                          ┌────────────────┐
                          │     STM32      │   firmware: 12-channel ADC
                          │   ~100 Hz      │   publishes JSON over MQTT
                          └────────┬───────┘   every 10 ms
                                   │ stm32/adc
                                   ▼
                          ┌────────────────┐
                          │   Mosquitto    │   broker on the Pi
                          └────────┬───────┘
                                   │
                ┌──────────────────┴──────────────────┐
                │                                     │
                ▼                                     ▼
       ┌────────────────┐                   ┌────────────────┐
       │ hermes-ingest  │                   │   hermes-api   │
       │ (one process,  │                   │    (FastAPI)   │
       │  detection +   │                   │                │
       │  durable sinks)│                   │                │
       └────────┬───────┘                   └────────┬───────┘
                │                                    │
                │  events + windows                  │  HTTP / SSE
                │  parameters / sessions             │
                ▼                                    ▼
       ┌────────────────────────────────────────────────────┐
       │              PostgreSQL + TimescaleDB              │
       │  hypertables: events, event_windows                │
       │  regular:     packages, sessions, parameters,      │
       │               sensor_offsets, devices, users       │
       └────────────────────────────────────────────────────┘
                                   ▲
                                   │
                          ┌────────┴───────┐
                          │   nginx (TLS)  │   reverse-proxy
                          └────────┬───────┘
                                   │
                          ┌────────▼───────┐
                          │   Browser      │   SvelteKit + uPlot
                          └────────────────┘
```

In multi-shard mode (Layer 3, opt-in), `hermes-ingest` becomes 4
processes and the API runs in `live_only` mode. See
[`MULTI_SHARD.md`](./MULTI_SHARD.md).

---

## 3. The `hermes-ingest` process

This is the hot path. **If you only have time to understand one
process, understand this one.**

### 3.1 Threading model

```
paho-mqtt internal network thread (background, daemon)
    │ on_message()
    │   - reads bytes from socket
    │   - records receive_ts = time.time()
    │   - asyncio.Queue.put_nowait via loop.call_soon_threadsafe
    │
    ▼  ─── thread boundary ───
asyncio.Queue (MPSC, asyncio-native)
    │
    ▼
asyncio event loop (single thread, single task)
    │
    └── _consume() coroutine, see services/hermes/ingest/main.py
```

The paho thread does the absolute minimum: read, timestamp, hand off.
**No JSON parsing, no logging, no detection** runs on the paho thread —
the asyncio event loop owns all of that. This was a hard-learned lesson
from the legacy system and is enforced in code.

### 3.2 The `_consume` coroutine

`services/hermes/ingest/main.py:_consume`. This is the single function
that makes HERMES tick. Per message:

1. **Parse** — `orjson.loads(raw_bytes)`. orjson, not stdlib json.
2. **Shard filter** (Layer 3) — drop if `device_id % shard_count != shard_index`.
3. **Anchor timestamp** — `ClockRegistry.anchor` converts the STM32
   counter to wall time. Re-anchors if drift > `MQTT_DRIFT_THRESHOLD_S`.
4. **Apply offsets** — `OffsetCache.apply` subtracts per-sensor
   calibration offsets.
5. **Push to live ring buffer** — `LiveDataHub.push`. The SSE
   endpoint reads from here; this is what the operator sees in
   real-time graphs.
6. **Push to window buffer** — `EventWindowBuffer.push_snapshot`. A
   30 s ring per device. When an event fires, the DB sink slices ±9 s
   around `triggered_at` and writes that to `event_windows`.
7. **Feed detection** (skipped in live_only mode) —
   `DetectionEngine.feed_snapshot`. Each of the 4 detector types
   updates its incremental sliding-window state and may emit a
   `DetectedEvent`.

Detected events flow:

```
DetectionEngine
    │ publish(DetectedEvent)
    ▼
TtlGateSink           dedup + priority + BREAK bypass; holds for 5 s
    │ publish (after TTL elapses)
    ▼
MultiplexEventSink    fans out per-child failure-isolated
    ├── DbEventSink        async writer task; persists events + windows
    └── MqttEventSink      publishes to stm32/events/<dev>/<sid>/<TYPE>
```

### 3.3 Hot-path discipline

Layer 1 (alpha.14) hardened the per-sample path:

- **`orjson` over `json`** — 3-5× faster on small JSON.
- **Pre-bound locals** — `LOAD_FAST` over `LOAD_GLOBAL+LOAD_ATTR` for
  every metric counter, `queue.get`, `parse_payload`, etc.
- **No per-sample log** — Prom counters cover what `log.debug` would.
- **Sampled `time_stage`** — histograms record 1-in-N stage timings,
  not all of them.

If you add work to `_consume`, **bench it**. The bench in
`tests/bench/test_throughput.py` is the source of truth and runs in CI.

### 3.4 Files to know

| File                                                | Owns                                                              |
| --------------------------------------------------- | ----------------------------------------------------------------- |
| `services/hermes/ingest/main.py`                    | `_consume`, `IngestPipeline`, `run` entry point                   |
| `services/hermes/ingest/clock.py`                   | `ClockRegistry` — STM32-counter-to-wall-time anchoring            |
| `services/hermes/ingest/offsets.py`                 | `OffsetCache` — per-sensor calibration                            |
| `services/hermes/ingest/parser.py`                  | `parse_stm32_adc_payload` — JSON shape → `{sensor_id: float}`    |
| `services/hermes/ingest/live_data.py`               | `LiveDataHub` — per-device ring buffer for SSE                    |
| `services/hermes/ingest/session_samples.py`         | `SessionSampleWriter` — opt-in raw-archive writer (asyncpg COPY) |
| `services/hermes/ingest/modbus.py`                  | `ModbusManager` + `ModbusPoller` — async Modbus TCP polling       |

---

## 4. The detection engine

`services/hermes/detection/engine.py`. Owns the 4 detector types and
their per-(device, sensor) state.

### 4.1 The four detector types

Detailed in [`docs/contracts/EVENT_DETECTION_CONTRACT.md`](../contracts/EVENT_DETECTION_CONTRACT.md).
Quick reference:

| Type | Trigger                                                                   |
| ---- | ------------------------------------------------------------------------- |
| A    | Single-sample threshold cross with debounce.                              |
| B    | Sustained out-of-band band over a window (variance-style).                |
| C    | Rate-of-change above a threshold.                                         |
| D    | A complex pattern combining inner/outer bands and a settling window.      |

Each detector lives in its own module under
`services/hermes/detection/detectors/`. They share the
`Detector` protocol — `feed(timestamp, value) → list[DetectedEvent]`.

### 4.2 Per-device, per-sensor state

The engine holds a `dict[(device_id, sensor_id), DetectorBundle]`
where `DetectorBundle` carries one instance of each detector type.
State is reset (`reset_device(device_id)`) when:

- Operator updates thresholds via `PUT /api/config/...`
- A multi-shard config-changed `NOTIFY` arrives (Layer 3)
- A long data gap (`> data_gap_reset_s`) is detected on a sensor

### 4.2a Mode switching (gap 3, alpha.17)

A separate `ModeStateMachine`
([`services/hermes/detection/mode_switching.py`](../../services/hermes/detection/mode_switching.py))
tracks each (device, sensor) pair through three modes — `POWER_ON`,
`STARTUP`, `BREAK` — and gates A/B/C/D detection accordingly:

| Mode      | Type A             | Types B/C/D         | BREAK emission                  |
| --------- | ------------------ | ------------------- | ------------------------------- |
| POWER_ON  | feeds, fires SUPPRESSED | skipped entirely    | none                            |
| STARTUP   | runs normally      | run normally        | on sustained drop below `break_threshold` |
| BREAK     | feeds, fires SUPPRESSED | skipped entirely    | already emitted; recovery is silent |

`enabled=False` by default — every sensor is treated as STARTUP and
detection runs unconditionally, matching pre-alpha.17 behaviour.

The BREAK event's `triggered_at` is the FIRST below-threshold sample's
wall time, NOT the moment the duration elapsed. Operators have alarms
wired to that earlier timestamp; preserving it is a hard contract
invariant. `BREAK` events bypass the TTL gate (already implemented in
alpha.13) and flow straight to the durable sinks.

Implementation lives outside `DetectionEngine` because the state
machine has its own non-trivial state (six per-sensor timestamps + a
mode integer) and its own configuration object. Keeping it isolated
keeps the engine focused on detector routing and lets parity tests
focus on a single piece of behaviour. See
[`docs/contracts/EVENT_DETECTION_CONTRACT.md`](../contracts/EVENT_DETECTION_CONTRACT.md)
§2.3 and §7 for the legacy spec.

### 4.3 Configuration flow

```
parameters table  ◄── PUT /api/config/...  (writes per-package thresholds)
       │                       │
       │                       │ commit
       │                       ▼
       │              pg_notify('hermes_config_changed', package_id)
       │                       │
       │                       ▼   (each shard's LISTEN coroutine)
       └── DbConfigProvider.reload()
                  │
                  ▼
           DetectionEngine.reset_device(device_id) per cached device
```

In single-process deployments the API process commits, reloads its own
provider, resets its own engine — the `NOTIFY` is harmless. In
multi-shard, the same `NOTIFY` triggers reload + reset in every shard.

### 4.4 Files to know

| File                                                | Owns                                                              |
| --------------------------------------------------- | ----------------------------------------------------------------- |
| `services/hermes/detection/engine.py`               | `DetectionEngine` — fan-out per (device, sensor) per type, mode gating |
| `services/hermes/detection/mode_switching.py`       | `ModeStateMachine` — POWER_ON / STARTUP / BREAK + BREAK emission   |
| `services/hermes/detection/db_config.py`            | `DbConfigProvider` — DB-backed config + LISTEN                    |
| `services/hermes/detection/config.py`               | `TypeAConfig` etc. dataclasses + `StaticConfigProvider`           |
| `services/hermes/detection/sink.py`                 | `LoggingEventSink`, `MultiplexEventSink`                          |
| `services/hermes/detection/db_sink.py`              | `DbEventSink` — async writer with the 9 s post-window fence       |
| `services/hermes/detection/mqtt_sink.py`            | `MqttEventSink` — publishes detected events back over MQTT        |
| `services/hermes/detection/ttl_gate.py`             | `TtlGateSink` — Rule 1/2/3/4 + BREAK bypass                       |
| `services/hermes/detection/window_buffer.py`        | `EventWindowBuffer` — 30 s ring used by `DbEventSink`             |
| `services/hermes/detection/encoding.py`             | `encode_window` / `decode_window` — `event_windows.encoding`      |

---

## 5. The `hermes-api` process

FastAPI app. Reads from Postgres, serves the SvelteKit UI, exposes
`/api/*`. Optionally embeds an `IngestPipeline` so SSE has a live ring
buffer.

### 5.1 Lifespan

`services/hermes/api/main.py:create_app`. The `lifespan` async-context-
manager:

1. Validates `Settings` (fails fast on misconfig).
2. Bootstraps a default `Package` + `Session` if the DB is empty.
3. Creates a `DbConfigProvider` and reloads it.
4. Creates an `IngestPipeline` with that provider and starts it.
5. Stores `live_data` and the pipeline on `app.state` so SSE and config
   handlers can find them.
6. On shutdown: stops pipeline, disposes the SQLAlchemy engine.

### 5.2 Routes

Route modules live under `services/hermes/api/routes/`:

| Module             | Surface                                                                |
| ------------------ | ---------------------------------------------------------------------- |
| `auth.py`          | `/api/auth/login`, `/api/auth/verify`, `/api/auth/logout`              |
| `sessions.py`      | `/api/sessions/*` — start, stop, list, attach                          |
| `events.py`        | `/api/events/*` — list, detail, window, CSV/NDJSON export              |
| `offsets.py`       | `/api/devices/<id>/offsets` — calibration CRUD                         |
| `config.py`        | `/api/config/*` — parameter CRUD with `_commit_and_reload` + `NOTIFY`  |
| `mqtt_brokers.py`  | `/api/mqtt-brokers/*` — broker registry; one row active at a time      |
| `packages.py`      | `/api/packages/*` — config-package CRUD + clone (parameter-row copy)   |
| `sessions.py`      | `/api/sessions/*` — session lifecycle (start/stop), audit log, /current |
| `system_tunables.py` | `/api/system-tunables` — read-only system status + tunable knobs    |
| `live.py`          | `/api/live/sse` — Server-Sent Events from `LiveDataHub`                |
| `metrics.py`       | `/api/metrics` — Prometheus text-format exposition                     |
| `health.py`        | `/api/health` — basic liveness + DB ping                               |

### 5.3 Auth

`services/hermes/auth/`. JWT (HS256), 1 h default expiry. The OTP flow
hashes the 6-digit code with argon2-cffi before storing. While
`HERMES_DEV_MODE=1`, every protected route accepts a stub user — that's
the dev shim, not production behaviour.

`secret_box.py` (alpha.18) provides at-rest symmetric encryption for
operator-typed secrets — currently the MQTT broker password. Fernet
key is derived from `HERMES_JWT_SECRET` via HKDF-SHA256 with a domain
separator, so a leaked Fernet key cannot forge JWTs and vice versa.
Rotating the JWT secret invalidates every active session AND
necessitates re-entry of stored broker passwords — same "reset
everything" mental model.

### 5.4 SSE

`services/hermes/api/routes/live.py`. Subscribes to a single
`LiveDataHub` and streams snapshots to the connected client. The hub
itself is an in-memory ring buffer; SSE is the only way to get live
data out of the API process to the browser.

In multi-shard mode, the API still owns `live_data` because the API
runs as `live_only` — it subscribes to MQTT for ALL devices and skips
detection. That keeps SSE single-process even when detection is sharded.

---

## 6. The data layer

### 6.1 Schema

See [`docs/design/DATABASE_REDESIGN.md`](./DATABASE_REDESIGN.md) for the
full design rationale and
[`docs/contracts/DATABASE_CONTRACT.md`](../contracts/DATABASE_CONTRACT.md)
for the legacy contract we're matching.

Quick sketch:

```
packages
   ├── id (uuid pk)
   ├── name, description, created_at
   └── (immutable once a session uses it)

sessions
   ├── id (uuid pk)
   ├── package_id → packages.id
   ├── name, started_at, ended_at?
   └── one active session per device at a time

parameters
   ├── id, package_id → packages.id
   ├── key, value (jsonb), scope (GLOBAL|DEVICE|SENSOR)
   ├── device_id?, sensor_id?
   └── resolution: SENSOR > DEVICE > GLOBAL

events                     [hypertable, partitioned by triggered_at]
   ├── id, session_id → sessions.id
   ├── triggered_at, fired_at
   ├── event_type (A|B|C|D|BREAK)
   ├── device_id, sensor_id
   └── trigger_value, metadata (jsonb)

event_windows              [hypertable, partitioned by triggered_at]
   ├── event_id (1:1 with events)
   ├── window_start, window_end (= triggered_at ± 9 s)
   ├── encoding ("json-utf8" today; "zstd+delta-f32" planned)
   └── data (bytea — encoded sample list)

sensor_offsets
   ├── device_id, sensor_id (composite pk)
   └── offset_value (float)

devices, users  — operator-facing CRUD
```

Hypertables use Timescale chunking + retention policies (configured in
migration 0005). Compression policies are set up in migration 0003.

### 6.2 Migrations

`migrations/00NN_<slug>.sql`. **Append-only**. To remove a column,
write a new migration that DROPs it; never edit a past migration. The
test harness runs every migration once per session and `TRUNCATE`s
between tests (see `tests/integration/conftest.py` for the rationale —
Timescale extension state can't be torn down cleanly in the same
backend).

### 6.3 Models + access patterns

`services/hermes/db/models.py` — SQLAlchemy declarative. Postgres enums
are decoded with the `_pg_enum()` helper (`values_callable=lambda x:
[e.value for e in x]`) to handle the case mismatch between Postgres
lowercase and Python `Enum.NAME`.

The DB writer for events is in `services/hermes/detection/db_sink.py`.
Its 9 s post-window fence is the canonical example of "we wait
deliberately rather than write twice" — see the docstring there.

---

## 7. Tests, CI, and quality gates

### 7.1 Tiers

| Tier         | Path                  | Marker  | Run on                                  |
| ------------ | --------------------- | ------- | --------------------------------------- |
| Unit         | `tests/unit/`         | none    | every PR, every push                    |
| Integration  | `tests/integration/`  | `db`    | every PR (CI Postgres service)          |
| Bench        | `tests/bench/`        | `bench` | every PR (asserts no perf regression)   |
| Golden       | `tests/golden/`       | `golden`| (planned) every PR touching detection   |

### 7.2 Quality gate

Local pre-commit:

```bash
uv run ruff check services tests
uv run ruff format services tests
uv run mypy services
uv run pytest tests/unit -q
```

CI runs the integration and bench markers in addition. See
`.github/workflows/ci.yml`.

### 7.3 What "bench green" means

The bench (`tests/bench/test_throughput.py`) drains 2 000 synthetic MQTT
messages through `_consume` and asserts:

- **No drops** — every queued message is processed.
- **Wall-clock budget** — drain time stays under
  `DRAIN_BUDGET_SECONDS` (currently 6 s on a developer laptop, with
  enough headroom for CI runners).

Print line includes msg/s, samples/s. We track the number per release
in the [README's Performance section](../../README.md#performance).

---

## 8. Common tasks — where to look

| Task                                                  | Open                                                                          |
| ----------------------------------------------------- | ----------------------------------------------------------------------------- |
| Add a new MQTT-side field to the inbound payload      | `parser.py` + a migration if it should land in the DB                         |
| Tune a detector threshold                             | API: `PUT /api/config/...`. Code: `services/hermes/detection/config.py`      |
| Add a new HTTP route                                  | `services/hermes/api/routes/<area>.py`, registered in `services/hermes/api/main.py` |
| Add a new metric                                      | `services/hermes/metrics.py` + use `time_stage` or a counter in the call site |
| Change event-window encoding                          | `services/hermes/detection/encoding.py` (encoder + decoder + new `encoding` string) |
| Document a divergence from legacy                     | `docs/contracts/BUG_DECISION_LOG.md` — append, don't edit                     |
| Add a migration                                       | `migrations/00NN_<slug>.sql`. Run via `./scripts/db-migrate.sh`              |
| Tweak the systemd ops surface                         | `packaging/systemd/*.service`                                                 |
| Add a new perf optimisation                           | Bench first. If green, ship behind the existing layer convention.             |
| Wire a new sink into the detector output              | Subclass `EventSink`, add to the `MultiplexEventSink` list in `IngestPipeline.__init__` |

---

## 9. Things that look weird but are correct

### 9.1 `time_stage` is sampled, not always-on

`services/hermes/metrics.py:time_stage` records 1 in 100 invocations
into the histogram by default. At 24 000 stage entries/s, recording all
of them costs more than the work it's measuring. The sampling factor
is good enough for percentile estimates.

### 9.2 The TTL gate holds events instead of forwarding immediately

The 5 s hold is intentional — it lets duplicate same-type events on
the same sensor merge into one, lower-priority types get blocked while
a higher type is armed, and BREAK bypasses everything. See
[`EVENT_DETECTION_CONTRACT.md`](../contracts/EVENT_DETECTION_CONTRACT.md)
§8 for the legacy spec and
[`services/hermes/detection/ttl_gate.py`](../../services/hermes/detection/ttl_gate.py)
for the implementation.

### 9.3 The DB sink writes events 9 s AFTER they fire

Because we want to capture the ±9 s window around `triggered_at`, the
writer waits until `triggered_at + 9 s` to write — by then the post-
window samples are guaranteed to be in `EventWindowBuffer`. The
`fired_at` column captures the actual write time so the operator can
see the difference if they care.

### 9.4 `_consume` has 14 pre-bound locals at the top of the function

CPython's `LOAD_FAST` is several times cheaper than
`LOAD_GLOBAL`+`LOAD_ATTR`. At 2 000 msg/s on a Pi, that compounding
matters. Don't refactor those out unless the bench says you can.

### 9.5 The integration test conftest TRUNCATEs between tests instead of dropping the schema

`DROP SCHEMA public CASCADE` removes the `timescaledb` extension from
the catalog but leaves the shared library loaded in the same Postgres
backend session. The next `CREATE EXTENSION timescaledb` then errors
with "extension already loaded with another version". `TRUNCATE` keeps
the extension state intact and is much faster anyway. See
`tests/integration/conftest.py` docstring.

### 9.6 The dev compose maps Postgres on 5432, but you may need a different port if you have other Postgres containers

If port 5432 is taken on your dev machine, set
`POSTGRES_HOST_PORT=5433` in your `.env` and update `DATABASE_URL` /
`MIGRATE_DATABASE_URL` accordingly. The compose file binds whatever
the env says.

---

## 10. What to read next

Once you've got the lay of the land:

1. The legacy contract for the area you're touching — `docs/contracts/`.
2. The matching rewrite design doc — `docs/design/`.
3. The implementation file. Every file has a top-of-file rationale
   docstring; **read it**. Many subtle invariants are documented there
   instead of being hidden in commit messages.
4. The matching test file. Tests are the executable spec for what
   the code is supposed to do.

When in doubt, ask in a Discussion (not an Issue) — see
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) §8.
