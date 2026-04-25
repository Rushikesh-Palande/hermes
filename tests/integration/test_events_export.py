"""
End-to-end tests for /api/events/export.

Verifies CSV and NDJSON output: header / line counts, filter pass-
through (so the exporter shares the same predicate as the list
endpoint), and that the route is reachable without colliding with the
parametric ``/{event_id}`` path.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from hermes.db.engine import async_session
from hermes.db.models import Device, Event, EventType
from hermes.detection.session import ensure_default_session


async def _seed_device(device_id: int = 1) -> None:
    async with async_session() as session:
        session.add(Device(device_id=device_id, name=f"dev-{device_id}"))


async def _seed_event(
    *,
    session_id: object,
    triggered_at: datetime,
    device_id: int = 1,
    sensor_id: int = 1,
    event_type: EventType = EventType.A,
    triggered_value: float = 12.34,
    metadata: dict | None = None,
) -> int:
    async with async_session() as session:
        ev = Event(
            session_id=session_id,
            triggered_at=triggered_at,
            fired_at=triggered_at,
            device_id=device_id,
            sensor_id=sensor_id,
            event_type=event_type,
            triggered_value=triggered_value,
            metadata_=metadata or {"source": "test"},
        )
        session.add(ev)
        await session.flush()
        return ev.event_id


@pytest.mark.db
@pytest.mark.asyncio
async def test_csv_export_emits_header_only_when_empty(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/events/export?format=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    text = resp.text
    # Just the header line.
    assert text.strip().split(",")[0] == "event_id"
    # No data rows.
    assert text.count("\n") == 1


@pytest.mark.db
@pytest.mark.asyncio
async def test_csv_export_round_trip(api_client: AsyncClient) -> None:
    session_id, _ = await ensure_default_session()
    await _seed_device(1)
    base = datetime.now(tz=UTC)
    for offset_min in (5, 10, 15):
        await _seed_event(
            session_id=session_id,
            triggered_at=base - timedelta(minutes=offset_min),
            triggered_value=float(offset_min),
            metadata={"k": offset_min},
        )

    resp = await api_client.get("/api/events/export?format=csv")
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 3
    # Newest first by triggered_at — `5` minutes ago is the most recent.
    assert [float(r["triggered_value"]) for r in rows] == [5.0, 10.0, 15.0]
    # metadata round-trips as a JSON-encoded string.
    assert json.loads(rows[0]["metadata"]) == {"k": 5}


@pytest.mark.db
@pytest.mark.asyncio
async def test_ndjson_export_one_record_per_line(api_client: AsyncClient) -> None:
    session_id, _ = await ensure_default_session()
    await _seed_device(1)
    base = datetime.now(tz=UTC)
    for i in range(2):
        await _seed_event(
            session_id=session_id,
            triggered_at=base - timedelta(seconds=i),
            triggered_value=float(i),
        )

    resp = await api_client.get("/api/events/export?format=ndjson")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in resp.text.splitlines() if line]
    assert len(lines) == 2
    assert {row["triggered_value"] for row in lines} == {0.0, 1.0}


@pytest.mark.db
@pytest.mark.asyncio
async def test_export_filters_match_list_endpoint(api_client: AsyncClient) -> None:
    session_id, _ = await ensure_default_session()
    await _seed_device(1)
    await _seed_device(2)
    base = datetime.now(tz=UTC)
    await _seed_event(session_id=session_id, triggered_at=base, device_id=1, sensor_id=3)
    await _seed_event(session_id=session_id, triggered_at=base, device_id=2, sensor_id=3)
    await _seed_event(session_id=session_id, triggered_at=base, device_id=1, sensor_id=7)

    resp = await api_client.get("/api/events/export?format=csv&device_id=1&sensor_id=3")
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) == 1
    assert rows[0]["device_id"] == "1"
    assert rows[0]["sensor_id"] == "3"


@pytest.mark.db
@pytest.mark.asyncio
async def test_export_default_format_is_csv(api_client: AsyncClient) -> None:
    """Omitting ``format=`` must work and yield CSV, not 422."""
    resp = await api_client.get("/api/events/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")


@pytest.mark.db
@pytest.mark.asyncio
async def test_export_unknown_format_is_422(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/events/export?format=xml")
    assert resp.status_code == 422


@pytest.mark.db
@pytest.mark.asyncio
async def test_export_route_does_not_collide_with_event_id_route(
    api_client: AsyncClient,
) -> None:
    """``/api/events/export`` must not bind to the ``/{event_id}`` route."""
    resp = await api_client.get("/api/events/export?format=csv")
    # If the parametric route had matched, FastAPI would have 422'd
    # because "export" isn't an int.
    assert resp.status_code == 200
