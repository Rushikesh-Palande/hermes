"""
Integration tests for /api/system-tunables (gap 8).

Single read-only endpoint; we verify:
  * Live state counts reflect real DB rows.
  * The tunables list contains the documented runtime knobs with
    sensible default values.
  * Unknown / sensitive fields don't leak (no JWT secret, no DB URL).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from hermes.db.engine import async_session
from hermes.db.models import (
    Device,
    DeviceProtocol,
    Package,
    SessionScope,
)
from hermes.db.models import (
    Session as SessionRow,
)


@pytest.mark.db
@pytest.mark.asyncio
async def test_returns_state_with_bootstrap_session(api_client: AsyncClient) -> None:
    """The conftest's api_client fixture starts an active GLOBAL; surface it."""
    resp = await api_client.get("/api/system-tunables")
    assert resp.status_code == 200
    body = resp.json()
    state = body["state"]
    assert state["ingest_mode"] == "all"
    assert state["shard_count"] == 1
    assert state["shard_index"] == 0
    assert state["active_global_session_id"] is not None
    assert state["active_local_session_count"] == 0
    assert state["sessions_recording_count"] == 0


@pytest.mark.db
@pytest.mark.asyncio
async def test_recording_count_reflects_record_raw_samples_flag(
    api_client: AsyncClient,
) -> None:
    """Adding a recording-on session bumps the counter."""
    async with async_session() as s:
        pkg = Package(name="rec", is_default=False)
        s.add(pkg)
        await s.flush()
        # End the bootstrap global so we can start a recording one.
        await s.execute(
            update(SessionRow)
            .where(
                SessionRow.scope == SessionScope.GLOBAL,
                SessionRow.ended_at.is_(None),
            )
            .values(ended_at=SessionRow.started_at)
        )
        s.add(
            SessionRow(
                scope=SessionScope.GLOBAL,
                package_id=pkg.package_id,
                record_raw_samples=True,
                started_by="test",
            )
        )
        await s.commit()

    body = (await api_client.get("/api/system-tunables")).json()
    assert body["state"]["sessions_recording_count"] == 1


@pytest.mark.db
@pytest.mark.asyncio
async def test_device_counts_split_by_protocol(api_client: AsyncClient) -> None:
    async with async_session() as s:
        s.add(Device(device_id=1, name="mqtt-a", protocol=DeviceProtocol.MQTT))
        s.add(Device(device_id=2, name="mqtt-b", protocol=DeviceProtocol.MQTT))
        s.add(
            Device(
                device_id=3,
                name="modbus-a",
                protocol=DeviceProtocol.MODBUS_TCP,
                modbus_config={"host": "h", "port": 502, "register_start": 0},
            )
        )
        await s.commit()

    body = (await api_client.get("/api/system-tunables")).json()
    assert body["state"]["mqtt_devices_active"] == 2
    assert body["state"]["modbus_devices_active"] == 1


@pytest.mark.db
@pytest.mark.asyncio
async def test_inactive_devices_dont_count(api_client: AsyncClient) -> None:
    """Soft-disabled devices must not show up in the active counts."""
    async with async_session() as s:
        s.add(Device(device_id=10, name="off", protocol=DeviceProtocol.MQTT, is_active=False))
        await s.commit()
    body = (await api_client.get("/api/system-tunables")).json()
    assert body["state"]["mqtt_devices_active"] == 0


@pytest.mark.db
@pytest.mark.asyncio
async def test_tunables_list_is_well_known(api_client: AsyncClient) -> None:
    body = (await api_client.get("/api/system-tunables")).json()
    keys = {row["key"] for row in body["tunables"]}
    # The documented set; if a future PR removes one, the UI's display
    # should be updated together — this test catches drift loudly.
    assert "event_ttl_seconds" in keys
    assert "live_buffer_max_samples" in keys
    assert "mqtt_drift_threshold_s" in keys
    assert "hermes_jwt_expiry_seconds" in keys
    assert "otp_expiry_seconds" in keys
    assert "otp_max_per_hour" in keys
    assert "detector_thresholds" in keys
    assert "mqtt_brokers" in keys


@pytest.mark.db
@pytest.mark.asyncio
async def test_no_secret_fields_in_response(api_client: AsyncClient) -> None:
    """Sensitive fields (JWT secret, DB URLs) must NEVER appear."""
    raw = (await api_client.get("/api/system-tunables")).text
    assert "DATABASE_URL" not in raw
    assert "jwt_secret" not in raw.lower()
    assert "smtp_pass" not in raw.lower()
    assert "password" not in raw.lower()


@pytest.mark.db
@pytest.mark.asyncio
async def test_editable_field_distinguishes_routes(api_client: AsyncClient) -> None:
    body = (await api_client.get("/api/system-tunables")).json()
    by_key = {row["key"]: row for row in body["tunables"]}
    # Detector thresholds explicitly point at /api/config.
    assert by_key["detector_thresholds"]["editable"] == "via_other_route"
    assert "Config" in by_key["detector_thresholds"]["edit_hint"]
    # MQTT brokers point at /api/mqtt-brokers.
    assert by_key["mqtt_brokers"]["editable"] == "via_other_route"
    # Settings-style fields require a restart.
    assert by_key["event_ttl_seconds"]["editable"] == "restart"


@pytest.mark.db
@pytest.mark.asyncio
async def test_version_reported(api_client: AsyncClient) -> None:
    body = (await api_client.get("/api/system-tunables")).json()
    assert body["state"]["version"]
    # Value comes from importlib.metadata when installed; pytest sees
    # "0.1.0a21" or similar — at minimum it shouldn't be the unknown
    # fallback in a test environment that has the package installed.
    assert body["state"]["version"] != "0.0.0+unknown"
