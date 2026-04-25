"""
End-to-end tests for /api/sessions (gap 5).

Covers:
  * list / get / current / start / stop / logs
  * the partial-unique-index invariants (one active global, one active
    local per device)
  * the sessions_scope_shape CHECK constraint surfaced as 422
  * audit-log writes on start + stop
  * the sessions_lock_package trigger flips is_locked on first close
  * the end_local_children trigger cascades stop to LOCAL children
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from hermes.db.engine import async_session
from hermes.db.models import Device, Package, SessionLog


async def _seed_package() -> uuid.UUID:
    """Create a fresh non-default package and return its id."""
    async with async_session() as s:
        pkg = Package(name="testpkg", is_default=False)
        s.add(pkg)
        await s.commit()
        await s.refresh(pkg)
        return uuid.UUID(str(pkg.package_id))


async def _seed_device(device_id: int = 7) -> int:
    async with async_session() as s:
        s.add(Device(device_id=device_id, name=f"dev-{device_id}"))
        await s.commit()
        return device_id


# ─── List & current ──────────────────────────────────────────────


@pytest.mark.db
@pytest.mark.asyncio
async def test_current_returns_global_bootstrap_session(api_client: AsyncClient) -> None:
    """The conftest's api_client fixture bootstraps an active global session."""
    resp = await api_client.get("/api/sessions/current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["global_session"] is not None
    assert body["global_session"]["scope"] == "global"
    assert body["global_session"]["ended_at"] is None
    assert body["local_sessions"] == []


@pytest.mark.db
@pytest.mark.asyncio
async def test_list_returns_active_filter(api_client: AsyncClient) -> None:
    rows = (await api_client.get("/api/sessions?active=true")).json()
    assert all(r["ended_at"] is None for r in rows)
    assert any(r["scope"] == "global" for r in rows)


# ─── Start: GLOBAL ──────────────────────────────────────────────


@pytest.mark.db
@pytest.mark.asyncio
async def test_start_global_returns_409_when_one_active(api_client: AsyncClient) -> None:
    """The bootstrap already created an active global; another POST must fail."""
    pkg_id = await _seed_package()
    resp = await api_client.post(
        "/api/sessions",
        json={"scope": "global", "package_id": str(pkg_id)},
    )
    assert resp.status_code == 409


@pytest.mark.db
@pytest.mark.asyncio
async def test_start_global_succeeds_after_stopping_active(api_client: AsyncClient) -> None:
    # Find the active global that the conftest started.
    current = (await api_client.get("/api/sessions/current")).json()
    glb_id = current["global_session"]["session_id"]
    # Stop it first.
    stop = await api_client.post(f"/api/sessions/{glb_id}/stop", json={})
    assert stop.status_code == 200
    assert stop.json()["ended_at"] is not None

    pkg_id = await _seed_package()
    new_resp = await api_client.post(
        "/api/sessions",
        json={"scope": "global", "package_id": str(pkg_id), "notes": "fresh start"},
    )
    assert new_resp.status_code == 201
    body = new_resp.json()
    assert body["scope"] == "global"
    assert body["ended_at"] is None
    assert body["notes"] == "fresh start"


# ─── Start: LOCAL ───────────────────────────────────────────────


@pytest.mark.db
@pytest.mark.asyncio
async def test_start_local_with_no_global_returns_422(api_client: AsyncClient) -> None:
    """LOCAL needs a parent global; if we stop the global first, POST fails."""
    current = (await api_client.get("/api/sessions/current")).json()
    glb_id = current["global_session"]["session_id"]
    await api_client.post(f"/api/sessions/{glb_id}/stop", json={})

    pkg_id = await _seed_package()
    device_id = await _seed_device(7)
    resp = await api_client.post(
        "/api/sessions",
        json={"scope": "local", "package_id": str(pkg_id), "device_id": device_id},
    )
    assert resp.status_code == 422
    assert "without an active global" in resp.json()["detail"]


@pytest.mark.db
@pytest.mark.asyncio
async def test_start_local_succeeds_with_active_global(api_client: AsyncClient) -> None:
    pkg_id = await _seed_package()
    device_id = await _seed_device(7)
    resp = await api_client.post(
        "/api/sessions",
        json={"scope": "local", "package_id": str(pkg_id), "device_id": device_id},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["scope"] == "local"
    assert body["device_id"] == device_id
    assert body["parent_session_id"] is not None


@pytest.mark.db
@pytest.mark.asyncio
async def test_start_two_locals_for_same_device_returns_409(api_client: AsyncClient) -> None:
    pkg_id = await _seed_package()
    device_id = await _seed_device(7)
    first = await api_client.post(
        "/api/sessions",
        json={"scope": "local", "package_id": str(pkg_id), "device_id": device_id},
    )
    assert first.status_code == 201
    second = await api_client.post(
        "/api/sessions",
        json={"scope": "local", "package_id": str(pkg_id), "device_id": device_id},
    )
    assert second.status_code == 409


@pytest.mark.db
@pytest.mark.asyncio
async def test_start_local_for_two_devices_both_succeed(api_client: AsyncClient) -> None:
    pkg_id = await _seed_package()
    await _seed_device(7)
    await _seed_device(8)
    a = await api_client.post(
        "/api/sessions",
        json={"scope": "local", "package_id": str(pkg_id), "device_id": 7},
    )
    b = await api_client.post(
        "/api/sessions",
        json={"scope": "local", "package_id": str(pkg_id), "device_id": 8},
    )
    assert a.status_code == 201
    assert b.status_code == 201


# ─── Validation: scope shape ─────────────────────────────────────


@pytest.mark.db
@pytest.mark.asyncio
async def test_global_with_device_id_rejected(api_client: AsyncClient) -> None:
    pkg_id = await _seed_package()
    resp = await api_client.post(
        "/api/sessions",
        json={"scope": "global", "package_id": str(pkg_id), "device_id": 1},
    )
    assert resp.status_code == 422


@pytest.mark.db
@pytest.mark.asyncio
async def test_local_without_device_id_rejected(api_client: AsyncClient) -> None:
    pkg_id = await _seed_package()
    resp = await api_client.post(
        "/api/sessions",
        json={"scope": "local", "package_id": str(pkg_id)},
    )
    assert resp.status_code == 422


@pytest.mark.db
@pytest.mark.asyncio
async def test_unknown_package_id_returns_422(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/sessions",
        json={"scope": "global", "package_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 422


# ─── Stop ─────────────────────────────────────────────────────────


@pytest.mark.db
@pytest.mark.asyncio
async def test_stop_idempotent_on_already_closed(api_client: AsyncClient) -> None:
    current = (await api_client.get("/api/sessions/current")).json()
    glb_id = current["global_session"]["session_id"]
    a = await api_client.post(f"/api/sessions/{glb_id}/stop", json={})
    assert a.status_code == 200
    first_ended = a.json()["ended_at"]
    b = await api_client.post(f"/api/sessions/{glb_id}/stop", json={"ended_reason": "ignored"})
    assert b.status_code == 200
    # Idempotent: ended_at unchanged, no overwrite.
    assert b.json()["ended_at"] == first_ended


@pytest.mark.db
@pytest.mark.asyncio
async def test_stop_unknown_session_returns_404(api_client: AsyncClient) -> None:
    resp = await api_client.post(f"/api/sessions/{uuid.uuid4()}/stop", json={})
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_stopping_global_cascades_to_local_children(api_client: AsyncClient) -> None:
    """The end_local_children trigger must close LOCAL children when the global stops."""
    pkg_id = await _seed_package()
    await _seed_device(9)
    local = await api_client.post(
        "/api/sessions",
        json={"scope": "local", "package_id": str(pkg_id), "device_id": 9},
    )
    assert local.status_code == 201
    local_id = local.json()["session_id"]

    current = (await api_client.get("/api/sessions/current")).json()
    glb_id = current["global_session"]["session_id"]
    await api_client.post(f"/api/sessions/{glb_id}/stop", json={})

    after = (await api_client.get(f"/api/sessions/{local_id}")).json()
    assert after["ended_at"] is not None  # cascaded stop


# ─── Package locking via trigger ─────────────────────────────────


@pytest.mark.db
@pytest.mark.asyncio
async def test_closing_a_session_locks_its_package(api_client: AsyncClient) -> None:
    """The sessions_lock_package trigger flips is_locked on the package."""
    pkg_id = await _seed_package()

    # Stop the bootstrap global so we can start a fresh one against the new package.
    current = (await api_client.get("/api/sessions/current")).json()
    await api_client.post(f"/api/sessions/{current['global_session']['session_id']}/stop", json={})

    started = await api_client.post(
        "/api/sessions",
        json={"scope": "global", "package_id": str(pkg_id)},
    )
    new_id = started.json()["session_id"]

    # Pre-stop: package is unlocked.
    pkg_pre = (await api_client.get(f"/api/packages/{pkg_id}")).json()
    assert pkg_pre["is_locked"] is False

    # Stop the session.
    await api_client.post(f"/api/sessions/{new_id}/stop", json={})

    # Post-stop: trigger locked the package.
    pkg_post = (await api_client.get(f"/api/packages/{pkg_id}")).json()
    assert pkg_post["is_locked"] is True


# ─── Audit logs ──────────────────────────────────────────────────


@pytest.mark.db
@pytest.mark.asyncio
async def test_start_writes_session_log_row(api_client: AsyncClient) -> None:
    current = (await api_client.get("/api/sessions/current")).json()
    glb_id = current["global_session"]["session_id"]
    logs = (await api_client.get(f"/api/sessions/{glb_id}/logs")).json()
    # The bootstrap path doesn't write a log row (it goes through
    # ensure_default_session, not the API). So the first log row should
    # appear after the first API-driven start. Trigger it now.
    await api_client.post(f"/api/sessions/{glb_id}/stop", json={})
    pkg_id = await _seed_package()
    started = await api_client.post(
        "/api/sessions",
        json={"scope": "global", "package_id": str(pkg_id)},
    )
    new_id = started.json()["session_id"]

    logs = (await api_client.get(f"/api/sessions/{new_id}/logs")).json()
    assert len(logs) >= 1
    assert logs[0]["event"] == "start"
    assert logs[0]["actor"] == "api"
    assert logs[0]["details"]["package_id"] == str(pkg_id)


@pytest.mark.db
@pytest.mark.asyncio
async def test_stop_writes_session_log_row_with_reason(api_client: AsyncClient) -> None:
    current = (await api_client.get("/api/sessions/current")).json()
    glb_id = current["global_session"]["session_id"]
    await api_client.post(
        f"/api/sessions/{glb_id}/stop",
        json={"ended_reason": "shift change"},
    )
    logs = (await api_client.get(f"/api/sessions/{glb_id}/logs?order=desc")).json()
    # The most recent log should be the stop event.
    assert logs[0]["event"] == "stop"
    assert logs[0]["details"]["reason"] == "shift change"


@pytest.mark.db
@pytest.mark.asyncio
async def test_logs_404_for_unknown_session(api_client: AsyncClient) -> None:
    resp = await api_client.get(f"/api/sessions/{uuid.uuid4()}/logs")
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_logs_order_asc_default_then_desc(api_client: AsyncClient) -> None:
    """Default is ascending; ?order=desc reverses."""
    current = (await api_client.get("/api/sessions/current")).json()
    glb_id = current["global_session"]["session_id"]
    await api_client.post(f"/api/sessions/{glb_id}/stop", json={"ended_reason": "x"})

    pkg_id = await _seed_package()
    started = await api_client.post(
        "/api/sessions",
        json={"scope": "global", "package_id": str(pkg_id)},
    )
    new_id = started.json()["session_id"]
    await api_client.post(f"/api/sessions/{new_id}/stop", json={"ended_reason": "y"})

    asc = (await api_client.get(f"/api/sessions/{new_id}/logs?order=asc")).json()
    desc = (await api_client.get(f"/api/sessions/{new_id}/logs?order=desc")).json()
    # We've written exactly two rows: start then stop.
    assert [r["event"] for r in asc] == ["start", "stop"]
    assert [r["event"] for r in desc] == ["stop", "start"]


# ─── Get / 404 ───────────────────────────────────────────────────


@pytest.mark.db
@pytest.mark.asyncio
async def test_get_returns_404_for_unknown(api_client: AsyncClient) -> None:
    resp = await api_client.get(f"/api/sessions/{uuid.uuid4()}")
    assert resp.status_code == 404


# ─── Sanity on filters ───────────────────────────────────────────


@pytest.mark.db
@pytest.mark.asyncio
async def test_list_with_scope_filter(api_client: AsyncClient) -> None:
    rows = (await api_client.get("/api/sessions?scope=global")).json()
    assert all(r["scope"] == "global" for r in rows)


@pytest.mark.db
@pytest.mark.asyncio
async def test_list_with_device_id_filter(api_client: AsyncClient) -> None:
    pkg_id = await _seed_package()
    await _seed_device(11)
    await api_client.post(
        "/api/sessions",
        json={"scope": "local", "package_id": str(pkg_id), "device_id": 11},
    )
    rows = (await api_client.get("/api/sessions?device_id=11")).json()
    assert all(r["device_id"] == 11 for r in rows)
    assert len(rows) >= 1


@pytest.mark.db
@pytest.mark.asyncio
async def test_session_log_table_truthy_after_writes(api_client: AsyncClient) -> None:
    """Sanity: the session_logs table actually has rows after a full lifecycle."""
    current = (await api_client.get("/api/sessions/current")).json()
    glb_id = current["global_session"]["session_id"]
    await api_client.post(f"/api/sessions/{glb_id}/stop", json={})
    pkg_id = await _seed_package()
    started = await api_client.post(
        "/api/sessions",
        json={"scope": "global", "package_id": str(pkg_id)},
    )
    new_id = started.json()["session_id"]
    await api_client.post(f"/api/sessions/{new_id}/stop", json={})

    async with async_session() as s:
        rows = (
            (await s.execute(select(SessionLog).where(SessionLog.session_id == uuid.UUID(new_id))))
            .scalars()
            .all()
        )
        events = sorted(r.event.value for r in rows)
        assert events == ["start", "stop"]
