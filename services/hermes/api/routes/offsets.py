"""
/api/devices/{device_id}/offsets — zero-point calibration per sensor.

The ingest pipeline applies ``corrected = raw - offset`` for every
incoming sample (see ``hermes.ingest.offsets.OffsetCache.apply``). This
router lets an operator read and edit those offsets without touching
the DB by hand.

Endpoints:

    GET    /api/devices/{device_id}/offsets
        → list of 12 entries, one per sensor (1..12). Sensors with no
          row default to 0.0.

    PUT    /api/devices/{device_id}/offsets
        → replace ALL offsets for the device. Body shape:
          ``{"1": 0.5, "2": -0.3, …}``. Missing sensor IDs are reset to
          0.0 (i.e. their row is deleted).

    PUT    /api/devices/{device_id}/offsets/{sensor_id}
        → upsert a single sensor's offset. Body: ``{"offset_value": …}``.

    DELETE /api/devices/{device_id}/offsets/{sensor_id}
        → remove the override (effective value reverts to 0.0).

Hot reload: every mutation writes to the DB, then refreshes the
running ``OffsetCache`` so the next sample uses the new value within
one tick. No ingest restart required.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from hermes.api.deps import CurrentUser, DbSession
from hermes.db.models import Device, SensorOffset
from hermes.ingest.offsets import OffsetCache

router = APIRouter()

NUM_SENSORS: int = 12


# ─── Shapes ────────────────────────────────────────────────────────


class SensorOffsetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sensor_id: int
    offset_value: float
    updated_at: datetime | None


class DeviceOffsetsOut(BaseModel):
    """All 12 sensors' offsets for one device, sensor_id order."""

    device_id: int
    offsets: list[SensorOffsetOut]


class SingleOffsetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset_value: float


class BulkOffsetsIn(BaseModel):
    """
    Bulk replacement for all sensor offsets on one device.

    Keys are sensor IDs as strings (1..12). Sensors omitted from the
    body are RESET (their row is deleted), so the caller can clear all
    offsets with ``{}``. Use the single-sensor PUT to edit one without
    touching the others.
    """

    model_config = ConfigDict(extra="forbid")
    offsets: dict[str, float] = Field(default_factory=dict)


# ─── Helpers ───────────────────────────────────────────────────────


async def _device_or_404(session: AsyncSession, device_id: int) -> Device:
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device {device_id} not found",
        )
    return device


async def _row_map(session: AsyncSession, device_id: int) -> dict[int, SensorOffset]:
    rows = await session.execute(select(SensorOffset).where(SensorOffset.device_id == device_id))
    return {r.sensor_id: r for r in rows.scalars().all()}


def _offset_cache(request: Request) -> OffsetCache | None:
    """Return the live OffsetCache if the pipeline is up; otherwise None.

    Mutations don't 503 when the cache is missing — DB writes still
    succeed and the next ingest restart picks them up — but we log it.
    """
    pipeline = getattr(request.app.state, "ingest_pipeline", None)
    if pipeline is None:
        return None
    cache = getattr(pipeline, "offset_cache", None)
    if not isinstance(cache, OffsetCache):
        return None
    return cache


async def _build_listing(session: AsyncSession, device_id: int) -> DeviceOffsetsOut:
    """Build the canonical 12-row response for one device."""
    rows = await _row_map(session, device_id)
    out = [
        SensorOffsetOut(
            sensor_id=sid,
            offset_value=float(rows[sid].offset_value) if sid in rows else 0.0,
            updated_at=rows[sid].updated_at if sid in rows else None,
        )
        for sid in range(1, NUM_SENSORS + 1)
    ]
    return DeviceOffsetsOut(device_id=device_id, offsets=out)


