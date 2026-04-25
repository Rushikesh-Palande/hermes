# METRICS.md — every Prometheus metric

> **Audience:** anyone wiring up Grafana / Prometheus / alertmanager,
> debugging "why is the queue full?", or adding a new metric to the
> codebase.
>
> **Companion docs:**
> - [`WORKFLOW.md`](./WORKFLOW.md) — where each metric ticks in the pipeline
> - [`CONFIGURATION.md`](./CONFIGURATION.md) — env vars affecting metrics endpoint
> - [`../design/REST_API.md`](../design/REST_API.md) §12 — `/api/metrics` shape

---

## Where the metrics live

```
hermes-api process
  ├── prometheus_client default registry
  ├── /api/metrics route → generate_latest() text format
  └── all counters/gauges/histograms below

hermes-ingest process
  ├── same registry definitions (imported from hermes.metrics)
  ├── ticks counters in _consume, sinks, writers, modbus
  └── exposes via /api/metrics ONLY when scraped
      (in multi-shard, each shard has its own /api/metrics
       behind a different port — see Multi-shard scraping)
```

The metric definitions are module-level singletons in
`services/hermes/metrics.py`. Both processes import the same module
so the names + label schemas are guaranteed to match.

`/api/metrics` is unauthenticated by design — firewall / nginx /
internal-only ACL gate it. Don't put it on the public internet.

---

## All metrics, by category

### 1. Inbound throughput

| Metric | Type | Labels | Where it ticks |
|--------|------|--------|----------------|
| `hermes_msgs_received_total` | Counter | `device_id` | `_consume` per inbound message (after JSON parse, after shard filter) |
| `hermes_msgs_invalid_total` | Counter | — | `_consume` on `orjson.JSONDecodeError` |
| `hermes_samples_processed_total` | Counter | `device_id` | `_consume` per snapshot, increments by `len(sensor_values)` |

**Interpretation:**
- `rate(hermes_msgs_received_total[1m])` ≈ MQTT message rate per device.
- `rate(hermes_samples_processed_total[1m])` ≈ sensor reading rate.
  Should be ~12 × the message rate at full health.
- A non-zero `rate(hermes_msgs_invalid_total[5m])` means broker / proxy
  is mangling payloads or firmware regressed.

### 2. Detection output

| Metric | Type | Labels | Where it ticks |
|--------|------|--------|----------------|
| `hermes_events_detected_total` | Counter | `event_type`, `device_id` | `DetectionEngine.feed_snapshot` immediately after a detector returns an event (BEFORE the TTL gate) |
| `hermes_events_persisted_total` | Counter | `event_type` | `DbEventSink._writer_loop` after a successful `events`+`event_windows` insert (AFTER the TTL gate) |
| `hermes_events_published_total` | Counter | `event_type` | `MqttEventSink.publish` after `paho.Client.publish` returns `rc == 0` |

**Interpretation:**
- `detected − persisted` over time ≈ events suppressed by the TTL gate.
- `persisted − published` ≈ events the broker rejected (broker
  disconnected, queue full).
- A spike on `event_type="A"` for one device usually means a sensor
  developed sustained noise.

### 3. Pipeline state (gauges)

| Metric | Type | Where it updates |
|--------|------|------------------|
| `hermes_consume_queue_depth` | Gauge | `_consume` after each successful dequeue (`queue.qsize()`) |
| `hermes_db_writer_pending` | Gauge | `DbEventSink._writer_loop` on enqueue + dequeue |
| `hermes_mqtt_connected` | Gauge | paho `on_connect` / `on_disconnect` callbacks. 1 = connected |

**Interpretation:**
- `consume_queue_depth` rising = consumer falling behind. Either CPU
  saturation on the asyncio loop or a slow downstream stage.
- `db_writer_pending` rising = events accumulating (the 9 s post-window
  fence + Postgres write rate). Sustained growth means writes are
  slower than the detection rate — investigate Postgres I/O.
