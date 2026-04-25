"""
/api/devices — device CRUD.

Devices are operator-owned data sources (STM32 over MQTT by default,
Modbus TCP as a legacy option). Creating a device does NOT start
ingestion — MQTT ingestion is always-on for the subscribed topic; a
device row is the logical record that ties samples to a name and
owns per-sensor config.

Design notes carried over from the legacy system:

    * device_id is operator-assigned (1..999), not auto-incremented.
      Operators reference devices by number on the dashboard; changing
      the numbering mid-life breaks their muscle memory.
    * `is_active` is a soft-disable flag — the row persists so historical
      events still resolve the FK, but ingestion skips it.
    * PATCH is used for partial updates (name, active, topic); PUT would
      require the caller to round-trip every field.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hermes.api.deps import CurrentUser, DbSession
from hermes.db.models import Device, DeviceProtocol

router = APIRouter()


# ─── Shapes ────────────────────────────────────────────────────────


class DeviceIn(BaseModel):
    """POST /api/devices body."""

    device_id: int = Field(..., ge=1, le=999)
    name: str = Field(..., min_length=1, max_length=120)
    protocol: DeviceProtocol = DeviceProtocol.MQTT
    topic: str | None = None


class DevicePatch(BaseModel):
    """PATCH /api/devices/{id} body. All fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None
    topic: str | None = None


class DeviceOut(BaseModel):
    """Response shape for list + get + create + patch."""

    model_config = ConfigDict(from_attributes=True)

    device_id: int
    name: str
    protocol: DeviceProtocol
    topic: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ─── Helpers ───────────────────────────────────────────────────────


async def _get_device_or_404(session: AsyncSession, device_id: int) -> Device:
    """Return the Device row or raise 404."""
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device {device_id} not found",
        )
    return device


# ─── Routes ────────────────────────────────────────────────────────


@router.get("", response_model=list[DeviceOut])
async def list_devices(user: CurrentUser, session: DbSession) -> list[DeviceOut]:
    """Return all devices, ordered by device_id."""
    del user
    rows = await session.execute(select(Device).order_by(Device.device_id))
    return [DeviceOut.model_validate(d) for d in rows.scalars().all()]


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def create_device(payload: DeviceIn, user: CurrentUser, session: DbSession) -> DeviceOut:
    """
    Create a new device row.

    Returns 409 if device_id is already in use. For MQTT devices with
    no ``topic``, the ingest process uses the broker-wide default from
    ``MQTT_TOPIC_ADC`` — the topic field is a per-device override, not
    a requirement.
    """
    del user
    device = Device(
        device_id=payload.device_id,
        name=payload.name,
        protocol=payload.protocol,
        topic=payload.topic,
        is_active=True,
    )
    session.add(device)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"device {payload.device_id} already exists",
        ) from exc
    await session.refresh(device)
    return DeviceOut.model_validate(device)


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(device_id: int, user: CurrentUser, session: DbSession) -> DeviceOut:
    del user
    device = await _get_device_or_404(session, device_id)
    return DeviceOut.model_validate(device)


@router.patch("/{device_id}", response_model=DeviceOut)
async def patch_device(
    device_id: int,
    payload: DevicePatch,
    user: CurrentUser,
    session: DbSession,
) -> DeviceOut:
    """
    Partial update. Fields omitted from the body are left unchanged.

    ``updated_at`` is refreshed by the ``touch_updated_at`` trigger
    in ``0004_triggers.sql``; we don't touch it here.
    """
    del user
    device = await _get_device_or_404(session, device_id)

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(device, field, value)

    await session.flush()
    await session.refresh(device)
    return DeviceOut.model_validate(device)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(device_id: int, user: CurrentUser, session: DbSession) -> None:
    """
    Hard-delete a device row.

    ``ON DELETE CASCADE`` on sensor_offsets removes calibration rows;
    events reference the device via FK and will block deletion if any
    events exist — callers should soft-disable via PATCH is_active=false
    instead of delete for devices with history.
    """
    del user
    device = await _get_device_or_404(session, device_id)
    await session.execute(delete(Device).where(Device.device_id == device.device_id))
