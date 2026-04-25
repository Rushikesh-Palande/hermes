"""
Unit tests for ``GET /api/metrics``.

Asserts the endpoint returns Prometheus text-format and includes the
metric names we ship. No DB or broker required — the route just calls
``prometheus_client.generate_latest()``.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from hermes import metrics as m


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prom_format(api_client: AsyncClient) -> None:
    # Touch every counter so the exposition includes them.
    m.MSGS_RECEIVED_TOTAL.labels(device_id="1").inc()
    m.SAMPLES_PROCESSED_TOTAL.labels(device_id="1").inc(12)
    m.EVENTS_DETECTED_TOTAL.labels(event_type="A", device_id="1").inc()
    m.MSGS_INVALID_TOTAL.inc()
    m.CONSUME_QUEUE_DEPTH.set(7)
    m.MQTT_CONNECTED.set(1)

    resp = await api_client.get("/api/metrics")
    assert resp.status_code == 200
    body = resp.text

    # Counters
    assert "hermes_msgs_received_total" in body
    assert "hermes_samples_processed_total" in body
    assert "hermes_events_detected_total" in body
    assert "hermes_msgs_invalid_total" in body
    # Gauges
    assert "hermes_consume_queue_depth" in body
    assert "hermes_mqtt_connected" in body
    # Histogram (defined even before any observation so prom keeps the
    # HELP / TYPE lines).
    assert "hermes_pipeline_stage_duration_seconds" in body
    # Format markers — generate_latest is the canonical Prom format.
    assert "# TYPE" in body
    assert "# HELP" in body