- `mqtt_connected = 0` is an alertable condition during normal ops.

### 4. Hot-path latency

| Metric | Type | Labels | Where it ticks |
|--------|------|--------|----------------|
| `hermes_pipeline_stage_duration_seconds` | Histogram | `stage` | `time_stage("<name>")` context manager, sampled 1-in-100 |

Buckets: 0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0
(seconds — covers ~10 µs through ~1 s).

Stages currently observed:

| Stage | Roughly does |
|-------|--------------|
| `parse` | `orjson.loads` |
| `anchor` | `ClockRegistry.anchor` |
| `adc_parse` | `parse_stm32_adc_payload` |
| `offset` | `OffsetCache.apply` |
| `buffers` | `LiveDataHub.push` + `EventWindowBuffer.push_snapshot` + sample writer push |
| `detect` | `DetectionEngine.feed_snapshot` (when not live_only) |

Sampling: every 100th `time_stage` invocation actually records into
the histogram. At 2 000 msg/s × 6 stages = 12 000 calls/s, that's
~120 observations per stage per second — plenty for stable percentile
estimates while keeping CPU overhead under 0.5%.

**Interpretation:**
- `histogram_quantile(0.99, sum(rate(hermes_pipeline_stage_duration_seconds_bucket[1m])) by (le, stage))` per stage.
- Stage `parse` p99 spiking is the canonical "JSON got bigger / different shape" signal.
- Stage `detect` p99 spiking with constant input rate is config drift
  (a detector started doing more work) or a sensor with a degenerate signal.

### 5. session_samples writer (gap 6, alpha.20)

| Metric | Type | Notes |
|--------|------|-------|
| `hermes_session_samples_written_total` | Counter | Rows successfully written to `session_samples` |
| `hermes_session_samples_dropped_total` | Counter | Rows dropped due to buffer overflow |
| `hermes_session_samples_batches_flushed_total` | Counter | `copy_records_to_table` calls |
| `hermes_session_samples_queue_depth` | Gauge | In-memory buffer size right now |
| `hermes_session_samples_recording_active` | Gauge | 1 if at least one session has `record_raw_samples=true` |

**Interpretation:**
- `recording_active = 1` and `written_total` flat = the writer's
  asyncpg connection died; check ingest logs.
- `dropped_total` rising = DB can't keep up with the input rate.
  Investigate compress lag, disk I/O.
- `queue_depth` near `60_000` = overflow imminent.

### 6. Modbus poller (gap 7, alpha.21)

| Metric | Type | Labels | Notes |
|--------|------|--------|-------|
| `hermes_modbus_reads_ok_total` | Counter | `device_id` | Successful TCP reads |
| `hermes_modbus_reads_failed_total` | Counter | `device_id` | Timeout, exception, error response, or wrong register count |
| `hermes_modbus_pollers_active` | Gauge | — | Count of pollers currently running |

**Interpretation:**
- `failed_total / (ok_total + failed_total)` per device > 0.5 means
  the device is unreachable / misconfigured — alert on this.
- `pollers_active` should equal `count(devices.protocol='modbus_tcp' AND is_active=true)`.

---

## Reading the labels

`device_id` is the integer device id stringified (Prom labels are
strings). `event_type` is `A` / `B` / `C` / `D` / `BREAK`. `stage` is
one of the names in §4.

Avoid high-cardinality labels — we **deliberately do NOT label** by:
- `sensor_id` (12 per device → cardinality explosion at 20 devices = 240 series per metric)
- `event_id` (every event would create a new series)
- `session_id` (UUIDs — unbounded)

When you need that detail, query the events table directly via
`/api/events?...`. Prom is for "is the system healthy?" telemetry;
forensic per-event lookup belongs in the SQL query layer.

---

## Helper APIs (used by tests)

`metrics.py` exposes three readers via the public `.collect()` API:

