"""
Prometheus metrics for the Hermes ingest + detection pipeline.

All metrics live on the default registry so
``prometheus_client.generate_latest()`` produces the wire format that
``/api/metrics`` returns. A scraper pointed at the FastAPI port pulls
them; a sidecar pushgateway is overkill for this scale.

Naming follows Prometheus convention:
    Counter   ``<namespace>_<noun>_total``  (cumulative)
    Gauge     ``<namespace>_<noun>``        (instantaneous)
    Histogram ``<namespace>_<noun>_seconds`` (latency)

Hot-path discipline:
    Counters / gauges are constant-time (single atomic add); they fire
    on every event. Histograms cost ~1 µs per ``observe`` in pure
    Python — at 24 000 samples/s × multiple stages that's ~100 ms/s of
    CPU spent on instrumentation alone, which we can't afford on a Pi.
    So timings are SAMPLED (1 in ``_SAMPLE_EVERY``); we still get
    enough observations to estimate p99 without the overhead.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram

# ─── Counters — fire on every event ────────────────────────────────

MSGS_RECEIVED_TOTAL = Counter(
    "hermes_msgs_received_total",
    "MQTT messages dequeued from the paho-asyncio handoff queue.",
    ["device_id"],
)

MSGS_INVALID_TOTAL = Counter(
    "hermes_msgs_invalid_total",
    "MQTT messages discarded due to a JSON-decode failure.",
)

SAMPLES_PROCESSED_TOTAL = Counter(
    "hermes_samples_processed_total",
    "Sensor readings (post-parse, post-offset) fed into the detection engine.",
    ["device_id"],
)

EVENTS_DETECTED_TOTAL = Counter(
    "hermes_events_detected_total",
    "Events fired by the detection engine (before any sink delivery).",
    ["event_type", "device_id"],
)

EVENTS_PERSISTED_TOTAL = Counter(
    "hermes_events_persisted_total",
    "Events successfully written to the events + event_windows tables.",
    ["event_type"],
)

EVENTS_PUBLISHED_TOTAL = Counter(
    "hermes_events_published_total",
    "Events successfully published to the outbound stm32/events/... topic.",
    ["event_type"],
)

# ─── session_samples writer (gap 6) ────────────────────────────────

SESSION_SAMPLES_WRITTEN_TOTAL = Counter(
    "hermes_session_samples_written_total",
    "Raw sensor rows persisted to session_samples by the continuous-sample writer.",
)

SESSION_SAMPLES_DROPPED_TOTAL = Counter(
    "hermes_session_samples_dropped_total",
    (
        "Raw sensor rows dropped before write because the writer queue "
        "was full. Sustained drops indicate the DB can't keep up with "
        "the input rate; investigate Postgres/Timescale write throughput."
    ),
)

SESSION_SAMPLES_BATCHES_FLUSHED_TOTAL = Counter(
    "hermes_session_samples_batches_flushed_total",
    "COPY batches dispatched by the session-samples writer.",
)

# ─── Modbus poller (gap 7) ─────────────────────────────────────────

MODBUS_READS_OK_TOTAL = Counter(
    "hermes_modbus_reads_ok_total",
    "Successful Modbus TCP register reads, per device.",
    ["device_id"],
)

MODBUS_READS_FAILED_TOTAL = Counter(
    "hermes_modbus_reads_failed_total",
    "Failed Modbus TCP reads (timeout, exception, error response).",
    ["device_id"],
)

# ─── Gauges — current state ────────────────────────────────────────

CONSUME_QUEUE_DEPTH = Gauge(
    "hermes_consume_queue_depth",
    "Pending MQTT messages in the asyncio handoff queue.",
)

DB_WRITER_PENDING = Gauge(
    "hermes_db_writer_pending",
    "Pending events in the DbEventSink writer queue (post-detection, pre-DB write).",
)

SESSION_SAMPLES_QUEUE_DEPTH = Gauge(
    "hermes_session_samples_queue_depth",
    "Buffered raw sensor rows waiting to be flushed by the session-samples writer.",
)

SESSION_SAMPLES_RECORDING_ACTIVE = Gauge(
    "hermes_session_samples_recording_active",
    "1 if at least one active session has record_raw_samples=true, else 0.",
)

MODBUS_POLLERS_ACTIVE = Gauge(
    "hermes_modbus_pollers_active",
    "Number of Modbus TCP pollers the manager currently has running.",
)

MQTT_CONNECTED = Gauge(
    "hermes_mqtt_connected",
    "1 if the paho client is currently connected to the broker, else 0.",
)

# ─── Histograms — sampled stage timings ────────────────────────────

# Latency buckets in seconds: 0.1 ms → 1 s. Wide because we want one
# Histogram type to cover both fast paths (parse, ~10 µs) and slow paths
# (DB write, ~10 ms).
_LATENCY_BUCKETS: tuple[float, ...] = (
    0.0001,
    0.0005,
    0.001,
    0.005,
    0.01,
    0.05,
    0.1,
    0.5,
    1.0,
)

STAGE_DURATION = Histogram(
    "hermes_pipeline_stage_duration_seconds",
    "Per-stage processing latency in the ingest hot path (sampled — see _SAMPLE_EVERY).",
    ["stage"],
    buckets=_LATENCY_BUCKETS,
)

# Sample every Nth call. At 2 000 msg/s × ~6 stages, N=100 yields ~120
# observations per stage per second — plenty for stable percentile
# estimates, under 0.5 % CPU overhead in real workloads.
_SAMPLE_EVERY: int = 100
_sample_counter: int = 0


@contextmanager
def time_stage(stage: str) -> Iterator[None]:
    """
    Time a block and observe the duration into the per-stage histogram.

    Sampled: only ~1 in ``_SAMPLE_EVERY`` calls actually times. The
    others are a no-op increment of the global counter. Caller doesn't
    have to think about this — wrap any hot block, get free p99 in
    Grafana.
    """
    global _sample_counter
    _sample_counter += 1
    if _sample_counter % _SAMPLE_EVERY != 0:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        STAGE_DURATION.labels(stage=stage).observe(time.perf_counter() - start)


def counter_value(metric: Counter, **labels: str) -> float:
    """
    Read a Counter's current value via the public ``.collect()`` API.

    prometheus_client doesn't expose a public reset, and its private
    ``_value`` / ``_metrics`` attributes have churned across versions.
    Tests assert *deltas* (post − pre) by sampling here before and
    after the work, which is stable across prom-client upgrades and
    free of cross-test pollution.
    """
    target_name = metric._name + "_total"  # noqa: SLF001
    for family in metric.collect():
        for sample in family.samples:
            if sample.name != target_name:
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                return float(sample.value)
    return 0.0


def gauge_value(metric: Gauge) -> float:
    """Read a Gauge value via ``.collect()`` (public API, no churn risk)."""
    for family in metric.collect():
        for sample in family.samples:
            if sample.name == metric._name:  # noqa: SLF001
                return float(sample.value)
    return 0.0


def histogram_count(metric: Histogram, **labels: str) -> float:
    """Total observation count for a labelled Histogram (post-sampling)."""
    target_name = metric._name + "_count"  # noqa: SLF001
    for family in metric.collect():
        for sample in family.samples:
            if sample.name != target_name:
                continue
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                return float(sample.value)
    return 0.0
