"""
/api/devices — device CRUD.

Devices are operator-owned data sources (STM32 over MQTT by default,
Modbus TCP as a legacy option). Creating a device does NOT start
ingestion; a separate call to /api/devices/{id}/start activates
the MQTT subscription.

Routes in this module are a SCAFFOLD — implementations arrive in the
Phase 1 "device CRUD" PR. Signatures are fixed so the UI team can build
against them.

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

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from hermes.api.deps import CurrentUser, DbSession
from hermes.db.models import DeviceProtocol

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
    """Response shape for list + get."""

    device_id: int
    name: str
    protocol: DeviceProtocol
    topic: str | None
    is_active: bool


# ─── Routes ────────────────────────────────────────────────────────


@router.get("", response_model=list[DeviceOut])
async def list_devices(_: CurrentUser, session: DbSession) -> list[DeviceOut]:
    """Return all devices. Authentication required."""
    _ = session  # stub
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="list_devices not yet implemented (scaffold only)",
    )


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def create_device(
    payload: DeviceIn, _: CurrentUser, session: DbSession
) -> DeviceOut:
    """Create a new device row. Does NOT start ingestion."""
    _ = payload  # stub
    _ = session
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="create_device not yet implemented (scaffold only)",
    )


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(device_id: int, _: CurrentUser, session: DbSession) -> DeviceOut:
    _ = session
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"get_device({device_id}) not yet implemented",
    )


@router.patch("/{device_id}", response_model=DeviceOut)
async def patch_device(
    device_id: int, payload: DevicePatch, _: CurrentUser, session: DbSession
) -> DeviceOut:
    _ = payload
    _ = session
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"patch_device({device_id}) not yet implemented",
    )


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(device_id: int, _: CurrentUser, session: DbSession) -> None:
    _ = session
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"delete_device({device_id}) not yet implemented",
    )