```python
def counter_value(metric: Counter, **labels: str) -> float
def gauge_value(metric: Gauge) -> float
def histogram_count(metric: Histogram, **labels: str) -> float
```

Tests use these to assert "this counter ticked by N" without touching
private Prom internals (which change between versions). Example:

```python
before = counter_value(MSGS_RECEIVED_TOTAL, device_id="1")
# ... do something
after = counter_value(MSGS_RECEIVED_TOTAL, device_id="1")
assert after - before == 5
```

---

## Multi-shard scraping

In multi-shard deployments (Layer 3, alpha.15):

```
                                      ┌──── /api/metrics on hermes-api    (port 8080)
prometheus.yml                         │
  scrape_configs:                      ├──── (planned) per-shard /api/metrics
    - job_name: hermes-api             │       on different ports for each
      static_configs:                  │       hermes-ingest@N.service
        - targets: [hermes:8080]       │
                                       └──── currently shards don't expose /api/metrics
                                              themselves; they tick into the same registry,
                                              but the registry is per-process. So shard
                                              counters are visible only when the shard is
                                              embedded in the API (mode=all).
```

**Today**: the only `/api/metrics` is on `hermes-api`. In `mode=all`
it's a complete picture; in `mode=live_only` (multi-shard API) it
only sees the API process's counters — detection counters tick in
separate shard processes that don't expose HTTP.

**Tracked follow-up**: optionally start a tiny HTTP server per shard
(separate from FastAPI) on port `9090 + shard_index` so Prom can
scrape each shard. Or push to a Pushgateway. Both options are bench-
free; pick when multi-shard goes to production.

---

## Adding a new metric

1. Define in `services/hermes/metrics.py` at module level:
   ```python
   MY_THING_TOTAL = Counter(
       "hermes_my_thing_total",
       "What this measures (single-line help string).",
       ["label1", "label2"],   # keep cardinality bounded
   )
   ```
2. Tick it from the producer code via the imported reference.
3. For latency, prefer `STAGE_DURATION` with a new stage name rather
   than a fresh histogram — uniform buckets across the codebase.
4. Add a row to §1–6 above with where it ticks + how to read it.
5. If it's an alertable condition (e.g. queue depth or fail rate),
   write the recommended Prom expression in the row.

Naming convention:
- Counters end in `_total`.
- Gauges have no suffix or end in `_count`/`_seconds`/`_bytes` etc.
- Histograms end in `_seconds` (we only use latency histograms).
- Always prefix with `hermes_` so they don't collide with
  out-of-the-box Prom exporters.

---

## Suggested Grafana dashboards

A starter set of panels for an operator dashboard:

| Panel | Query | Threshold |
|-------|-------|-----------|
| **Live throughput** | `sum(rate(hermes_msgs_received_total[1m])) by (device_id)` | per-device line |
| **Detection rate by type** | `sum(rate(hermes_events_detected_total[5m])) by (event_type)` | one line per type |
| **Queue depth** | `hermes_consume_queue_depth` | red above 50 |
| **DB writer lag** | `hermes_db_writer_pending` | red above 100 |
| **MQTT connectivity** | `hermes_mqtt_connected` | alert on 0 |
| **Modbus failure rate** | `sum(rate(hermes_modbus_reads_failed_total[5m])) by (device_id) / sum(rate(hermes_modbus_reads_ok_total[5m] + hermes_modbus_reads_failed_total[5m])) by (device_id)` | alert >0.5 |
| **Stage latency p99** | `histogram_quantile(0.99, sum(rate(hermes_pipeline_stage_duration_seconds_bucket[5m])) by (le, stage))` | one line per stage |
| **Session samples drop rate** | `rate(hermes_session_samples_dropped_total[5m])` | alert above 0 |
| **JSON decode failures** | `rate(hermes_msgs_invalid_total[5m])` | alert above 0.01 |

Dashboard JSON not yet checked in — when the production deployment
crystallises, add it under `packaging/grafana/`.