async def _refresh_device(
    request: Request, session: AsyncSession, device_id: int
) -> dict[int, float]:
    """Re-read the DB and push the device's entry into the live cache."""
    rows = await session.execute(select(SensorOffset).where(SensorOffset.device_id == device_id))
    fresh = {row.sensor_id: float(row.offset_value) for row in rows.scalars().all()}
    cache = _offset_cache(request)
    if cache is not None:
        cache.load(device_id, fresh)
    return fresh


# ─── Routes ────────────────────────────────────────────────────────


@router.get("", response_model=DeviceOffsetsOut)
async def list_offsets(
    device_id: Annotated[int, Path(ge=1, le=999)],
    user: CurrentUser,
    session: DbSession,
) -> DeviceOffsetsOut:
    """List all 12 sensors' offsets. Missing rows default to 0.0."""
    del user
    await _device_or_404(session, device_id)
    return await _build_listing(session, device_id)


@router.put("", response_model=DeviceOffsetsOut)
async def replace_offsets(
    device_id: Annotated[int, Path(ge=1, le=999)],
    payload: BulkOffsetsIn,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> DeviceOffsetsOut:
    """
    Replace every offset for this device.

    Sensors not in the body are reset (row deleted). Send ``{}`` to wipe.
    """
    del user
    await _device_or_404(session, device_id)

    # Validate sensor IDs in the body before touching anything.
    parsed: dict[int, float] = {}
    for raw_key, raw_val in payload.offsets.items():
        try:
            sid = int(raw_key)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"sensor_id keys must be integers, got {raw_key!r}",
            ) from exc
        if sid < 1 or sid > NUM_SENSORS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"sensor_id {sid} outside 1..{NUM_SENSORS}",
            )
        parsed[sid] = float(raw_val)

    existing = await _row_map(session, device_id)
    # Upsert / update entries that landed in the body.
    for sid, value in parsed.items():
        row = existing.get(sid)
        if row is None:
            session.add(
                SensorOffset(
                    device_id=device_id,
                    sensor_id=sid,
                    offset_value=value,
                )
            )
        else:
            row.offset_value = value
    # Drop rows for sensors that were omitted.
    for sid in set(existing) - set(parsed):
        await session.execute(
            delete(SensorOffset).where(
                SensorOffset.device_id == device_id,
                SensorOffset.sensor_id == sid,
            )
        )

    await session.commit()
    await _refresh_device(request, session, device_id)
    return await _build_listing(session, device_id)


@router.put("/{sensor_id}", response_model=SensorOffsetOut)
async def upsert_one(
    device_id: Annotated[int, Path(ge=1, le=999)],
    sensor_id: Annotated[int, Path(ge=1, le=NUM_SENSORS)],
    payload: SingleOffsetIn,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> SensorOffsetOut:
    """Upsert a single sensor's offset; other sensors untouched."""
    del user
    await _device_or_404(session, device_id)
    existing = await session.get(SensorOffset, (device_id, sensor_id))
    if existing is None:
        existing = SensorOffset(
            device_id=device_id,
            sensor_id=sensor_id,
            offset_value=payload.offset_value,
        )
        session.add(existing)
    else:
        existing.offset_value = payload.offset_value
    await session.commit()
    await session.refresh(existing)
    await _refresh_device(request, session, device_id)
    return SensorOffsetOut.model_validate(existing)


@router.delete("/{sensor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_one(
    device_id: Annotated[int, Path(ge=1, le=999)],
    sensor_id: Annotated[int, Path(ge=1, le=NUM_SENSORS)],
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> None:
    """Remove a single sensor's override; effective value reverts to 0.0."""
    del user
    await _device_or_404(session, device_id)
    existing = await session.get(SensorOffset, (device_id, sensor_id))
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no offset for device {device_id} sensor {sensor_id}",
        )
    await session.execute(
        delete(SensorOffset).where(
            SensorOffset.device_id == device_id,
            SensorOffset.sensor_id == sensor_id,
        )
    )
    await session.commit()
    await _refresh_device(request, session, device_id)
