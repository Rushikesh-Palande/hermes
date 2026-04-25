"""
Integration tests for /api/packages (gap 5 dependency).

Covers list / get / create / clone behaviours plus the parameter-row
copy-on-clone invariant. Locking-on-session-end is exercised by the
sessions tests (where the trigger actually fires).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from hermes.db.engine import async_session
from hermes.db.models import Parameter, ParameterScope


@pytest.mark.db
@pytest.mark.asyncio
async def test_list_initially_returns_default_package(api_client: AsyncClient) -> None:
    """The conftest's api_client fixture bootstraps a default package."""
    resp = await api_client.get("/api/packages")
    assert resp.status_code == 200
    rows = resp.json()
    # At least the default package should be present.
    assert any(p["is_default"] for p in rows)


@pytest.mark.db
@pytest.mark.asyncio
async def test_create_returns_201_with_unlocked_package(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/packages", json={"name": "pkg-alpha", "description": "first"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "pkg-alpha"
    assert body["description"] == "first"
    assert body["is_default"] is False
    assert body["is_locked"] is False
    assert body["parent_package_id"] is None


@pytest.mark.db
@pytest.mark.asyncio
async def test_get_404_for_unknown_package(api_client: AsyncClient) -> None:
    import uuid

    resp = await api_client.get(f"/api/packages/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_clone_copies_all_parameter_rows(api_client: AsyncClient) -> None:
    """Cloning must duplicate every Parameter row under the new package_id."""
    # Create the source package and seed a couple of parameter rows.
    src = (await api_client.post("/api/packages", json={"name": "src"})).json()
    import uuid as _uuid

    src_id = _uuid.UUID(src["package_id"])
    async with async_session() as s:
        s.add(
            Parameter(
                package_id=src_id,
                key="type_a.config",
                value={"enabled": True, "T1": 2.0},
                scope=ParameterScope.GLOBAL,
            )
        )
        s.add(
            Parameter(
                package_id=src_id,
                key="type_b.config",
                value={"enabled": False},
                scope=ParameterScope.GLOBAL,
            )
        )
        await s.commit()

    resp = await api_client.post(
        f"/api/packages/{src_id}/clone", json={"name": "src-fork", "description": "clone"}
    )
    assert resp.status_code == 201
    clone = resp.json()
    assert clone["parent_package_id"] == str(src_id)
    assert clone["is_locked"] is False

    # The clone must carry the same parameter rows.
    async with async_session() as s:
        rows = (
            (
                await s.execute(
                    select(Parameter).where(Parameter.package_id == _uuid.UUID(clone["package_id"]))
                )
            )
            .scalars()
            .all()
        )
        keys = sorted(r.key for r in rows)
        assert keys == ["type_a.config", "type_b.config"]


@pytest.mark.db
@pytest.mark.asyncio
async def test_clone_does_not_modify_source(api_client: AsyncClient) -> None:
    src = (await api_client.post("/api/packages", json={"name": "immut"})).json()
    await api_client.post(f"/api/packages/{src['package_id']}/clone", json={"name": "immut-clone"})
    listed = (await api_client.get(f"/api/packages/{src['package_id']}")).json()
    assert listed["is_locked"] is False  # cloning doesn't lock the source
    assert listed["name"] == "immut"


@pytest.mark.db
@pytest.mark.asyncio
async def test_create_with_min_payload_works(api_client: AsyncClient) -> None:
    resp = await api_client.post("/api/packages", json={"name": "p"})
    assert resp.status_code == 201
    assert resp.json()["description"] is None


@pytest.mark.db
@pytest.mark.asyncio
async def test_create_rejects_empty_name(api_client: AsyncClient) -> None:
    resp = await api_client.post("/api/packages", json={"name": ""})
    assert resp.status_code == 422
