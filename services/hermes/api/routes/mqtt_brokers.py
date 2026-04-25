"""
/api/mqtt-brokers — operator-managed MQTT broker registry (gap 4).

The ingest pipeline currently reads its broker host/port from the
``MQTT_*`` env vars at process start (see ``Settings`` in
``hermes.config``). For deployments where the operator wants to
re-point HERMES at a different broker without editing systemd
env files + restarting the service from a shell, this module
provides a UI-driven config registry stored in ``mqtt_brokers``.

Schema invariant (enforced by the partial unique index from
migration 0002): at most one row may have ``is_active=TRUE`` at a
time. The CRUD here preserves that — activating broker N atomically
deactivates whichever row was previously active.

What's in scope for alpha.18:
    * GET    /api/mqtt-brokers          — list
    * POST   /api/mqtt-brokers          — create
    * GET    /api/mqtt-brokers/{id}     — read
    * PATCH  /api/mqtt-brokers/{id}     — partial update
    * DELETE /api/mqtt-brokers/{id}     — delete
    * POST   /api/mqtt-brokers/{id}/activate — atomic activate

What's deliberately NOT in scope for alpha.18:
    * Live broker switchover. The ingest reads broker config at process
      start; flipping ``is_active`` doesn't reconnect the existing paho
      client. Operators must restart ``hermes-ingest`` after activating
      a different broker. A future release will close this gap with
      LISTEN/NOTIFY + paho.disconnect/connect, mirroring the alpha.15
      config-sync pattern.

Password handling:
    The plaintext password is accepted on POST/PATCH bodies and stored
    via ``secret_box.encrypt`` into ``password_enc``. It is NEVER
    returned in any response — the response shape carries a
    ``has_password: bool`` flag instead. The ingest process decrypts
    when constructing its paho client at startup.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hermes.api.deps import CurrentUser, DbSession
from hermes.auth.secret_box import encrypt
from hermes.db.models import MqttBroker

router = APIRouter()


# ─── Shapes ────────────────────────────────────────────────────────


class MqttBrokerIn(BaseModel):
    """POST /api/mqtt-brokers body."""

    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(default=1883, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=255)
    use_tls: bool = False
    is_active: bool = True


class MqttBrokerPatch(BaseModel):
    """PATCH /api/mqtt-brokers/{id} body. All fields optional.

    To clear the password explicitly, send ``"password": ""``. Sending
    ``null`` or omitting the field leaves the existing password
    unchanged — matches operator intuition where "blank means leave it
    alone" is the convention on most settings UIs.
    """

    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=255)
    use_tls: bool | None = None
    is_active: bool | None = None


class MqttBrokerOut(BaseModel):
    """Response shape. Never includes the plaintext password."""

    model_config = ConfigDict(from_attributes=True)

    broker_id: int
    host: str
    port: int
    username: str | None
    has_password: bool
    use_tls: bool
    is_active: bool
    created_at: datetime


def _to_out(broker: MqttBroker) -> MqttBrokerOut:
    """Project a row to the response shape (computes has_password)."""
    return MqttBrokerOut(
        broker_id=broker.broker_id,
        host=broker.host,
        port=broker.port,
        username=broker.username,
        has_password=broker.password_enc is not None,
        use_tls=broker.use_tls,
        is_active=broker.is_active,
        created_at=broker.created_at,
    )


# ─── Helpers ───────────────────────────────────────────────────────


async def _get_or_404(session: AsyncSession, broker_id: int) -> MqttBroker:
    broker = await session.get(MqttBroker, broker_id)
    if broker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"mqtt broker {broker_id} not found",
        )
    return broker


async def _deactivate_others(session: AsyncSession, except_id: int | None) -> None:
    """Set ``is_active=FALSE`` on every row except ``except_id``.

    Called BEFORE flipping a row to active so the partial unique index
    ``mqtt_brokers_one_active`` doesn't reject the change. Done in a
    single UPDATE so the DB enforces the invariant atomically.
    """
    stmt = update(MqttBroker).values(is_active=False).where(MqttBroker.is_active.is_(True))
    if except_id is not None:
        stmt = stmt.where(MqttBroker.broker_id != except_id)
    await session.execute(stmt)


# ─── Routes ────────────────────────────────────────────────────────


@router.get("", response_model=list[MqttBrokerOut])
async def list_brokers(user: CurrentUser, session: DbSession) -> list[MqttBrokerOut]:
    """Return every broker, ordered by ``broker_id`` ascending."""
    del user
    rows = await session.execute(select(MqttBroker).order_by(MqttBroker.broker_id))
    return [_to_out(b) for b in rows.scalars().all()]


@router.post("", response_model=MqttBrokerOut, status_code=status.HTTP_201_CREATED)
async def create_broker(
    payload: MqttBrokerIn,
    user: CurrentUser,
    session: DbSession,
) -> MqttBrokerOut:
    """Create a new broker row.

    If ``is_active=True`` (the default), every other row is deactivated
    in the same transaction to preserve the one-active invariant.
    Empty-string ``password`` is treated as "no password", matching
    ``MQTT_PASSWORD=""`` env-var semantics.
    """
    del user

    if payload.is_active:
        await _deactivate_others(session, except_id=None)

    password_enc = encrypt(payload.password) if payload.password else None
    broker = MqttBroker(
        host=payload.host,
        port=payload.port,
        username=payload.username if payload.username else None,
        password_enc=password_enc,
        use_tls=payload.use_tls,
        is_active=payload.is_active,
    )
    session.add(broker)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # The only constraint that should fire here is the one-active
        # partial index, but defensively report it cleanly.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="conflict creating mqtt broker (likely active-row constraint)",
        ) from exc
    await session.refresh(broker)
    await session.commit()
    return _to_out(broker)


@router.get("/{broker_id}", response_model=MqttBrokerOut)
async def get_broker(broker_id: int, user: CurrentUser, session: DbSession) -> MqttBrokerOut:
    del user
    broker = await _get_or_404(session, broker_id)
    return _to_out(broker)


@router.patch("/{broker_id}", response_model=MqttBrokerOut)
async def patch_broker(
    broker_id: int,
    payload: MqttBrokerPatch,
    user: CurrentUser,
    session: DbSession,
) -> MqttBrokerOut:
    """Partial update.

    Password semantics:
      * Field omitted → unchanged
      * Field is ``""`` → cleared (sets ``password_enc`` to NULL)
      * Field is a non-empty string → re-encrypted and stored

    Activation semantics: setting ``is_active=True`` deactivates every
    other row first. Setting ``is_active=False`` is a no-op against the
    invariant — the only-one-active rule allows zero active brokers
    (the ingest then falls back to env-var settings).
    """
    del user
    broker = await _get_or_404(session, broker_id)

    updates = payload.model_dump(exclude_unset=True)

    # Activation must happen BEFORE the field assignment so the
    # deactivation UPDATE doesn't race with our pending change.
    if updates.get("is_active") is True and not broker.is_active:
        await _deactivate_others(session, except_id=broker_id)

    if "password" in updates:
        pw = updates.pop("password")
        broker.password_enc = encrypt(pw) if pw else None
    if "username" in updates:
        # Empty string normalises to NULL — matches Settings default.
        u = updates.pop("username")
        broker.username = u if u else None
    for field, value in updates.items():
        setattr(broker, field, value)

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="conflict updating mqtt broker (likely active-row constraint)",
        ) from exc
    await session.refresh(broker)
    await session.commit()
    return _to_out(broker)


@router.delete("/{broker_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_broker(broker_id: int, user: CurrentUser, session: DbSession) -> None:
    del user
    broker = await _get_or_404(session, broker_id)
    await session.execute(delete(MqttBroker).where(MqttBroker.broker_id == broker.broker_id))
    await session.commit()


@router.post("/{broker_id}/activate", response_model=MqttBrokerOut)
async def activate_broker(
    broker_id: int,
    user: CurrentUser,
    session: DbSession,
) -> MqttBrokerOut:
    """Mark this broker active; deactivate every other row.

    Provided as a dedicated endpoint (in addition to PATCH is_active)
    because operators frequently want a one-click "use this broker"
    button in the UI. The endpoint is idempotent — activating an
    already-active broker is a no-op.
    """
    del user
    broker = await _get_or_404(session, broker_id)
    if not broker.is_active:
        await _deactivate_others(session, except_id=broker_id)
        broker.is_active = True
        await session.flush()
        await session.refresh(broker)
        await session.commit()
    return _to_out(broker)
