"""
End-to-end CRUD against /api/devices with a real Postgres.

Uses the ASGI transport so no real socket is opened; the API handlers
still hit the actual database via SQLAlchemy. Each test starts from a
clean `devices` table so ordering does not matter.

Dev-mode auth bypass is relied upon here — ``HERMES_DEV_MODE=1`` is set
in ``conftest.py`` defaults so ``CurrentUser`` resolves without a JWT.
In CI the same flag is set on the python job's env.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import delete

from hermes.db.engine import async_session
from hermes.db.models import Device


@pytest_asyncio.fixture
async def clean_devices() -> AsyncIterator[None]:
    """Truncate the devices table before and after each test."""
    async with async_session() as session:
        await session.execute(delete(Device))
        await session.commit()
    yield
    async with async_session() as session:
        await session.execute(delete(Device))
        await session.commit()


@pytest.mark.db
@pytest.mark.asyncio
async def test_empty_list_returns_empty_array(api_client: AsyncClient, clean_devices: None) -> None:
    resp = await api_client.get("/api/devices")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.db
@pytest.mark.asyncio
async def test_create_device_returns_201_and_persists(
    api_client: AsyncClient, clean_devices: None
) -> None:
    resp = await api_client.post(
        "/api/devices",
        json={"device_id": 1, "name": "STM32 MQTT"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["device_id"] == 1
    assert body["name"] == "STM32 MQTT"
    assert body["protocol"] == "mqtt"
    assert body["is_active"] is True
    assert "created_at" in body and "updated_at" in body

    # Verify GET round-trip.
    get_resp = await api_client.get("/api/devices/1")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "STM32 MQTT"


@pytest.mark.db
@pytest.mark.asyncio
async def test_duplicate_device_id_returns_409(
    api_client: AsyncClient, clean_devices: None
) -> None:
    await api_client.post("/api/devices", json={"device_id": 5, "name": "first"})
    resp = await api_client.post("/api/devices", json={"device_id": 5, "name": "second"})
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


@pytest.mark.db
@pytest.mark.asyncio
async def test_device_id_out_of_range_rejected(
    api_client: AsyncClient, clean_devices: None
) -> None:
    # device_id must be 1..999 (Field constraint + DB check constraint).
    for bad in (0, 1000, -1):
        resp = await api_client.post("/api/devices", json={"device_id": bad, "name": "x"})
        assert resp.status_code == 422, f"device_id={bad} should be rejected"


@pytest.mark.db
@pytest.mark.asyncio
async def test_get_missing_device_returns_404(api_client: AsyncClient, clean_devices: None) -> None:
    resp = await api_client.get("/api/devices/42")
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_patch_updates_only_provided_fields(
    api_client: AsyncClient, clean_devices: None
) -> None:
    await api_client.post(
        "/api/devices",
        json={"device_id": 1, "name": "original", "topic": "stm32/adc"},
    )

    # Patch only name; topic must stay.
    resp = await api_client.patch("/api/devices/1", json={"name": "renamed"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "renamed"
    assert body["topic"] == "stm32/adc"
    assert body["is_active"] is True

    # Soft-disable.
    resp = await api_client.patch("/api/devices/1", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.db
@pytest.mark.asyncio
async def test_patch_missing_device_returns_404(
    api_client: AsyncClient, clean_devices: None
) -> None:
    resp = await api_client.patch("/api/devices/999", json={"name": "x"})
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_delete_removes_device(api_client: AsyncClient, clean_devices: None) -> None:
    await api_client.post("/api/devices", json={"device_id": 3, "name": "temp"})

    resp = await api_client.delete("/api/devices/3")
    assert resp.status_code == 204

    # Gone.
    assert (await api_client.get("/api/devices/3")).status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_delete_missing_device_returns_404(
    api_client: AsyncClient, clean_devices: None
) -> None:
    resp = await api_client.delete("/api/devices/12345")
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_list_is_ordered_by_device_id(api_client: AsyncClient, clean_devices: None) -> None:
    for did in (7, 2, 5, 1):
        await api_client.post("/api/devices", json={"device_id": did, "name": f"d{did}"})
    resp = await api_client.get("/api/devices")
    assert resp.status_code == 200
    ids = [row["device_id"] for row in resp.json()]
    assert ids == [1, 2, 5, 7]
