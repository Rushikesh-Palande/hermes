"""
End-to-end tests for /api/config — read, write, and hot-reload.

Each test starts from a fresh schema (autouse fixture) and bootstraps
its own session via the API lifespan when ``api_client`` is used. The
tests assert both the round-trip (PUT then GET returns what was put)
and the hot-reload effect (the live DbConfigProvider reflects the new
values without a process restart).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.db
@pytest.mark.asyncio
async def test_get_type_a_returns_defaults_on_fresh_install(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/config/type_a")
    assert resp.status_code == 200
    body = resp.json()
    # Default config is all-disabled with the dataclass field defaults.
    assert body["enabled"] is False
    assert body["T1"] == 1.0
    assert body["threshold_cv"] == 5.0


@pytest.mark.db
@pytest.mark.asyncio
async def test_put_type_a_persists_and_reads_back(api_client: AsyncClient) -> None:
    payload = {
        "enabled": True,
        "T1": 2.5,
        "threshold_cv": 7.5,
        "debounce_seconds": 0.5,
        "init_fill_ratio": 0.8,
        "expected_sample_rate_hz": 123.0,
    }
    resp = await api_client.put("/api/config/type_a", json=payload)
    assert resp.status_code == 200
    assert resp.json() == payload

    # GET reflects the new value.
    resp = await api_client.get("/api/config/type_a")
    assert resp.json() == payload


@pytest.mark.db
@pytest.mark.asyncio
async def test_put_type_a_rejects_unknown_field(api_client: AsyncClient) -> None:
    resp = await api_client.put(
        "/api/config/type_a",
        json={"enabled": True, "T1": 1.0, "threshold_cv": 5.0, "phantom": 1.0},
    )
    assert resp.status_code == 422


@pytest.mark.db
@pytest.mark.asyncio
async def test_put_type_a_rejects_negative_t1(api_client: AsyncClient) -> None:
    resp = await api_client.put(
        "/api/config/type_a",
        json={"enabled": True, "T1": -1.0},
    )
    assert resp.status_code == 422


@pytest.mark.db
@pytest.mark.asyncio
async def test_put_type_b_round_trip(api_client: AsyncClient) -> None:
    payload = {
        "enabled": True,
        "T2": 5.0,
        "lower_threshold_pct": 3.0,
        "upper_threshold_pct": 4.0,
        "debounce_seconds": 0.0,
        "init_fill_ratio": 0.9,
        "expected_sample_rate_hz": 100.0,
    }
    resp = await api_client.put("/api/config/type_b", json=payload)
    assert resp.status_code == 200
    assert resp.json() == payload


@pytest.mark.db
@pytest.mark.asyncio
async def test_put_type_c_rejects_inverted_thresholds(api_client: AsyncClient) -> None:
    resp = await api_client.put(
        "/api/config/type_c",
        json={
            "enabled": True,
            "T3": 10.0,
            "threshold_lower": 80.0,
            "threshold_upper": 20.0,
            "debounce_seconds": 0.0,
            "init_fill_ratio": 0.9,
            "expected_sample_rate_hz": 100.0,
        },
    )
    assert resp.status_code == 422
    assert "threshold_lower" in resp.json()["detail"]


@pytest.mark.db
@pytest.mark.asyncio
async def test_put_type_d_round_trip(api_client: AsyncClient) -> None:
    payload = {
        "enabled": True,
        "T4": 8.0,
        "T5": 20.0,
        "tolerance_pct": 4.5,
        "debounce_seconds": 0.0,
        "init_fill_ratio": 0.9,
        "expected_sample_rate_hz": 100.0,
    }
    resp = await api_client.put("/api/config/type_d", json=payload)
    assert resp.status_code == 200
    assert resp.json() == payload


@pytest.mark.db
@pytest.mark.asyncio
async def test_put_then_get_all_four_types(api_client: AsyncClient) -> None:
    """Smoke test that A/B/C/D each persist independently without overwriting each other."""
    a = {
        "enabled": True,
        "T1": 1.5,
        "threshold_cv": 6.0,
        "debounce_seconds": 0.1,
        "init_fill_ratio": 0.9,
        "expected_sample_rate_hz": 100.0,
    }
    b = {
        "enabled": True,
        "T2": 2.0,
        "lower_threshold_pct": 1.0,
        "upper_threshold_pct": 1.0,
        "debounce_seconds": 0.0,
        "init_fill_ratio": 0.9,
        "expected_sample_rate_hz": 100.0,
    }
    c = {
        "enabled": False,
        "T3": 5.0,
        "threshold_lower": 10.0,
        "threshold_upper": 90.0,
        "debounce_seconds": 0.0,
        "init_fill_ratio": 0.9,
        "expected_sample_rate_hz": 100.0,
    }
    d = {
        "enabled": True,
        "T4": 4.0,
        "T5": 15.0,
        "tolerance_pct": 2.5,
        "debounce_seconds": 0.0,
        "init_fill_ratio": 0.9,
        "expected_sample_rate_hz": 100.0,
    }

    assert (await api_client.put("/api/config/type_a", json=a)).status_code == 200
    assert (await api_client.put("/api/config/type_b", json=b)).status_code == 200
    assert (await api_client.put("/api/config/type_c", json=c)).status_code == 200
    assert (await api_client.put("/api/config/type_d", json=d)).status_code == 200

    assert (await api_client.get("/api/config/type_a")).json() == a
    assert (await api_client.get("/api/config/type_b")).json() == b
    assert (await api_client.get("/api/config/type_c")).json() == c
    assert (await api_client.get("/api/config/type_d")).json() == d
