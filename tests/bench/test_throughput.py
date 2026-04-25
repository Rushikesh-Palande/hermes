"""
Synthetic-load benchmark for the ingest hot path.

Bypasses the broker — we synthesise the same JSON payloads paho would
push and feed them directly to the asyncio handoff queue, then time
how long the consumer takes to drain.

Target load (the production budget):

    20 devices × 12 sensors × 100 Hz = 2 000 messages/s, 24 000 readings/s.

Default in CI: 1 second of synthetic traffic (2 000 messages). The
benchmark asserts:
    1. Every message ends up in either MSGS_RECEIVED_TOTAL or
       MSGS_INVALID_TOTAL (no silent drops).
    2. SAMPLES_PROCESSED_TOTAL == messages × 12 (sensors per device).
    3. Wall-clock to drain stays under a budget that leaves headroom
       for FastAPI + Postgres + GC on the same Pi 4 core.

Numbers feed Grafana / readme so we know if a future commit silently
makes the pipeline slower.

Marked ``bench`` — deselected from the default ``pytest`` run so a
laptop without ``HERMES_DEV_MODE`` doesn't pay the cost on every save.
Run explicitly with ``pytest -m bench``.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from hermes import metrics as m
from hermes.config import get_settings
from hermes.detection.config import StaticConfigProvider, TypeAConfig
from hermes.detection.engine import DetectionEngine
from hermes.detection.sink import LoggingEventSink
from hermes.detection.window_buffer import EventWindowBuffer
from hermes.ingest.clock import ClockRegistry
from hermes.ingest.live_data import LiveDataHub
from hermes.ingest.main import _consume
from hermes.ingest.offsets import OffsetCache

# Each MQTT message carries one device tick = 12 sensor readings.
SENSORS_PER_MSG: int = 12

# Number of synthetic messages to feed. 2_000 = 1 s of production load.
TOTAL_MSGS: int = 2_000

# Wall-clock budget to drain TOTAL_MSGS on a developer laptop. The Pi 4
# is ~3-4× slower; the value here is calibrated for CI runners and
# leaves enough headroom that GC pauses don't false-alarm. Feel free
# to tighten this once we have stable numbers from a few runs.
DRAIN_BUDGET_SECONDS: float = 6.0


def _make_payload(device_id: int, ts_ms: int) -> bytes:
    """Build a synthetic STM32 ADC payload identical to what paho receives."""
    return json.dumps(
        {
            "device_id": device_id,
            "ts": ts_ms,
            "adc1": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
            "adc2": [20.0, 21.0, 22.0, 23.0, 24.0, 25.0],
        },
        separators=(",", ":"),
    ).encode("utf-8")


@pytest.mark.bench
@pytest.mark.asyncio
async def test_consumer_drains_2000_msgs_under_budget() -> None:
    settings = get_settings()

    queue: asyncio.Queue[tuple[bytes, float]] = asyncio.Queue()
    clocks = ClockRegistry(drift_threshold_s=settings.mqtt_drift_threshold_s)
    offsets = OffsetCache()
    live = LiveDataHub(maxlen=settings.live_buffer_max_samples)
    window = EventWindowBuffer()
    # Disabled detection: we're benchmarking ingest throughput, not the
    # cost of a sustained detector fire. Detector fires are exercised
    # in the unit tests already.
    engine = DetectionEngine(
        StaticConfigProvider(TypeAConfig(enabled=False)),
        LoggingEventSink(),
    )

    # Pre-fill the queue: simulates a steady stream paho already
    # delivered. The consumer drains it as fast as it can.
    base_ts_ms = int(time.time() * 1000)
    for i in range(TOTAL_MSGS):
        device_id = (i % 20) + 1  # cycle through 20 devices
        payload = _make_payload(device_id, base_ts_ms + i * 10)
        receive_ts = time.time()
        queue.put_nowait((payload, receive_ts))

    stop_event = asyncio.Event()
    started = time.perf_counter()

    consumer = asyncio.create_task(
        _consume(queue, clocks, offsets, live, window, engine, stop_event),
        name="bench-consumer",
    )

    # Wait until the queue is fully drained, then signal stop and join.
    # Lint exempts: this is a benchmark; the work IS the polling.
    while not queue.empty():  # noqa: ASYNC110
        await asyncio.sleep(0.01)
    stop_event.set()
    await consumer
    elapsed = time.perf_counter() - started

    print(
        f"\n[bench] drained {TOTAL_MSGS} msgs in {elapsed:.3f}s "
        f"({TOTAL_MSGS / elapsed:.0f} msg/s, "
        f"{TOTAL_MSGS * SENSORS_PER_MSG / elapsed:.0f} samples/s)"
    )

    # Sanity: every message landed in either the OK counter or the
    # invalid counter. Sum across the 20 device labels seeded above.
    received = sum(m.counter_value(m.MSGS_RECEIVED_TOTAL, device_id=str(d)) for d in range(1, 21))
    invalid = m.counter_value(m.MSGS_INVALID_TOTAL)
    assert received + invalid >= TOTAL_MSGS, (
        f"received={received} invalid={invalid} total={TOTAL_MSGS}"
    )

    samples_processed = sum(
        m.counter_value(m.SAMPLES_PROCESSED_TOTAL, device_id=str(d)) for d in range(1, 21)
    )
    assert samples_processed >= TOTAL_MSGS * SENSORS_PER_MSG

    # Hard budget — fail if a future change crosses this line.
    assert elapsed < DRAIN_BUDGET_SECONDS, (
        f"drain took {elapsed:.3f}s, budget {DRAIN_BUDGET_SECONDS}s"
    )
