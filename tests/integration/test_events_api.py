"""
End-to-end tests for /api/events.

Each test seeds events directly (bypassing the detection engine) so the
focus stays on the API layer: filters, ordering, pagination, 404s, and
window decoding.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from hermes.db.engine import async_session
from hermes.db.models import Device, Event, EventType, EventWindow
from hermes.detection.encoding import ENCODING_JSON
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
    with_window: bool = True,
    window_samples: list[tuple[float, float]] | None = None,
) -> int:
    """Insert one Event (and optionally an EventWindow). Returns event_id."""
    async with async_session() as session:
        # Pin fired_at == triggered_at so the events_fire_vs_trigger CHECK
        # constraint (fired_at >= triggered_at - 1 minute) holds even when
        # the test seeds a triggered_at well outside "now" (e.g. 2 hours
        # in the future for the time-range filter test).
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

        if with_window:
            samples = window_samples or [(triggered_at.timestamp(), triggered_value)]
            payload = json.dumps(
                [{"ts": ts, "v": v} for ts, v in samples], separators=(",", ":")
            ).encode("utf-8")
            win = EventWindow(
                event_id=ev.event_id,
                start_ts=triggered_at - timedelta(seconds=9),
                end_ts=triggered_at + timedelta(seconds=9),
                sample_rate_hz=123.0,
                sample_count=len(samples),
                encoding=ENCODING_JSON,
                data=payload,
            )
            session.add(win)
            await session.flush()
            ev.window_id = win.window_id
            await session.flush()
        return ev.event_id


@pytest.mark.db
@pytest.mark.asyncio
async def test_empty_list_returns_empty_array(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/events")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.db
@pytest.mark.asyncio
async def test_list_orders_newest_first(api_client: AsyncClient) -> None:
    session_id, _ = await ensure_default_session()
    await _seed_device()
    base = datetime.now(tz=UTC)
    older = await _seed_event(session_id=session_id, triggered_at=base - timedelta(minutes=10))
    newer = await _seed_event(session_id=session_id, triggered_at=base)

    resp = await api_client.get("/api/events")
    assert resp.status_code == 200
    body = resp.json()
    assert [e["event_id"] for e in body] == [newer, older]


@pytest.mark.db
@pytest.mark.asyncio
async def test_filter_by_device_and_sensor(api_client: AsyncClient) -> None:
    session_id, _ = await ensure_default_session()
    await _seed_device(1)
    await _seed_device(2)
    base = datetime.now(tz=UTC)
    await _seed_event(session_id=session_id, triggered_at=base, device_id=1, sensor_id=3)
    await _seed_event(session_id=session_id, triggered_at=base, device_id=2, sensor_id=3)
    await _seed_event(session_id=session_id, triggered_at=base, device_id=1, sensor_id=7)

    resp = await api_client.get("/api/events?device_id=1&sensor_id=3")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["device_id"] == 1
    assert body[0]["sensor_id"] == 3


@pytest.mark.db
@pytest.mark.asyncio
async def test_filter_by_event_type(api_client: AsyncClient) -> None:
    session_id, _ = await ensure_default_session()
    await _seed_device()
    base = datetime.now(tz=UTC)
    for et in (EventType.A, EventType.B, EventType.C, EventType.D):
        await _seed_event(session_id=session_id, triggered_at=base, event_type=et)

    resp = await api_client.get("/api/events?event_type=B")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["event_type"] == "B"


@pytest.mark.db
@pytest.mark.asyncio
async def test_filter_by_time_range(api_client: AsyncClient) -> None:
    session_id, _ = await ensure_default_session()
    await _seed_device()
    base = datetime.now(tz=UTC)
    await _seed_event(session_id=session_id, triggered_at=base - timedelta(hours=2))
    in_range = await _seed_event(session_id=session_id, triggered_at=base - timedelta(minutes=30))
    await _seed_event(session_id=session_id, triggered_at=base + timedelta(hours=2))

    after = (base - timedelta(hours=1)).isoformat()
    before = (base + timedelta(hours=1)).isoformat()
    # Use ``params=`` so httpx URL-encodes the ``+`` in the offset
    # (otherwise FastAPI receives it as a literal space and 422s).
    resp = await api_client.get("/api/events", params={"after": after, "before": before})
    assert resp.status_code == 200
    body = resp.json()
    assert [e["event_id"] for e in body] == [in_range]


@pytest.mark.db
@pytest.mark.asyncio
async def test_pagination(api_client: AsyncClient) -> None:
    session_id, _ = await ensure_default_session()
    await _seed_device()
    base = datetime.now(tz=UTC)
    for i in range(5):
        await _seed_event(
            session_id=session_id,
            triggered_at=base - timedelta(minutes=i),
        )

    page1 = (await api_client.get("/api/events?limit=2&offset=0")).json()
    page2 = (await api_client.get("/api/events?limit=2&offset=2")).json()
    page3 = (await api_client.get("/api/events?limit=2&offset=4")).json()
    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1
    # No overlap between pages.
    ids = {e["event_id"] for e in page1 + page2 + page3}
    assert len(ids) == 5


@pytest.mark.db
@pytest.mark.asyncio
async def test_get_single_event(api_client: AsyncClient) -> None:
    session_id, _ = await ensure_default_session()
    await _seed_device()
    eid = await _seed_event(
        session_id=session_id,
        triggered_at=datetime.now(tz=UTC),
        triggered_value=42.5,
        metadata={"cv_percent": 17.3, "average": 50.0},
    )
    resp = await api_client.get(f"/api/events/{eid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == eid
    assert body["triggered_value"] == 42.5
    assert body["metadata"]["cv_percent"] == 17.3


@pytest.mark.db
@pytest.mark.asyncio
async def test_get_missing_event_returns_404(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/events/999999")
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_get_event_window_decodes_samples(api_client: AsyncClient) -> None:
    session_id, _ = await ensure_default_session()
    await _seed_device()
    samples = [(1.0, 10.0), (1.01, 10.5), (1.02, 9.8)]
    eid = await _seed_event(
        session_id=session_id,
        triggered_at=datetime.now(tz=UTC),
        window_samples=samples,
    )
    resp = await api_client.get(f"/api/events/{eid}/window")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sample_count"] == 3
    # Pydantic v2 serialises tuples as lists in JSON.
    assert body["samples"] == [list(s) for s in samples]


@pytest.mark.db
@pytest.mark.asyncio
async def test_get_window_for_event_without_window_returns_404(
    api_client: AsyncClient,
) -> None:
    session_id, _ = await ensure_default_session()
    await _seed_device()
    eid = await _seed_event(
        session_id=session_id,
        triggered_at=datetime.now(tz=UTC),
        with_window=False,
    )
    resp = await api_client.get(f"/api/events/{eid}/window")
    assert resp.status_code == 404
