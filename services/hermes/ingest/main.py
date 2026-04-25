"""
MQTT consumer — Phase 2 + 3 (parser, clock, offsets, detection, persistence).

Pipeline (all on the asyncio event loop after the callback hand-off):

    paho callback (background thread)
        → put raw (payload_bytes, receive_ts) on asyncio.Queue
              via loop.call_soon_threadsafe

    _consume() coroutine (event loop)
        → json.loads
        → parse_stm32_adc_payload   → {sensor_id: raw_float}
        → ClockRegistry.anchor      → wall_ts
        → OffsetCache.apply         → {sensor_id: corrected_float}
        → LiveDataHub.push          → live ring buffer (SSE reads from here)
        → EventWindowBuffer.push    → 30 s ring buffer (DB sink reads from here)
        → DetectionEngine.feed_snapshot
            → for each detected event: DbEventSink.publish (queues for write)

    DbEventSink._writer_loop (event loop, background task)
        → for each event: wait until triggered_at + 9 s, slice the
          window buffer, write events + event_windows in one tx.

Embedding in the API process:
    The API lifespan creates an IngestPipeline and stores its
    ``live_data`` handle on ``app.state``. The SSE endpoint reads from
    there. The DB sink is started/stopped alongside the pipeline.

Standalone mode:
    ``hermes-ingest`` calls ``run()``, which owns the SIGTERM handler.

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

from hermes import metrics as _m
from hermes.config import Settings, get_settings
from hermes.db.engine import async_session, dispose_engine
from hermes.db.models import SensorOffset
from hermes.detection.config import (
    DetectorConfigProvider,
    StaticConfigProvider,
    TypeAConfig,
)
from hermes.detection.db_sink import DbEventSink
from hermes.detection.engine import DetectionEngine
from hermes.detection.mqtt_sink import MqttEventSink
from hermes.detection.session import ensure_default_session
from hermes.detection.sink import LoggingEventSink, MultiplexEventSink
from hermes.detection.types import EventSink
from hermes.detection.window_buffer import EventWindowBuffer
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
    window_buffer: EventWindowBuffer,
    detection: DetectionEngine,
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

        # Update queue depth gauge after each successful dequeue. Cheap;
        # ``qsize`` is O(1) on asyncio.Queue.
        _m.CONSUME_QUEUE_DEPTH.set(queue.qsize())

        try:
            with _m.time_stage("parse"):
                payload: dict[str, Any] = json.loads(raw_bytes)
        except json.JSONDecodeError:
            _m.MSGS_INVALID_TOTAL.inc()
            log.warning("mqtt_bad_json", size=len(raw_bytes))
            continue

        device_id: int = int(payload.get("device_id", 1))
        device_label = str(device_id)
        _m.MSGS_RECEIVED_TOTAL.labels(device_id=device_label).inc()

        # --- Timestamp anchoring ---
        dev_ts_ms = payload.get("ts")
        with _m.time_stage("anchor"):
            if dev_ts_ms is not None:
                ts = clocks.anchor(device_id, receive_ts, float(dev_ts_ms) / 1000.0)
            else:
                ts = receive_ts

        # --- Parse ADC channels ---
        with _m.time_stage("adc_parse"):
            sensor_values = parse_stm32_adc_payload(payload)
        if not sensor_values:
            continue

        # --- Apply per-sensor calibration offsets ---
        with _m.time_stage("offset"):
            sensor_values = offsets.apply(device_id, sensor_values)

        # --- Feed live ring buffer (SSE) and window buffer (event capture) ---
        with _m.time_stage("buffers"):
            live.push(device_id, ts, sensor_values)
            window_buffer.push_snapshot(device_id, ts, sensor_values)

        # Counter ticks once per sensor reading actually fed into the
        # pipeline. Same labelling as MSGS_RECEIVED_TOTAL so a Grafana
        # join is straightforward.
        _m.SAMPLES_PROCESSED_TOTAL.labels(device_id=device_label).inc(len(sensor_values))

        # --- Feed detection engine ---
        with _m.time_stage("detect"):
            detection.feed_snapshot(device_id, ts, sensor_values)

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

    DB persistence:
        If a session_id is provided at construction, the pipeline uses
        ``DbEventSink`` (writes events + windows). If None, it falls back
        to ``LoggingEventSink`` (no DB writes — useful for tests and for
        running ingest before the schema is provisioned).
    """

    def __init__(
        self,
        settings: Settings,
        session_id: object | None = None,
        config_provider: DetectorConfigProvider | None = None,
    ) -> None:
        self._settings = settings
        self.live_data = LiveDataHub(maxlen=settings.live_buffer_max_samples)
        self.window_buffer = EventWindowBuffer()
        self.offset_cache = OffsetCache()
        self._clocks = ClockRegistry(drift_threshold_s=settings.mqtt_drift_threshold_s)

        # Sinks: events fan out to (a) DB persistence and (b) outbound
        # MQTT topic, in that order. Use MultiplexEventSink so an outage
        # on one branch (e.g. broker down) doesn't silence the other.
        # In tests / no-session mode we drop DB and just log + publish.
        sinks: list[EventSink] = []
        if session_id is not None:
            import uuid as _uuid

            assert isinstance(session_id, _uuid.UUID), "session_id must be a UUID when provided"
            self._db_sink: DbEventSink | None = DbEventSink(
                session_id=session_id,
                window_buffer=self.window_buffer,
            )
            sinks.append(self._db_sink)
        else:
            self._db_sink = None
            sinks.append(LoggingEventSink())

        # Outbound MQTT publish to stm32/events/<dev>/<sid>/<TYPE>. The
        # paho client doesn't exist yet (we connect in start()); we
        # attach it later. Pre-start fires log + drop, no crash.
        self.mqtt_event_sink = MqttEventSink(
            base_topic=settings.mqtt_topic_events_prefix,
        )
        sinks.append(self.mqtt_event_sink)

        # Default to a static all-disabled provider so a fresh deployment
        # is silent until thresholds are written via /api/config. The API
        # lifespan swaps in a DbConfigProvider once a session exists.
        if config_provider is None:
            config_provider = StaticConfigProvider(TypeAConfig(enabled=False))
        self.detection_engine = DetectionEngine(
            config_provider=config_provider,
            sink=MultiplexEventSink(sinks),
        )
        self._stop_event = asyncio.Event()
        self._queue: asyncio.Queue[tuple[bytes, float]] = asyncio.Queue()
        self._consumer_task: asyncio.Task[None] | None = None
        self._client: mqtt.Client | None = None

    async def start(self) -> None:
        """Connect to MQTT, load offsets, start writer + consumer tasks."""
        try:
            await _load_sensor_offsets(self.offset_cache)
        except Exception:
            log.warning("offset_load_failed_continuing", exc_info=True)

        if self._db_sink is not None:
            await self._db_sink.start()

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
                _m.MQTT_CONNECTED.set(1)
                client.subscribe(settings.mqtt_topic_adc, qos=0)
            else:
                log.error("mqtt_connect_failed", reason_code=reason_code)
                _m.MQTT_CONNECTED.set(0)

        def on_disconnect(
            _client: mqtt.Client,
            _userdata: Any,
            reason_code: int,
            _props: Any = None,
        ) -> None:
            log.warning("mqtt_disconnected", reason_code=reason_code)
            _m.MQTT_CONNECTED.set(0)

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
        # Wire the same paho client into the outbound event sink so
        # detected events can publish back over MQTT.
        self.mqtt_event_sink.attach_client(client)

        self._consumer_task = asyncio.create_task(
            _consume(
                self._queue,
                self._clocks,
                self.offset_cache,
                self.live_data,
                self.window_buffer,
                self.detection_engine,
                self._stop_event,
            ),
            name="mqtt-consumer",
        )

        log.info("ingest_running", topic=settings.mqtt_topic_adc)

    async def stop(self) -> None:
        """Signal the consumer, drain the queue, disconnect MQTT, stop the writer."""
        log.info("ingest_stopping")
        self._stop_event.set()
        if self._consumer_task is not None:
            await self._consumer_task
        # Drop the client reference on the outbound sink BEFORE
        # disconnecting paho — any event still in flight will skip the
        # publish (logged warn) instead of racing a torn-down client.
        self.mqtt_event_sink.detach_client()
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
        if self._db_sink is not None:
            await self._db_sink.stop()


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

    # Bootstrap a default Package + Session + DbConfigProvider so events
    # have somewhere to land and the operator can write thresholds via
    # the API. Failures fall back to the logging sink so the ingest path
    # still works against a not-yet-provisioned DB.
    from hermes.detection.db_config import DbConfigProvider

    session_id: object | None = None
    config_provider: DbConfigProvider | None = None
    try:
        session_id, package_id = await ensure_default_session()
        config_provider = DbConfigProvider(package_id)
        await config_provider.reload()
    except Exception:
        log.exception("session_bootstrap_failed_continuing")

    pipeline = IngestPipeline(settings, session_id=session_id, config_provider=config_provider)
    await pipeline.start()

    try:
        await stop_event.wait()
    finally:
        await pipeline.stop()
        await dispose_engine()
