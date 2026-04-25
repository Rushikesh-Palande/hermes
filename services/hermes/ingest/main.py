"""
MQTT consumer — parser, clock, offsets, detection, persistence.

Pipeline (all on the asyncio event loop after the callback hand-off):

    paho callback (background thread)
        → put raw (payload_bytes, receive_ts) on asyncio.Queue
              via loop.call_soon_threadsafe

    _consume() coroutine (event loop)
        → orjson.loads                  (Layer 1: orjson, not stdlib json)
        → optional shard filter         (Layer 3: drop non-owned devices)
        → parse_stm32_adc_payload   → {sensor_id: raw_float}
        → ClockRegistry.anchor      → wall_ts
        → OffsetCache.apply         → {sensor_id: corrected_float}
        → LiveDataHub.push          → live ring buffer (SSE reads from here)
        → EventWindowBuffer.push    → 30 s ring buffer (DB sink reads from here)
        → DetectionEngine.feed_snapshot   (skipped in live_only mode)
            → for each detected event:
                TtlGateSink → MultiplexEventSink → [DbEventSink, MqttEventSink]

    DbEventSink._writer_loop (event loop, background task)
        → for each event: wait until triggered_at + 9 s, slice the
          window buffer, write events + event_windows in one tx.

Operating modes (selected via ``Settings.hermes_ingest_mode``):
    * "all"        — single process subscribes to everything, runs
                     detection on all devices, fills live ring buffer.
                     Default. Bench: ~16 700 msg/s laptop / ~5 500 msg/s Pi 4.
    * "shard"      — one of N detection processes (Layer 3). Subscribes
                     to ``stm32/adc`` and discards messages where
                     ``device_id % shard_count != shard_index``.
                     Owns detection + DB sink + outbound MQTT for its
                     slice. Does NOT fill the live ring buffer.
    * "live_only"  — runs no detection; just keeps the live ring buffer
                     warm for SSE. Used by the API process when shards
                     are running.

Cross-shard config sync (Layer 3):
    The API process emits ``NOTIFY hermes_config_changed`` after committing
    a parameter update. Each detection shard's DbConfigProvider runs a
    LISTEN coroutine on that channel and reloads its provider + resets
    cached detectors when notified. Single-process deployments use the
    same code path; the API just reloads in-process.

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
import signal
import time
from typing import Any

import orjson
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
from hermes.detection.ttl_gate import TtlGateSink
from hermes.detection.types import EventSink
from hermes.detection.window_buffer import EventWindowBuffer
from hermes.ingest.clock import ClockRegistry
from hermes.ingest.live_data import LiveDataHub
from hermes.ingest.modbus import ModbusManager
from hermes.ingest.offsets import OffsetCache
from hermes.ingest.parser import parse_stm32_adc_payload
from hermes.ingest.session_samples import SessionSampleWriter
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
    detection: DetectionEngine | None,
    stop_event: asyncio.Event,
    shard_count: int = 1,
    shard_index: int = 0,
    sample_writer: SessionSampleWriter | None = None,
) -> None:
    """
    Drain the handoff queue and run the full ingestion pipeline.

    Runs until ``stop_event`` is set and the queue is empty.

    Layer 3 multi-process shard support (zero-cost when ``shard_count==1``):
      * If ``shard_count > 1``, messages where
        ``device_id % shard_count != shard_index`` are dropped after
        parse but before any work happens. The hash is intentionally
        the same on every shard (modulo of a stable integer key) so
        each device deterministically lands on exactly one shard.
      * If ``detection`` is None (e.g. API process running in
        ``live_only`` mode for SSE only), the detect step is skipped
        but live + window buffers still fill normally.

    Hot-path discipline (Layer 1 micro-opts):
      * ``orjson.loads`` instead of stdlib ``json`` — 3-5x faster on
        the small JSON envelopes STM32 emits.
      * Metrics module references (``_m.X``) and a few hot helpers are
        pre-bound to locals before the loop, dropping a global+attr
        lookup per sample at 24 000 samples/s.
      * No per-sample log. The ``MSGS_RECEIVED_TOTAL`` and
        ``SAMPLES_PROCESSED_TOTAL`` counters cover the same ground for
        debugging without paying structlog formatting overhead 24 000
        times a second. Errors and warnings still log fully.
      * ``time_stage`` context managers stay — they're sampled (1 in
        100 by default) so the steady-state cost is negligible, and
        we'd be flying blind without them.
    """
    # Pre-bind hot path attribute lookups to locals. CPython's LOAD_FAST
    # is several times cheaper than LOAD_GLOBAL+LOAD_ATTR; at 2 000 msg/s
    # this saves ~5-8 ms/s of pure interpreter overhead.
    queue_get = queue.get
    queue_qsize = queue.qsize
    metrics_consume_qdepth = _m.CONSUME_QUEUE_DEPTH
    metrics_msgs_invalid = _m.MSGS_INVALID_TOTAL
    metrics_msgs_received = _m.MSGS_RECEIVED_TOTAL
    metrics_samples_processed = _m.SAMPLES_PROCESSED_TOTAL
    metrics_time_stage = _m.time_stage
    orjson_loads = orjson.loads
    parse_payload = parse_stm32_adc_payload
    clocks_anchor = clocks.anchor
    offsets_apply = offsets.apply
    live_push = live.push
    window_push = window_buffer.push_snapshot
    # Detection feed is None in live_only mode (API process for SSE).
    detect_feed = detection.feed_snapshot if detection is not None else None
    # Continuous-sample writer (gap 6). When no session has
    # record_raw_samples=true, push_snapshot is a fast no-op (one
    # dict lookup + early return). Bound to a local so the hot path
    # avoids a global+attr lookup per snapshot.
    sample_push = sample_writer.push_snapshot if sample_writer is not None else None
    # Shard predicate: pre-built once. When count == 1 we never check it.
    sharded = shard_count > 1

    while not stop_event.is_set() or not queue.empty():
        try:
            raw_bytes, receive_ts = await asyncio.wait_for(queue_get(), timeout=0.1)
        except TimeoutError:
            continue

        # Queue depth gauge: cheap O(1) read on asyncio.Queue.
        metrics_consume_qdepth.set(queue_qsize())

        try:
            with metrics_time_stage("parse"):
                payload: dict[str, Any] = orjson_loads(raw_bytes)
        except orjson.JSONDecodeError:
            metrics_msgs_invalid.inc()
            log.warning("mqtt_bad_json", size=len(raw_bytes))
            continue

        device_id: int = int(payload.get("device_id", 1))
        # Shard filter: drop messages whose device isn't ours. Cheap
        # int modulo. Done before MSGS_RECEIVED_TOTAL so the counter
        # only ticks for messages this process actually processes —
        # summing the counter across all shards gives the true total.
        if sharded and device_id % shard_count != shard_index:
            continue
        device_label = str(device_id)
        metrics_msgs_received.labels(device_id=device_label).inc()

        # --- Timestamp anchoring ---
        dev_ts_ms = payload.get("ts")
        with metrics_time_stage("anchor"):
            if dev_ts_ms is not None:
                ts = clocks_anchor(device_id, receive_ts, float(dev_ts_ms) / 1000.0)
            else:
                ts = receive_ts

        # --- Parse ADC channels ---
        with metrics_time_stage("adc_parse"):
            sensor_values = parse_payload(payload)
        if not sensor_values:
            continue

        # --- Apply per-sensor calibration offsets ---
        with metrics_time_stage("offset"):
            sensor_values = offsets_apply(device_id, sensor_values)

        # --- Feed live ring buffer (SSE) and window buffer (event capture) ---
        with metrics_time_stage("buffers"):
            live_push(device_id, ts, sensor_values)
            window_push(device_id, ts, sensor_values)
            if sample_push is not None:
                # No-op early return when no session is recording.
                sample_push(device_id, ts, sensor_values)

        # Counter ticks once per sensor reading actually fed into the
        # pipeline. Same labelling as MSGS_RECEIVED_TOTAL so a Grafana
        # join is straightforward.
        metrics_samples_processed.labels(device_id=device_label).inc(len(sensor_values))

        # --- Feed detection engine (skipped in live_only mode) ---
        if detect_feed is not None:
            with metrics_time_stage("detect"):
                detect_feed(device_id, ts, sensor_values)


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
        self._mode = settings.hermes_ingest_mode
        self._shard_count = settings.hermes_shard_count
        self._shard_index = settings.hermes_shard_index
        self.live_data = LiveDataHub(maxlen=settings.live_buffer_max_samples)
        self.window_buffer = EventWindowBuffer()
        self.offset_cache = OffsetCache()
        self._clocks = ClockRegistry(drift_threshold_s=settings.mqtt_drift_threshold_s)

        # In live_only mode (multi-shard API process), we run no detection
        # and own no sinks — purpose is purely to keep the live ring
        # buffer warm for SSE. Detection shards do that work.
        run_detection = self._mode != "live_only"

        # Continuous-sample writer (gap 6). Created unconditionally so
        # the hot path doesn't need a None-check; ``push_snapshot`` is
        # a fast no-op when no session has ``record_raw_samples=true``.
        # The connection is opened lazily in ``start()`` so module
        # construction stays cheap and synchronous. live_only mode
        # (multi-shard API) skips it — detection shards own this path.
        self.session_sample_writer: SessionSampleWriter | None = None
        if run_detection:
            self.session_sample_writer = SessionSampleWriter(dsn=settings.migrate_database_url)

        # Modbus TCP poller manager (gap 7). Created in any mode that
        # runs detection; if no devices have ``protocol=modbus_tcp`` the
        # manager just idles between refresh ticks. live_only mode
        # skips it because polled snapshots have nowhere to land
        # without detection.
        self.modbus_manager: ModbusManager | None = None
        if run_detection:
            self.modbus_manager = ModbusManager(callback=self._on_modbus_snapshot)

        # Sinks: events fan out to (a) DB persistence and (b) outbound
        # MQTT topic, in that order. Use MultiplexEventSink so an outage
        # on one branch (e.g. broker down) doesn't silence the other.
        # In tests / no-session mode we drop DB and just log + publish.
        sinks: list[EventSink] = []
        self._db_sink: DbEventSink | None = None
        self.mqtt_event_sink: MqttEventSink | None = None
        self.ttl_gate: TtlGateSink | None = None
        self.detection_engine: DetectionEngine | None = None

        if run_detection:
            if session_id is not None:
                import uuid as _uuid

                assert isinstance(session_id, _uuid.UUID), "session_id must be a UUID when provided"
                self._db_sink = DbEventSink(
                    session_id=session_id,
                    window_buffer=self.window_buffer,
                )
                sinks.append(self._db_sink)
            else:
                sinks.append(LoggingEventSink())

            # Outbound MQTT publish to stm32/events/<dev>/<sid>/<TYPE>. The
            # paho client doesn't exist yet (we connect in start()); we
            # attach it later. Pre-start fires log + drop, no crash.
            self.mqtt_event_sink = MqttEventSink(
                base_topic=settings.mqtt_topic_events_prefix,
            )
            sinks.append(self.mqtt_event_sink)

            # Default to a static all-disabled provider so a fresh
            # deployment is silent until thresholds are written via
            # /api/config. The API lifespan swaps in a DbConfigProvider
            # once a session exists.
            if config_provider is None:
                config_provider = StaticConfigProvider(TypeAConfig(enabled=False))

            # TTL gate sits between the detector and the durable sinks.
            # Bursts of same-type events on the same sensor collapse to
            # one forwarded event; lower-priority types are blocked
            # while a higher-priority is armed; BREAK bypasses entirely.
            # The gate also exposes a flush() called on shutdown so we
            # don't lose held events.
            self.ttl_gate = TtlGateSink(
                child=MultiplexEventSink(sinks),
                ttl_seconds=settings.event_ttl_seconds,
            )
            self.detection_engine = DetectionEngine(
                config_provider=config_provider,
                sink=self.ttl_gate,
            )

        self._stop_event = asyncio.Event()
        self._queue: asyncio.Queue[tuple[bytes, float]] = asyncio.Queue()
        self._consumer_task: asyncio.Task[None] | None = None
        self._client: mqtt.Client | None = None

    def _on_modbus_snapshot(
        self, device_id: int, ts: float, sensor_values: dict[int, float]
    ) -> None:
        """Downstream callback for ``ModbusPoller``.

        Modbus snapshots take the same downstream path as MQTT
        snapshots — offset correction, live + window buffers,
        detection, sample writer — but skip the parts unique to MQTT
        (JSON parse, STM32 clock anchoring). Local poll time IS the
        wall clock for Modbus, so we use ``ts`` as-is.

        Called from the asyncio event loop only; safe to mutate the
        same buffers as ``_consume`` because they're single-threaded
        within the loop.
        """
        # Offset correction (per-sensor calibration applies to either source).
        sensor_values = self.offset_cache.apply(device_id, sensor_values)

        # Live + window buffers — same writes as the MQTT path.
        self.live_data.push(device_id, ts, sensor_values)
        self.window_buffer.push_snapshot(device_id, ts, sensor_values)

        # Counter ticks: count each Modbus reading the same way an MQTT
        # one is counted, so Grafana sums work uniformly.
        device_label = str(device_id)
        _m.MSGS_RECEIVED_TOTAL.labels(device_id=device_label).inc()
        _m.SAMPLES_PROCESSED_TOTAL.labels(device_id=device_label).inc(len(sensor_values))

        # Detection. None in live_only mode (Modbus manager isn't
        # constructed there, so we should never hit this branch with
        # detection_engine == None — guard anyway).
        if self.detection_engine is not None:
            self.detection_engine.feed_snapshot(device_id, ts, sensor_values)

        # Continuous-sample writer (gap 6). Same fast no-op when no
        # session is recording.
        if self.session_sample_writer is not None:
            self.session_sample_writer.push_snapshot(device_id, ts, sensor_values)

    async def start(self) -> None:
        """Connect to MQTT, load offsets, start writer + consumer tasks."""
        try:
            await _load_sensor_offsets(self.offset_cache)
        except Exception:
            log.warning("offset_load_failed_continuing", exc_info=True)

        if self._db_sink is not None:
            await self._db_sink.start()

        # Best-effort start of the continuous-sample writer. A connect
        # failure here is non-fatal — the hot path stays a no-op until
        # the writer is healthy on the next start cycle.
        if self.session_sample_writer is not None:
            try:
                await self.session_sample_writer.start()
            except Exception:
                log.exception("session_samples_writer_start_failed")

        # Modbus poller manager (gap 7). Best-effort: a query failure
        # here means we miss the initial discovery; the refresh loop
        # will pick up devices on the next cycle.
        if self.modbus_manager is not None:
            try:
                await self.modbus_manager.start()
            except Exception:
                log.exception("modbus_manager_start_failed")

        log.info(
            "ingest_starting",
            mode=self._mode,
            shard_index=self._shard_index,
            shard_count=self._shard_count,
        )

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
        # detected events can publish back over MQTT. live_only has no
        # sink because it runs no detection.
        if self.mqtt_event_sink is not None:
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
                shard_count=self._shard_count,
                shard_index=self._shard_index,
                sample_writer=self.session_sample_writer,
            ),
            name="mqtt-consumer",
        )

        log.info("ingest_running", topic=settings.mqtt_topic_adc)

    async def stop(self) -> None:
        """Signal the consumer, drain the queue, disconnect MQTT, stop the writer."""
        log.info("ingest_stopping")
        self._stop_event.set()
        # Stop the Modbus manager BEFORE the consumer drains so no new
        # snapshots arrive into a torn-down detection engine. Pollers
        # cancel their own tasks and close their TCP clients.
        if self.modbus_manager is not None:
            try:
                await self.modbus_manager.stop()
            except Exception:
                log.exception("modbus_manager_stop_failed")
        if self._consumer_task is not None:
            await self._consumer_task
        # Force-forward any TTL-held events so a graceful shutdown
        # doesn't silently drop a burst that was still inside the
        # 5 s dedup window. Skipped in live_only mode (no detection).
        if self.ttl_gate is not None:
            self.ttl_gate.flush()
        # Drop the client reference on the outbound sink BEFORE
        # disconnecting paho — any event still in flight will skip the
        # publish (logged warn) instead of racing a torn-down client.
        if self.mqtt_event_sink is not None:
            self.mqtt_event_sink.detach_client()
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
        if self._db_sink is not None:
            await self._db_sink.stop()
        # Stop the continuous-sample writer last so any final samples
        # buffered during shutdown still flush to session_samples.
        if self.session_sample_writer is not None:
            try:
                await self.session_sample_writer.stop()
            except Exception:
                log.exception("session_samples_writer_stop_failed")


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

    # Layer 3: subscribe to config-changed notifications. Single-process
    # deployments use the same code path; the API still emits NOTIFY but
    # has nothing to do because it reloaded its own provider in-process.
    # Multi-shard deployments rely on this LISTEN to invalidate caches
    # in detection shards when the operator updates thresholds via UI.
    if config_provider is not None:
        try:
            await config_provider.start_listener(
                dsn=settings.migrate_database_url,
                engine=pipeline.detection_engine,
            )
        except Exception:
            log.exception("config_listener_start_failed_continuing")

    try:
        await stop_event.wait()
    finally:
        if config_provider is not None:
            try:
                await config_provider.stop_listener()
            except Exception:
                log.exception("config_listener_stop_failed")
        await pipeline.stop()
        await dispose_engine()
