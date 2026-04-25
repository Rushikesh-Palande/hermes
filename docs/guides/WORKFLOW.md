# WORKFLOW.md — end-to-end data flow

> **Audience:** anyone — dev, ops, or operator — who wants to understand
> what happens to a single sensor reading from the moment the STM32
> samples its ADC to the moment it shows up on the live chart, becomes
> an event row, and ends up in a CSV export.
>
> **Companion docs:**
> - [`BACKEND.md`](./BACKEND.md) — every Python service module
> - [`UI.md`](./UI.md) — every SvelteKit page
> - [`EVENTS.md`](./EVENTS.md) — detector mechanics
> - [`../design/ARCHITECTURE.md`](../design/ARCHITECTURE.md) — high-level architecture
> - [`../design/MULTI_SHARD.md`](../design/MULTI_SHARD.md) — multi-process scaling
> - [`../design/DATABASE_REDESIGN.md`](../design/DATABASE_REDESIGN.md) — data model rationale

---

## Table of contents

1. [Big picture in one diagram](#1-big-picture-in-one-diagram)
2. [Stage 0 — STM32 firmware emits a frame](#2-stage-0--stm32-firmware-emits-a-frame)
3. [Stage 1 — paho on_message callback](#3-stage-1--paho-on_message-callback)
4. [Stage 2 — `_consume` parse / clock / offset](#4-stage-2--_consume-parse--clock--offset)
5. [Stage 3 — buffers (live + window + raw archive)](#5-stage-3--buffers-live--window--raw-archive)
6. [Stage 4 — detection engine + mode gating](#6-stage-4--detection-engine--mode-gating)
7. [Stage 5 — TTL gate](#7-stage-5--ttl-gate)
8. [Stage 6 — durable sinks (DB + outbound MQTT)](#8-stage-6--durable-sinks-db--outbound-mqtt)
9. [Stage 7 — operator surfaces (SSE + REST + UI)](#9-stage-7--operator-surfaces-sse--rest--ui)
10. [Modbus path — the alternate inlet](#10-modbus-path--the-alternate-inlet)
11. [What happens at each load level](#11-what-happens-at-each-load-level)
12. [Failure modes and where they manifest](#12-failure-modes-and-where-they-manifest)

---

## 1. Big picture in one diagram

```
                    ┌──────────────────┐
                    │      STM32       │  firmware: 12-channel ADC,
                    │   ~100 Hz / sensor │  publishes JSON to MQTT
                    │   12 sensors     │  every ~10 ms
                    └────────┬─────────┘
                             │ stm32/adc
                             │ {"device_id", "ts", "adc1": [...], "adc2": [...]}
                             ▼
                    ┌──────────────────┐
                    │   Mosquitto      │  on the same Pi as HERMES
                    │   (MQTT broker)  │  port 1883, no TLS by default
                    └────────┬─────────┘
                             │
            ┌────────────────┴────────────────┐
            │                                 │
            ▼                                 ▼ (Modbus path: see §10)
    ┌──────────────────────────────────────────────────────────┐
    │ paho-mqtt callback thread (background, daemon)           │
    │   on_message:  receive_ts = time.time()                  │
    │                loop.call_soon_threadsafe(queue.put)      │
    └────────────────────────┬─────────────────────────────────┘
                             │ (asyncio.Queue, MPSC, lock-free)
                             ▼
    ┌──────────────────────────────────────────────────────────┐
    │ asyncio event loop — _consume() coroutine                │
    │   1. orjson.loads(raw_bytes)                             │
    │   2. shard filter  (Layer 3: device_id % count == index) │
    │   3. ClockRegistry.anchor    → wall_ts                   │
    │   4. parse_stm32_adc_payload → {sensor_id: float}        │
    │   5. OffsetCache.apply       → calibrated values         │
    │   6. LiveDataHub.push        → live ring buffer (SSE)    │
    │   7. EventWindowBuffer.push  → 30 s ring (DB sink reads) │
    │   8. SessionSampleWriter.push → opt-in raw archive       │
    │   9. DetectionEngine.feed_snapshot                       │
    │        → ModeStateMachine (POWER_ON/STARTUP/BREAK)       │
    │        → Type A/B/C/D detectors                          │
    │        → emits DetectedEvent if fired                    │
    └────────────────────────┬─────────────────────────────────┘
                             │ DetectedEvent
                             ▼
    ┌──────────────────────────────────────────────────────────┐
    │ TtlGateSink — dedup + priority + BREAK bypass             │
    │   Rule 1: higher-priority armed → drop lower               │
    │   Rule 2: higher-priority arrives → preempt lower          │
    │   Rule 3: same type already armed → swallow               │
    │   Rule 4: arm timer; forward when triggered_at + ttl      │
    │   BREAK: bypass entirely                                  │
    └────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │  MultiplexEventSink  │  fan-out, per-child failure isolation
                  └────┬─────────────┬───┘
                       │             │
                       ▼             ▼
            ┌───────────────┐  ┌────────────────────┐
            │ DbEventSink   │  │ MqttEventSink      │
            │  asyncio task │  │  paho client       │
            │  waits 9 s    │  │  publish topic:    │
            │  then writes  │  │  stm32/events/     │
            │  events +     │  │   <dev>/<sid>/<TYPE>│
            │  event_windows│  │  payload:          │
            │  in one tx    │  │  {timestamp,       │
            │               │  │   sensor_value}    │
            └───────┬───────┘  └─────────┬──────────┘
                    │                    │
                    ▼                    ▼
        ┌──────────────────────┐   ┌──────────────────┐
        │ PostgreSQL +         │   │ Mosquitto        │
        │ TimescaleDB          │   │ (stm32/events/...)│
        │  events (hypertable) │   │   ↳ PLC, SCADA,  │
        │  event_windows ditto │   │     Grafana...   │
        │  packages, sessions, │   └──────────────────┘
        │  parameters, ...     │
        └──────────┬───────────┘
                   │
                   ▼
       ┌─────────────────────────────┐
       │ FastAPI (hermes-api)        │   reads from DB,
       │  /api/events                │   serves SSE from LiveDataHub,
       │  /api/sessions              │   exposes Prometheus
       │  /api/config (thresholds)   │   metrics, mediates UI
       │  /api/devices               │
       │  /api/mqtt-brokers          │
       │  /api/system-tunables       │
       │  /api/live_stream/<dev>     │── SSE ──┐
       │  /api/metrics (Prom)        │         │
       └──────────────┬──────────────┘         │
                      │ HTTPS (nginx in prod)  │
                      ▼                        ▼
              ┌──────────────────────────────────┐
              │ SvelteKit UI (uPlot live charts) │
              │   /devices/<id>     live + chart │
              │   /events           list + ±9 s  │
              │   /sessions         lifecycle    │
              │   /config           thresholds   │
              │   /mqtt-brokers     broker reg   │
              │   /settings         system info  │
              └──────────────────────────────────┘
```

The next ten sections walk one frame from `recv_ts` to "operator clicks
the row".

---

## 2. Stage 0 — STM32 firmware emits a frame

Not part of HERMES, but documented in
[`../contracts/HARDWARE_INTERFACE.md`](../contracts/HARDWARE_INTERFACE.md).
Summary of what HERMES expects on the wire:

```json
{
  "device_id": 1,
  "ts": 1234567,
  "adc1": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
  "adc2": [20.0, 21.0, 22.0, 23.0, 24.0, 25.0]
}
```

| Field | Meaning |
|-------|---------|
| `device_id` | Operator-assigned, 1..999. Used for routing, sharding, and FK joins. |
| `ts` | STM32 milliseconds-since-boot counter. NOT wall time. |
| `adc1` | First six sensor readings (sensors 1..6). |
| `adc2` | Last six sensor readings (sensors 7..12). |

The split into `adc1` / `adc2` is firmware-historic — both are mapped
to a flat `dict[int, float]` by `parse_stm32_adc_payload` in
[`services/hermes/ingest/parser.py`](../../services/hermes/ingest/parser.py).
Adding a third `adc3` block (e.g. for >12 sensors) is a parser change
plus a `session_samples_sensor_range` constraint relaxation in
migration `0002_core_tables.sql`.

---

## 3. Stage 1 — paho `on_message` callback

Lives at
[`services/hermes/ingest/main.py`](../../services/hermes/ingest/main.py)
inside `IngestPipeline.start()`. The paho client runs its own
background thread (`client.loop_start()`); `on_message` runs there.

**This callback is intentionally minimal — three operations only:**

```python
def on_message(_client, _userdata, msg):
    receive_ts = time.time()
    loop.call_soon_threadsafe(self._queue.put_nowait,
                              (msg.payload, receive_ts))
```

```
   paho thread                        asyncio loop thread
   ┌──────────┐                       ┌──────────┐
   │ network  │  on_message:          │ event    │
   │ socket   ├─► time.time()         │ loop     │
   │          │   put_nowait via      │          │
   │ "ULTRA-  │   call_soon_threadsafe│  _consume│
   │  LIGHT"  │═══════════════════════►  draining │
   └──────────┘  (lock-free handoff)  └──────────┘
```

Why so thin: anything heavier on the paho thread (JSON parse, logging,
DB hit) blocks the next message. The legacy system documented this
under "ULTRA-LIGHT callback", and the rewrite preserves it.

`receive_ts` is the wall-clock-time **the broker delivered the message
to us**, NOT the STM32's `ts` field. We need it for the deterministic
golden-traffic replay — see
[`../contracts/GOLDEN_TRAFFIC_PLAN.md`](../contracts/GOLDEN_TRAFFIC_PLAN.md).

---

## 4. Stage 2 — `_consume` parse / clock / offset

`async def _consume(...)` in
[`services/hermes/ingest/main.py`](../../services/hermes/ingest/main.py).

Per-sample work, broken into named "stages" so the
`hermes_pipeline_stage_duration_seconds` Prometheus histogram (sampled
1-in-100) can break down latency:

```
┌───────────────────────────────────────────────────────────────┐
│ For each (raw_bytes, receive_ts) dequeued from asyncio.Queue: │
└───────────────────────────────────────────────────────────────┘
        │
        ├──[stage: parse]──── orjson.loads(raw_bytes)
        │                     bad JSON → MSGS_INVALID_TOTAL.inc(),
        │                                log warning, continue
        │
        ├──[shard filter ]──── if shard_count > 1 and
        │                       device_id % shard_count != shard_index:
        │                          continue  (drop without metrics)
        │
        ├── MSGS_RECEIVED_TOTAL.labels(device_id).inc()
        │
        ├──[stage: anchor ]──── ClockRegistry.anchor(device_id,
        │                                            receive_ts,
        │                                            stm32_ts_sec)
        │                       (re-anchors if drift > MQTT_DRIFT_THRESHOLD_S)
        │
        ├──[stage: adc_parse]── parse_stm32_adc_payload(payload)
        │                       returns {sensor_id: raw_float}
        │                       empty → continue
        │
        ├──[stage: offset ]──── OffsetCache.apply(device_id, sensor_values)
        │                       sensor_values[sid] -= offset[sid]
        │                       (calibration; see SensorOffset model)
        │
        ├──[stage: buffers ]─── live_data.push(...)
        │                       window_buffer.push_snapshot(...)
        │                       sample_writer.push_snapshot(...)  (gap 6)
        │
        ├── SAMPLES_PROCESSED_TOTAL.inc(len(sensor_values))
        │
        └──[stage: detect ]──── detection_engine.feed_snapshot(...)
                                (skipped if detection is None — live_only mode)
```

### Clock anchoring deserves a paragraph

STM32 sends `ts` in milliseconds-since-boot — useless for wall-clock
correlation across reboots. `ClockRegistry.anchor`:

1. Computes a per-device offset on first sample: `offset = receive_ts - stm32_ts_sec`.
2. Returns wall time as `stm32_ts_sec + offset`.
3. If the computed wall time drifts from `receive_ts` by more than
   `MQTT_DRIFT_THRESHOLD_S` (default 5 s), re-anchors automatically.
   This handles STM32 counter wraps and reboots.

The anchored `ts` is the single source of truth used for all
downstream timestamps — live charts, event triggered_at, window
boundaries, session_samples ts.

---

## 5. Stage 3 — buffers (live + window + raw archive)

Three concurrent in-memory writers, all on the asyncio loop, all
single-threaded.

### 5.1 `LiveDataHub` — for SSE

[`services/hermes/ingest/live_data.py`](../../services/hermes/ingest/live_data.py).

Per-device `collections.deque(maxlen=LIVE_BUFFER_MAX_SAMPLES)` of
`SensorSnapshot(ts, values)`. Default depth 2000 = ~20 s at 100 Hz.

```
LiveDataHub
  device 1: deque([snap_t0, snap_t1, ..., snap_t1999])   maxlen=2000
  device 2: deque([snap_t0, snap_t1, ..., snap_t1999])
  ...
```

Read path: `since(device_id, after_ts)` returns snapshots newer than
the cursor. The SSE endpoint
([`services/hermes/api/routes/live_stream.py`](../../services/hermes/api/routes/live_stream.py))
polls every `interval_s` (default 100 ms), batches up to `max_samples`,
serialises once, sends.

### 5.2 `EventWindowBuffer` — for ±9 s window slicing

[`services/hermes/detection/window_buffer.py`](../../services/hermes/detection/window_buffer.py).

Same shape as LiveDataHub but a longer ring (30 s). When an event
fires, `DbEventSink._writer_loop` waits until `triggered_at + 9 s`,
then slices the buffer for `(triggered_at - 9 s, triggered_at + 9 s)`,
encodes the samples, and writes one `event_windows` row per event.

The 30 s ring is sized so the post-9 s wait never falls off the back
of the buffer even with mild jitter.

### 5.3 `SessionSampleWriter` — opt-in raw archive

[`services/hermes/ingest/session_samples.py`](../../services/hermes/ingest/session_samples.py).

Hot-path is one dict lookup + early return when no session has
`record_raw_samples=true`. When recording is on, the writer:

1. Resolves session: LOCAL session for the device beats GLOBAL.
2. Appends `(session_id, device_id, sensor_id, ts, value)` tuples to
   an in-memory list (max 60 000 entries ≈ 2 s at full production rate).
3. A background task flushes the buffer every 1 s via asyncpg
   `copy_records_to_table` to the `session_samples` hypertable, in
   chunks of 5000 rows.
4. Refresh task polls the DB every 5 s for the active recording set.
5. Backpressure: if the buffer overflows, oldest samples drop and
   `hermes_session_samples_dropped_total` ticks. Operators see drops
   in Grafana before they see DB latency.

---

## 6. Stage 4 — detection engine + mode gating

[`services/hermes/detection/engine.py`](../../services/hermes/detection/engine.py).

`feed_snapshot(device_id, ts, values)` is called once per inbound
snapshot (12 sensor values). For each sensor:

```
sensor_values[sid] ──┐
                     │
                     ▼
            ┌──────────────────────┐
            │  ModeStateMachine    │  (gap 3, alpha.17)
            │  feed(dev, sid,      │
            │       value, ts)     │
            └────┬─────────────┬───┘
                 │             │
        active=True            active=False
        (STARTUP)              (POWER_ON or BREAK)
            │                       │
            ▼                       ▼
   ┌─────────────────┐      ┌─────────────────┐
   │ Run all 4 type  │      │ Type A: feed    │
   │ detectors       │      │   (window stays │
   │ (A, B, C, D)    │      │   primed) but   │
   │ in order        │      │   suppress fires│
   │                 │      │ Types B/C/D:    │
   │ Type C must     │      │   skip entirely │
   │ run before D    │      └─────────────────┘
   │ (D reads C's    │
   │ current_avg)    │
   └────────┬────────┘
            │
       DetectedEvent | None
            │
            ▼
   sink.publish(event)   ──► TtlGateSink (next stage)
```

Plus, on every sample, the state machine itself can emit a BREAK event
on the STARTUP→BREAK transition. That goes through the same
`sink.publish` path and is forwarded by the TTL gate verbatim.

Detection state is keyed per `(device_id, sensor_id, event_type)`.
`reset_device(device_id)` drops cached detectors for that device and
resets the mode state machine; it's called when:
- Operator commits a config change via `/api/config/...`
- A multi-shard `NOTIFY hermes_config_changed` arrives
- A long data gap (`> data_gap_reset_s`) is detected on a sensor

Detector internals are documented in [`EVENTS.md`](./EVENTS.md).

---

## 7. Stage 5 — TTL gate

[`services/hermes/detection/ttl_gate.py`](../../services/hermes/detection/ttl_gate.py)
(gap 2, alpha.13).

Without this, a sustained out-of-band signal would trigger an event on
every sample and flood the operator's view. The gate enforces four
legacy rules per `(device_id, sensor_id)`:

```
                    ┌────────────────────────┐
event arrives ────► │  Is this a BREAK?      │── yes ──► forward verbatim
                    └─────────┬──────────────┘
                              │ no
                              ▼
                    ┌────────────────────────┐
                    │ Is a HIGHER-priority   │── yes ──► drop (Rule 1)
                    │ type armed for this    │
                    │ (device, sensor)?      │
                    └─────────┬──────────────┘
                              │ no
                              ▼
                    ┌────────────────────────┐
                    │ Is the SAME type       │── yes ──► drop (Rule 3 — merge)
                    │ already armed?         │
                    └─────────┬──────────────┘
                              │ no
                              ▼
                    ┌────────────────────────┐
                    │ Are LOWER-priority     │── yes ──► clear them (Rule 2 — preempt)
                    │ timers armed for this  │
                    │ (device, sensor)?      │
                    └─────────┬──────────────┘
                              │
                              ▼
                       Arm timer (Rule 4)
                       Hold for ttl_seconds
                       (default 5 s)
                              │
                              │  ts >= armed_at + ttl_seconds
                              ▼
                       Forward to child sink
```

Priority order: A (1) < B (2) < C (3) < D (4). BREAK is outside the
scale.

`flush()` on shutdown forces every armed timer to forward immediately
so a graceful stop doesn't lose held events.

---

## 8. Stage 6 — durable sinks (DB + outbound MQTT)

The TTL gate's child is a `MultiplexEventSink` that fans events to
two children with per-child failure isolation:

```
event ──► MultiplexEventSink ──┬──► DbEventSink     (Postgres)
                                 │
                                 └──► MqttEventSink   (Mosquitto)
```

If one child raises, the other still receives the event. Used to
prevent broker outages from silencing DB persistence and vice versa.

### 8.1 `DbEventSink` — `events` + `event_windows`

[`services/hermes/detection/db_sink.py`](../../services/hermes/detection/db_sink.py).

`publish(event)` is sync (called on the asyncio loop) — it just
enqueues the event onto a per-sink internal queue. A separate
`_writer_loop` background task processes the queue:

```
Wait until triggered_at + 9 s (post-window fence)
  │
  ▼
Slice EventWindowBuffer for (triggered_at - 9 s, triggered_at + 9 s)
  │
  ▼
Encode samples → bytes (json-utf8 today; zstd+delta-f32 planned)
  │
  ▼
Open DB session, single transaction:
  - INSERT INTO events (session_id, triggered_at, fired_at, ...)
  - INSERT INTO event_windows (event_id, window_start, window_end,
                                encoding, data)
  - COMMIT
  │
  ▼
EVENTS_PERSISTED_TOTAL.labels(event_type).inc()
```

The 9 s wait is why operators see events appear with a small delay
even when the trigger was instant — the window has to fill before the
row exists. `events.fired_at` records the actual write time so
forensics can compare.

### 8.2 `MqttEventSink` — outbound publish

[`services/hermes/detection/mqtt_sink.py`](../../services/hermes/detection/mqtt_sink.py)
(gap 1, alpha.11).

Topic shape (matches legacy contract):

```
stm32/events/<device_id>/<sensor_id>/<EVENT_TYPE>
```

Payload:

```json
{"timestamp": "YYYY-MM-DD HH:MM:SS.mmm", "sensor_value": <float|null>}
```

Existing PLC / SCADA systems subscribed to the legacy topic shape keep
working without any change.

---

## 9. Stage 7 — operator surfaces (SSE + REST + UI)

```
Browser ──HTTPS──► nginx (TLS terminator, prod)
                     │
                     ├── /api/* ─────► hermes-api (FastAPI)
                     │                  ├── /api/live_stream/<id>  (SSE)
                     │                  ├── /api/events            (REST)
                     │                  ├── /api/sessions          (REST)
                     │                  ├── /api/config            (REST + NOTIFY)
                     │                  ├── /api/devices           (REST)
                     │                  ├── /api/mqtt-brokers      (REST)
                     │                  ├── /api/system-tunables   (REST)
                     │                  └── /api/metrics           (Prom)
                     │
                     └── /  ──────────► SvelteKit static bundle
```

### Live chart path

```
DetectionEngine produces snapshots ──► LiveDataHub (per-device deque)
                                            │
                                            ▼
                                   /api/live_stream/<dev>
                                   (poll every 100 ms,
                                    batch up to 500 samples,
                                    SSE: data: {"samples":[{"ts","values"}]})
                                            │
                                            ▼
                                   ui/src/lib/LiveChart.svelte
                                   (uPlot, 12 traces, 1 ms/frame paint)
```

### Event row → window chart path

```
Operator clicks event row in /events page
                  │
                  ▼
    GET /api/events/<id>            ──► EventOut (metadata)
                  │
                  ▼
    GET /api/events/<id>/window     ──► EventWindowOut
                  │                       (decoded sample list)
                  ▼
    UI renders ±9 s waveform
    (chart pending — see audit response)
```

UI page-by-page detail in [`UI.md`](./UI.md). API endpoint catalog in
[`../design/REST_API.md`](../design/REST_API.md).

---

## 10. Modbus path — the alternate inlet

Devices with `protocol=modbus_tcp` have the same downstream path but
a different inlet (gap 7, alpha.21):

```
ModbusManager (asyncio task, every 5 s)
   │
   ├── SELECT * FROM devices
   │       WHERE protocol='modbus_tcp' AND is_active=true
   │
   ├── reconcile in-memory poller set with DB:
   │     - new device → spawn ModbusPoller
   │     - removed device → poller.stop()
   │     - changed config → restart poller
   │
   ▼
ModbusPoller (one per device, asyncio task)
   │
   │ every poll_interval_ms (default 100 ms):
   │   ├── client.read_input_registers(register_start, register_count)
   │   ├── decode each uint16 to float via raw / scaling
   │   └── invoke callback(device_id, ts, sensor_values)
   │
   ▼
IngestPipeline._on_modbus_snapshot
   │
   │  (this is the only difference from the MQTT path —
   │   we already have a structured snapshot, so no JSON
   │   parse, no STM32 clock anchoring; the local poll
   │   time IS wall time)
   │
   ├── OffsetCache.apply
   ├── LiveDataHub.push
   ├── EventWindowBuffer.push
   ├── DetectionEngine.feed_snapshot
   └── SessionSampleWriter.push_snapshot
```

A device row's `modbus_config` JSONB carries the polling parameters,
validated by the `ModbusConfig` Pydantic model:

| Field | Default | Notes |
|-------|---------|-------|
| `host` | (required) | Modbus TCP server hostname/IP |
| `port` | 502 | Standard Modbus TCP port |
| `unit_id` | 1 | Modbus slave id |
| `register_start` | (required) | First register address |
| `register_count` | 12 | Always 12 in HERMES |
| `scaling` | 1.0 | `engineering_value = raw_uint16 / scaling` |
| `poll_interval_ms` | 100 | 10 Hz default |
| `timeout_s` | 1.0 | TCP read timeout |

---

## 11. What happens at each load level

The pipeline is the same; throughput moves through latency tiers.

### 11.1 Idle (1 sensor heartbeat per second)

```
~1 msg/s → asyncio.Queue depth ≈ 0
            _consume drains in <1 ms
            no events fire
            DB writer queue empty
            Prometheus counters tick lazily
```

### 11.2 Production (20 devices × 12 sensors × 100 Hz = 2 000 msg/s)

```
2 000 msg/s × ~100 µs/msg = 200 ms/s of CPU on one core ≈ 20%
asyncio.Queue depth: 0–10 typical, 50 spike on bursty seconds
events: ~1/hour/sensor → ~1/min system-wide
DB writer queue: 0–5 events
LiveDataHub: 12 snapshots/sec/device written, no growth (deque)
Prometheus stage histogram: ~120 obs/stage/sec (sampled 1/100)
```

Bench: alpha.14+ sustains ~16 700 msg/s on a developer laptop and
~5 500 msg/s on a Pi 4 — comfortably above the 2 000 msg/s target.

### 11.3 Burst (a sustained out-of-band signal on every sensor)

```
2 000 msg/s × 12 sensors all in band → 24 000 detections candidate/s
TTL gate dedups → 12 events held per (device, sensor) per ttl_seconds
DB writer queue: ~240 events held (12 sensors × 20 devices)
Window buffer: each event waits 9 s post-window before write
Outbound MQTT: 240 publishes when timers expire
```

The TTL gate is the protection here — without it the operator UI and
the DB would both be flooded.

### 11.4 Multi-shard (Layer 3 alpha.15)

Same numbers, but spread across 4 ingest processes:

```
shard 0: devices where id %4 == 0 (devices 4, 8, 12, 16, 20)
shard 1: devices where id %4 == 1 (devices 1, 5, 9, 13, 17)
shard 2: devices where id %4 == 2 (devices 2, 6, 10, 14, 18)
shard 3: devices where id %4 == 3 (devices 3, 7, 11, 15, 19)

API process: live_only mode, subscribes to all stm32/adc messages,
             fills LiveDataHub for SSE, runs no detection
```

Detection is still serial within each shard but parallel across
shards — uses all 4 Pi 4 cores. See
[`../design/MULTI_SHARD.md`](../design/MULTI_SHARD.md).

---

## 12. Failure modes and where they manifest

Quick lookup of "what goes wrong → which metric / log → where to fix":

| Failure | Symptom | Metric / log | Where to fix |
|---------|---------|--------------|--------------|
| Bad JSON from STM32 | message dropped silently | `hermes_msgs_invalid_total` ticks; `mqtt_bad_json` warn log | firmware bug or broker proxy mangling payload |
| Broker unreachable | live chart freezes; no new events | `hermes_mqtt_connected` = 0; `mqtt_disconnected` log | network, mosquitto config, MQTT credentials |
| Postgres unreachable | events queue grows; UI shows stale list | `hermes_db_writer_pending` rises; `db_writer_failed` log | systemd `postgresql`, disk full, pg_hba.conf |
| Detection thresholds too low | event flood | `hermes_events_detected_total` rate spikes | tune via `/config` UI |
| Detection thresholds too high | nothing fires when it should | `hermes_events_detected_total` flat | same |
| Mode switching wrong | detector silent in STARTUP, fires in BREAK | inspect via `/api/system-tunables`; sensor mode badges (TODO) | edit `mode_switching.config` parameter |
| Recording session backpressure | `session_samples` rows missing | `hermes_session_samples_dropped_total` ticks | check disk I/O, Postgres compress lag |
| Modbus unreachable | sensor values frozen for that device | `hermes_modbus_reads_failed_total{device}` ticks | network to PLC, slave_id mismatch |
| Operator config change not applying on shard | thresholds stale on shard N | API logs `pg_notify`; shard logs missing `config_reloaded` | LISTEN connection dropped, restart the shard |

For deeper troubleshooting, see (planned) `docs/operations/TROUBLESHOOTING.md`.
