"""
MQTT consumer — Phase 2: real parsing, clock anchoring, offset correction.

Pipeline (all on the asyncio event loop after the callback hand-off):

    paho callback (background thread)
        → put raw (payload_bytes, receive_ts) on asyncio.Queue
              via loop.call_soon_threadsafe

    _consume() coroutine (event loop)
        → json.loads
        → parse_stm32_adc_payload   → {sensor_id: raw_float}
        → ClockRegistry.anchor      → wall_ts
        → OffsetCache.apply         → {sensor_id: corrected_float}
        → LiveDataHub.push          → ring buffer (SSE reads from here)

Embedding in the API process (Phase 2):
    The API lifespan creates an IngestPipeline and stores its ``live_data``
    handle on ``app.state``. The SSE endpoint reads from there.

Standalone mode:
    ``hermes-ingest`` calls ``run()``, which owns the SIGTERM handler and
    runs until the process is killed.

Why paho-mqtt and not gmqtt or aiomqtt:
    Proven in the legacy system at 123 Hz × 12 sensors × 20 devices
    with zero dropped frames observed. paho's loop_start model runs
    network I/O on a background thread; the callback hands off to
    asyncio via call_soon_threadsafe so neither side blocks the other.

Reconnection:
    paho's built-in reconnect handles transient broker flaps. We do NOT
    wrap it in our own retry loop — that caused double-reconnect bugs in
    the legacy system (see BUG_DECISION_LOG).
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from typing import Any

import paho.mqtt.client as mqtt
from sqlalchemy import select

from hermes.config import Settings, get_settings
from hermes.db.engine import async_session, dispose_engine
from hermes.db.models import SensorOffset
from hermes.ingest.clock import ClockRegistry
from hermes.ingest.live_data import LiveDataHub
from hermes.ingest.offsets import OffsetCache
from hermes.ingest.parser import parse_stm32_adc_payload
from hermes.logging import configure_logging, get_logger

log = get_logger(__name__, component="ingest")


async def _load_sensor_offsets(cache: OffsetCache) -> None:
    """
    Populate OffsetCache from the ``sensor_offsets`` DB table.

    Runs once at startup; call again per-device when the operator updates
    calibration via the API.
    """
    by_device: dict[int, dict[int, float]] = {}
    async with async_session() as session:
        rows = await session.execute(select(SensorOffset))
        for row in rows.scalars().all():
            by_device.setdefault(row.device_id, {})[row.sensor_id] = float(row.offset_value)
    for device_id, offsets in by_device.items():
        cache.load(device_id, offsets)
    log.info("offsets_loaded", device_count=len(by_device))


async def _consume(
    queue: asyncio.Queue[tuple[bytes, float]],
    clocks: ClockRegistry,
    offsets: OffsetCache,
    live: LiveDataHub,
    stop_event: asyncio.Event,
) -> None:
    """
    Drain the handoff queue and run the full ingestion pipeline.

    Runs until ``stop_event`` is set and the queue is empty.
    """
    while not stop_event.is_set() or not queue.empty():
        try:
            raw_bytes, receive_ts = await asyncio.wait_for(queue.get(), timeout=0.1)
        except TimeoutError:
            continue

        try:
            payload: dict[str, Any] = json.loads(raw_bytes)
        except json.JSONDecodeError:
            log.warning("mqtt_bad_json", size=len(raw_bytes))
            continue

        device_id: int = int(payload.get("device_id", 1))

        # --- Timestamp anchoring ---
        dev_ts_ms = payload.get("ts")
        if dev_ts_ms is not None:
            ts = clocks.anchor(device_id, receive_ts, float(dev_ts_ms) / 1000.0)
        else:
            ts = receive_ts

        # --- Parse ADC channels ---
        sensor_values = parse_stm32_adc_payload(payload)
        if not sensor_values:
            continue

        # --- Apply per-sensor calibration offsets ---
        sensor_values = offsets.apply(device_id, sensor_values)

        # --- Feed live ring buffer ---
        live.push(device_id, ts, sensor_values)

        log.debug(
            "sample_ingested",
            device_id=device_id,
            ts=ts,
            sensors=len(sensor_values),
        )


class IngestPipeline:
    """
    Encapsulates the MQTT connection, consumer task, and shared state.

    Used by the API lifespan to embed ingestion in the same asyncio loop:

        pipeline = IngestPipeline(settings)
        await pipeline.start()
        app.state.live_data = pipeline.live_data
        # ... serve requests ...
        await pipeline.stop()

    The standalone ``run()`` function below wraps this class for CLI use.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.live_data = LiveDataHub(maxlen=settings.live_buffer_max_samples)
        self._offsets = OffsetCache()
        self._clocks = ClockRegistry(drift_threshold_s=settings.mqtt_drift_threshold_s)
        self._stop_event = asyncio.Event()
        self._queue: asyncio.Queue[tuple[bytes, float]] = asyncio.Queue()
        self._consumer_task: asyncio.Task[None] | None = None
        self._client: mqtt.Client | None = None

    async def start(self) -> None:
        """Connect to MQTT, load offsets, start the consumer task."""
        try:
            await _load_sensor_offsets(self._offsets)
        except Exception:
            log.warning("offset_load_failed_continuing", exc_info=True)

        loop = asyncio.get_running_loop()
        settings = self._settings

        def on_connect(
            client: mqtt.Client,
            _userdata: Any,
            _flags: dict[str, int],
            reason_code: int,
            _props: Any = None,
        ) -> None:
            if reason_code == 0:
                log.info(
                    "mqtt_connected",
                    host=settings.mqtt_host,
                    port=settings.mqtt_port,
                )
                client.subscribe(settings.mqtt_topic_adc, qos=0)
            else:
                log.error("mqtt_connect_failed", reason_code=reason_code)

        def on_disconnect(
            _client: mqtt.Client,
            _userdata: Any,
            reason_code: int,
            _props: Any = None,
        ) -> None:
            log.warning("mqtt_disconnected", reason_code=reason_code)

        def on_message(_client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
            receive_ts = time.time()
            loop.call_soon_threadsafe(self._queue.put_nowait, (msg.payload, receive_ts))

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
            client_id="hermes-ingest",
            clean_session=True,
        )
        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        if settings.mqtt_username:
            client.username_pw_set(
                settings.mqtt_username,
                settings.mqtt_password.get_secret_value(),
            )

        client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        client.loop_start()
        self._client = client

        self._consumer_task = asyncio.create_task(
            _consume(
                self._queue,
                self._clocks,
                self._offsets,
                self.live_data,
                self._stop_event,
            ),
            name="mqtt-consumer",
        )

        log.info("ingest_running", topic=settings.mqtt_topic_adc)

    async def stop(self) -> None:
        """Signal the consumer, drain the queue, disconnect MQTT."""
        log.info("ingest_stopping")
        self._stop_event.set()
        if self._consumer_task is not None:
            await self._consumer_task
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()


async def run() -> None:
    """
    Standalone entry point. Connects, processes, exits on SIGTERM / SIGINT.

    Called by the ``hermes-ingest`` console script (see pyproject.toml).
    """
    configure_logging()
    settings = get_settings()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    pipeline = IngestPipeline(settings)
    await pipeline.start()

    try:
        await stop_event.wait()
    finally:
        await pipeline.stop()
        await dispose_engine()
