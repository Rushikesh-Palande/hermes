"""
Liveness smoke test.

Readiness is NOT tested here because it requires a real DB; that lives
in tests/integration/ and is exercised by the CI job that spins up
Postgres via the docker-compose service container.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from hermes import __version__


@pytest.mark.asyncio
async def test_health_liveness(api_client: AsyncClient) -> None:
    """GET /api/health returns 200 with the current version string."""
    response = await api_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
