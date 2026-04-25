"""
End-to-end tests for /api/devices/{device_id}/offsets.

Exercises the four endpoints: list (always 12 rows), single upsert,
single delete, and bulk replace. Each test seeds its own device via
the standard ``api_client`` fixture which already runs migrations
once per session and truncates between tests.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from hermes.db.engine import async_session
from hermes.db.models import Device


async def _seed_device(device_id: int = 1) -> None:
    async with async_session() as session:
        session.add(Device(device_id=device_id, name=f"dev-{device_id}"))


@pytest.mark.db
@pytest.mark.asyncio
async def test_list_returns_12_zeroed_entries_for_fresh_device(
    api_client: AsyncClient,
) -> None:
    await _seed_device(1)
    resp = await api_client.get("/api/devices/1/offsets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["device_id"] == 1
    assert len(body["offsets"]) == 12
    assert all(row["offset_value"] == 0.0 for row in body["offsets"])
    assert all(row["updated_at"] is None for row in body["offsets"])


@pytest.mark.db
@pytest.mark.asyncio
async def test_list_for_missing_device_returns_404(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/devices/42/offsets")
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_upsert_single_offset_round_trips(api_client: AsyncClient) -> None:
    await _seed_device(1)
    resp = await api_client.put(
        "/api/devices/1/offsets/3",
        json={"offset_value": 1.25},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sensor_id"] == 3
    assert body["offset_value"] == 1.25
    assert body["updated_at"] is not None

    listing = (await api_client.get("/api/devices/1/offsets")).json()
    sensor_3 = next(r for r in listing["offsets"] if r["sensor_id"] == 3)
    assert sensor_3["offset_value"] == 1.25
    others = [r for r in listing["offsets"] if r["sensor_id"] != 3]
    assert all(r["offset_value"] == 0.0 for r in others)


@pytest.mark.db
@pytest.mark.asyncio
async def test_upsert_overwrites_existing_offset(api_client: AsyncClient) -> None:
    await _seed_device(1)
    await api_client.put("/api/devices/1/offsets/5", json={"offset_value": 0.5})
    await api_client.put("/api/devices/1/offsets/5", json={"offset_value": -0.3})
    listing = (await api_client.get("/api/devices/1/offsets")).json()
    sensor_5 = next(r for r in listing["offsets"] if r["sensor_id"] == 5)
    assert sensor_5["offset_value"] == -0.3


@pytest.mark.db
@pytest.mark.asyncio
async def test_upsert_rejects_invalid_sensor_id(api_client: AsyncClient) -> None:
    await _seed_device(1)
    for sid in (0, 13, -1):
        resp = await api_client.put(f"/api/devices/1/offsets/{sid}", json={"offset_value": 0.1})
        assert resp.status_code == 422, f"sensor_id={sid} should be rejected"


@pytest.mark.db
@pytest.mark.asyncio
async def test_delete_returns_204_then_404(api_client: AsyncClient) -> None:
    await _seed_device(1)
    await api_client.put("/api/devices/1/offsets/2", json={"offset_value": 0.5})
    first = await api_client.delete("/api/devices/1/offsets/2")
    assert first.status_code == 204
    second = await api_client.delete("/api/devices/1/offsets/2")
    assert second.status_code == 404
    # And the GET reverts to 0.0 for that sensor.
    listing = (await api_client.get("/api/devices/1/offsets")).json()
    sensor_2 = next(r for r in listing["offsets"] if r["sensor_id"] == 2)
    assert sensor_2["offset_value"] == 0.0


@pytest.mark.db
@pytest.mark.asyncio
async def test_bulk_put_replaces_all_offsets(api_client: AsyncClient) -> None:
    await _seed_device(1)
    # Seed: sensors 1, 4, 9 have offsets.
    await api_client.put("/api/devices/1/offsets/1", json={"offset_value": 1.0})
    await api_client.put("/api/devices/1/offsets/4", json={"offset_value": 4.0})
    await api_client.put("/api/devices/1/offsets/9", json={"offset_value": 9.0})

    # Bulk put writes 2 + 7. 1, 4, 9 should be reset.
    resp = await api_client.put(
        "/api/devices/1/offsets",
        json={"offsets": {"2": 2.5, "7": 7.5}},
    )
    assert resp.status_code == 200
    body = resp.json()
    by_sid = {row["sensor_id"]: row["offset_value"] for row in body["offsets"]}
    assert by_sid[2] == 2.5
    assert by_sid[7] == 7.5
    for missing in (1, 3, 4, 5, 6, 8, 9, 10, 11, 12):
        assert by_sid[missing] == 0.0


@pytest.mark.db
@pytest.mark.asyncio
async def test_bulk_put_with_empty_body_clears_all(api_client: AsyncClient) -> None:
    await _seed_device(1)
    await api_client.put("/api/devices/1/offsets/3", json={"offset_value": 0.7})
    resp = await api_client.put("/api/devices/1/offsets", json={"offsets": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert all(row["offset_value"] == 0.0 for row in body["offsets"])


@pytest.mark.db
@pytest.mark.asyncio
async def test_bulk_put_rejects_out_of_range_sensor(api_client: AsyncClient) -> None:
    await _seed_device(1)
    resp = await api_client.put(
        "/api/devices/1/offsets",
        json={"offsets": {"0": 0.1}},
    )
    assert resp.status_code == 422


@pytest.mark.db
@pytest.mark.asyncio
async def test_bulk_put_for_missing_device_returns_404(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.put("/api/devices/999/offsets", json={"offsets": {"1": 1.0}})
    assert resp.status_code == 404
