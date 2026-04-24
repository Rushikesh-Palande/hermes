"""
MQTT consumer skeleton.

Phase 1 goal: connect to the broker, subscribe to the ADC topic, log
that messages are arriving. No parsing, no offsetting, no detection —
those arrive in Phase 2.

Why paho-mqtt and not gmqtt or aiomqtt:
    * Proven in the legacy system at 123 Hz × 12 sensors × 20 devices
      with zero dropped frames observed.
    * paho's "loop_start" model runs network I/O on a background thread
      and callbacks from that thread. We publish onto an asyncio Queue
      in the callback; the async side does database writes without
      blocking the receive path.

Reconnection:
    paho's built-in reconnect handles transient broker flaps. We DO NOT
    wrap it with our own retry loop — that was a source of double-
    reconnect bugs in the legacy ingest (see BUG_DECISION_LOG for
    context on the `stm32_ts_offsets` re-anchoring behavior we preserve).
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

import paho.mqtt.client as mqtt

from hermes.config import get_settings
from hermes.logging import configure_logging, get_logger

log = get_logger(__name__, component="ingest")


async def run() -> None:
    """
    Main ingest loop. Connects, subscribes, and sits on an asyncio event
    until SIGTERM. The actual message processing is a no-op in Phase 1;
    we just log each arrival at DEBUG and keep the connection alive.
    """
    configure_logging()
    settings = get_settings()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    def on_connect(
        client: mqtt.Client,
        _userdata: Any,
        _flags: dict[str, int],
        reason_code: int,
        _props: Any = None,
    ) -> None:
        """Log the outcome; subscribe if connected."""
        if reason_code == 0:
            log.info("mqtt_connected", host=settings.mqtt_host, port=settings.mqtt_port)
            client.subscribe(settings.mqtt_topic_adc, qos=0)
        else:
            log.error("mqtt_connect_failed", reason_code=reason_code)

    def on_disconnect(
        _client: mqtt.Client,
        _userdata: Any,
        reason_code: int,
        _props: Any = None,
    ) -> None:
        # paho automatically reconnects; we only log here.
        log.warning("mqtt_disconnected", reason_code=reason_code)

    def on_message(_client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        # Phase 1: count-only. Phase 2: parse, offset, feed detectors.
        log.debug("mqtt_message", topic=msg.topic, size=len(msg.payload))

    # CallbackAPIVersion.VERSION2 matches the v2-style signatures above.
    # paho's 2.x type stubs don't re-export CallbackAPIVersion through the
    # `client` module; the attribute exists at runtime. Silence mypy here.
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
    log.info("ingest_running", topic=settings.mqtt_topic_adc)

    try:
        await stop_event.wait()
    finally:
        log.info("ingest_stopping")
        client.loop_stop()
        client.disconnect()
