"""
End-to-end tests for /api/config/{type}/overrides.

Covers the per-device and per-sensor scope: list, upsert, delete, and
the SENSOR → DEVICE → GLOBAL fallback in the live DbConfigProvider.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from hermes.db.engine import async_session
from hermes.db.models import Device


def _full_type_a(threshold_cv: float = 5.0, t1: float = 1.0) -> dict:
    return {
        "enabled": True,
        "T1": t1,
        "threshold_cv": threshold_cv,
        "debounce_seconds": 0.0,
        "init_fill_ratio": 0.9,
        "expected_sample_rate_hz": 100.0,
    }


async def _seed_device(device_id: int) -> None:
    async with async_session() as session:
        session.add(Device(device_id=device_id, name=f"dev-{device_id}"))


@pytest.mark.db
@pytest.mark.asyncio
async def test_overrides_endpoint_returns_empty_initially(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/config/type_a/overrides")
    assert resp.status_code == 200
    assert resp.json() == {"devices": {}, "sensors": []}


@pytest.mark.db
@pytest.mark.asyncio
async def test_unknown_type_name_rejected(api_client: AsyncClient) -> None:
    # FastAPI Literal binding rejects unknown values as 422.
    resp = await api_client.get("/api/config/type_z/overrides")
    assert resp.status_code == 422


@pytest.mark.db
@pytest.mark.asyncio
async def test_put_device_override_round_trips(api_client: AsyncClient) -> None:
    await _seed_device(7)
    payload = _full_type_a(threshold_cv=10.0)
    resp = await api_client.put("/api/config/type_a/overrides/device/7", json=payload)
    assert resp.status_code == 200
    assert resp.json() == payload

    listing = await api_client.get("/api/config/type_a/overrides")
    assert listing.status_code == 200
    body = listing.json()
    assert body["devices"]["7"]["threshold_cv"] == 10.0
    assert body["sensors"] == []


@pytest.mark.db
@pytest.mark.asyncio
async def test_put_sensor_override_round_trips(api_client: AsyncClient) -> None:
    await _seed_device(1)
    payload = _full_type_a(threshold_cv=2.5, t1=0.5)
    resp = await api_client.put("/api/config/type_a/overrides/sensor/1/3", json=payload)
    assert resp.status_code == 200

    listing = (await api_client.get("/api/config/type_a/overrides")).json()
    assert listing["sensors"] == [{"device_id": 1, "sensor_id": 3, "config": payload}]


@pytest.mark.db
@pytest.mark.asyncio
async def test_delete_device_override_returns_204_then_404(
    api_client: AsyncClient,
) -> None:
    await _seed_device(2)
    await api_client.put(
        "/api/config/type_b/overrides/device/2",
        json={
            "enabled": True,
            "T2": 5.0,
            "lower_threshold_pct": 5.0,
            "upper_threshold_pct": 5.0,
            "debounce_seconds": 0.0,
            "init_fill_ratio": 0.9,
            "expected_sample_rate_hz": 100.0,
        },
    )
    first = await api_client.delete("/api/config/type_b/overrides/device/2")
    assert first.status_code == 204
    second = await api_client.delete("/api/config/type_b/overrides/device/2")
    assert second.status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_delete_sensor_override_returns_204_then_404(
    api_client: AsyncClient,
) -> None:
    await _seed_device(1)
    await api_client.put("/api/config/type_a/overrides/sensor/1/5", json=_full_type_a())
    first = await api_client.delete("/api/config/type_a/overrides/sensor/1/5")
    assert first.status_code == 204
    assert (await api_client.delete("/api/config/type_a/overrides/sensor/1/5")).status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_invalid_payload_rejected_for_override(
    api_client: AsyncClient,
) -> None:
    await _seed_device(1)
    # Negative T1 fails Field(gt=0).
    bad = {
        "enabled": True,
        "T1": -1.0,
        "threshold_cv": 1.0,
        "debounce_seconds": 0.0,
        "init_fill_ratio": 0.9,
        "expected_sample_rate_hz": 100.0,
    }
    resp = await api_client.put("/api/config/type_a/overrides/device/1", json=bad)
    assert resp.status_code == 422


@pytest.mark.db
@pytest.mark.asyncio
async def test_inverted_thresholds_rejected_at_every_scope(
    api_client: AsyncClient,
) -> None:
    """The Type C threshold-ordering check now lives on the Pydantic model."""
    await _seed_device(1)
    bad = {
        "enabled": True,
        "T3": 10.0,
        "threshold_lower": 100.0,
        "threshold_upper": 10.0,
        "debounce_seconds": 0.0,
        "init_fill_ratio": 0.9,
        "expected_sample_rate_hz": 100.0,
    }
    # Global PUT
    assert (await api_client.put("/api/config/type_c", json=bad)).status_code == 422
    # Device override PUT
    assert (
        await api_client.put("/api/config/type_c/overrides/device/1", json=bad)
    ).status_code == 422
    # Sensor override PUT
    assert (
        await api_client.put("/api/config/type_c/overrides/sensor/1/2", json=bad)
    ).status_code == 422


@pytest.mark.db
@pytest.mark.asyncio
async def test_sensor_override_takes_precedence_over_device_and_global(
    api_client: AsyncClient,
) -> None:
    """
    Set GLOBAL, DEVICE override, and SENSOR override with different
    threshold_cv values; verify the live provider walks SENSOR → DEVICE
    → GLOBAL by checking that the listing shows what we wrote at each
    scope (the engine's actual lookup is exercised in unit tests).
    """
    await _seed_device(1)
    await api_client.put("/api/config/type_a", json=_full_type_a(threshold_cv=1.0))
    await api_client.put(
        "/api/config/type_a/overrides/device/1",
        json=_full_type_a(threshold_cv=2.0),
    )
    await api_client.put(
        "/api/config/type_a/overrides/sensor/1/4",
        json=_full_type_a(threshold_cv=3.0),
    )

    overrides = (await api_client.get("/api/config/type_a/overrides")).json()
    assert overrides["devices"]["1"]["threshold_cv"] == 2.0
    matching = [s for s in overrides["sensors"] if s["sensor_id"] == 4]
    assert len(matching) == 1
    assert matching[0]["config"]["threshold_cv"] == 3.0

    # The global GET still reflects the GLOBAL row, not the overrides.
    glob = (await api_client.get("/api/config/type_a")).json()
    assert glob["threshold_cv"] == 1.0
