"""
End-to-end tests for /api/mqtt-brokers (gap 4).

Covers the CRUD surface plus the partial-unique-index invariant
(at most one ``is_active=TRUE`` row at a time). Each test starts from
a truncated DB courtesy of the standard ``api_client`` fixture.

Password handling is verified at the boundary:
    * The ``has_password`` boolean flag flips with the stored value.
    * The plaintext is NEVER returned in any response shape.
    * Empty-string PATCH clears the stored password.
    * The encrypted value in the DB round-trips through ``secret_box``.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from hermes.auth.secret_box import decrypt
from hermes.db.engine import async_session
from hermes.db.models import MqttBroker


def _payload(**overrides: object) -> dict[str, object]:
    """Build a POST body with sensible defaults; overrides win per-call."""
    base: dict[str, object] = {
        "host": "broker.example.com",
        "port": 1883,
        "username": "iot",
        "password": "hunter2",
        "use_tls": False,
        "is_active": True,
    }
    base.update(overrides)
    return base


@pytest.mark.db
@pytest.mark.asyncio
async def test_list_empty_when_no_brokers(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/mqtt-brokers")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.db
@pytest.mark.asyncio
async def test_create_returns_201_with_has_password_true(api_client: AsyncClient) -> None:
    resp = await api_client.post("/api/mqtt-brokers", json=_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["host"] == "broker.example.com"
    assert body["port"] == 1883
    assert body["username"] == "iot"
    assert body["has_password"] is True
    assert body["use_tls"] is False
    assert body["is_active"] is True
    # The plaintext password must NEVER be in the response.
    assert "password" not in body
    assert "password_enc" not in body


@pytest.mark.db
@pytest.mark.asyncio
async def test_create_without_password_sets_has_password_false(api_client: AsyncClient) -> None:
    resp = await api_client.post("/api/mqtt-brokers", json=_payload(password=None))
    assert resp.status_code == 201
    assert resp.json()["has_password"] is False


@pytest.mark.db
@pytest.mark.asyncio
async def test_create_empty_string_password_treated_as_no_password(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.post("/api/mqtt-brokers", json=_payload(password=""))
    assert resp.status_code == 201
    assert resp.json()["has_password"] is False


@pytest.mark.db
@pytest.mark.asyncio
async def test_stored_password_round_trips_via_secret_box(api_client: AsyncClient) -> None:
    """The DB row must hold a Fernet token that decrypts to the original."""
    resp = await api_client.post("/api/mqtt-brokers", json=_payload(password="my$ecret"))
    assert resp.status_code == 201
    broker_id = resp.json()["broker_id"]
    async with async_session() as session:
        broker = await session.get(MqttBroker, broker_id)
        assert broker is not None
        assert broker.password_enc is not None
        assert broker.password_enc != "my$ecret"  # not plaintext
        assert decrypt(broker.password_enc) == "my$ecret"


@pytest.mark.db
@pytest.mark.asyncio
async def test_create_two_active_brokers_keeps_only_latest_active(
    api_client: AsyncClient,
) -> None:
    """Activating a new broker must atomically deactivate the old one."""
    a = (await api_client.post("/api/mqtt-brokers", json=_payload(host="a"))).json()
    b = (await api_client.post("/api/mqtt-brokers", json=_payload(host="b"))).json()

    listing = (await api_client.get("/api/mqtt-brokers")).json()
    by_id = {row["broker_id"]: row for row in listing}
    assert by_id[a["broker_id"]]["is_active"] is False
    assert by_id[b["broker_id"]]["is_active"] is True


@pytest.mark.db
@pytest.mark.asyncio
async def test_get_404_for_unknown_broker(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/mqtt-brokers/9999")
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_patch_partial_update_leaves_other_fields_untouched(
    api_client: AsyncClient,
) -> None:
    created = (
        await api_client.post(
            "/api/mqtt-brokers",
            json=_payload(host="original", port=1883, username="u1"),
        )
    ).json()
    broker_id = created["broker_id"]

    resp = await api_client.patch(
        f"/api/mqtt-brokers/{broker_id}", json={"port": 8883, "use_tls": True}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["port"] == 8883
    assert body["use_tls"] is True
    # Untouched fields should retain prior values.
    assert body["host"] == "original"
    assert body["username"] == "u1"
    assert body["has_password"] is True


@pytest.mark.db
@pytest.mark.asyncio
async def test_patch_omitting_password_leaves_password_unchanged(
    api_client: AsyncClient,
) -> None:
    created = (await api_client.post("/api/mqtt-brokers", json=_payload(password="old"))).json()
    bid = created["broker_id"]
    await api_client.patch(f"/api/mqtt-brokers/{bid}", json={"port": 8884})
    # Fetch and confirm has_password still True; underlying row still
    # decrypts to "old".
    listed = (await api_client.get(f"/api/mqtt-brokers/{bid}")).json()
    assert listed["has_password"] is True
    async with async_session() as session:
        broker = await session.get(MqttBroker, bid)
        assert broker is not None
        assert broker.password_enc is not None
        assert decrypt(broker.password_enc) == "old"


@pytest.mark.db
@pytest.mark.asyncio
async def test_patch_empty_password_clears_stored_password(
    api_client: AsyncClient,
) -> None:
    created = (await api_client.post("/api/mqtt-brokers", json=_payload(password="x"))).json()
    bid = created["broker_id"]
    resp = await api_client.patch(f"/api/mqtt-brokers/{bid}", json={"password": ""})
    assert resp.status_code == 200
    assert resp.json()["has_password"] is False
    async with async_session() as session:
        broker = await session.get(MqttBroker, bid)
        assert broker is not None
        assert broker.password_enc is None


@pytest.mark.db
@pytest.mark.asyncio
async def test_delete_204_then_404(api_client: AsyncClient) -> None:
    created = (await api_client.post("/api/mqtt-brokers", json=_payload())).json()
    bid = created["broker_id"]
    resp = await api_client.delete(f"/api/mqtt-brokers/{bid}")
    assert resp.status_code == 204
    resp = await api_client.delete(f"/api/mqtt-brokers/{bid}")
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.asyncio
async def test_activate_endpoint_atomically_swaps_active_row(
    api_client: AsyncClient,
) -> None:
    a = (await api_client.post("/api/mqtt-brokers", json=_payload(host="a"))).json()
    b = (await api_client.post("/api/mqtt-brokers", json=_payload(host="b"))).json()
    # b is active after create. Activating a should make a active and b inactive.
    resp = await api_client.post(f"/api/mqtt-brokers/{a['broker_id']}/activate")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True

    listing = {row["broker_id"]: row for row in (await api_client.get("/api/mqtt-brokers")).json()}
    assert listing[a["broker_id"]]["is_active"] is True
    assert listing[b["broker_id"]]["is_active"] is False


@pytest.mark.db
@pytest.mark.asyncio
async def test_activate_already_active_is_idempotent(api_client: AsyncClient) -> None:
    a = (await api_client.post("/api/mqtt-brokers", json=_payload())).json()
    resp = await api_client.post(f"/api/mqtt-brokers/{a['broker_id']}/activate")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


@pytest.mark.db
@pytest.mark.asyncio
async def test_zero_active_allowed_after_deactivating_only_row(
    api_client: AsyncClient,
) -> None:
    """The partial unique index permits zero active rows — falls back to env settings."""
    a = (await api_client.post("/api/mqtt-brokers", json=_payload())).json()
    resp = await api_client.patch(f"/api/mqtt-brokers/{a['broker_id']}", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # And a list shows it inactive.
    rows = (await api_client.get("/api/mqtt-brokers")).json()
    assert all(row["is_active"] is False for row in rows)


@pytest.mark.db
@pytest.mark.asyncio
async def test_list_orders_by_broker_id_ascending(api_client: AsyncClient) -> None:
    for i in range(3):
        await api_client.post("/api/mqtt-brokers", json=_payload(host=f"h{i}"))
    rows = (await api_client.get("/api/mqtt-brokers")).json()
    ids = [row["broker_id"] for row in rows]
    assert ids == sorted(ids)


@pytest.mark.db
@pytest.mark.asyncio
async def test_no_active_row_in_db_when_none_active(api_client: AsyncClient) -> None:
    """Sanity: after PATCH is_active=False, the DB has no active row."""
    a = (await api_client.post("/api/mqtt-brokers", json=_payload())).json()
    await api_client.patch(f"/api/mqtt-brokers/{a['broker_id']}", json={"is_active": False})
    async with async_session() as session:
        actives = await session.execute(select(MqttBroker).where(MqttBroker.is_active.is_(True)))
        assert actives.scalars().all() == []
