"""
/api/config — read and write detector thresholds.

Operator-facing surface for the four event-detection types. Each
detector has its own GET / PUT pair so the UI can render distinct
forms; under the hood they all write to the same ``parameters`` table
under the active package's GLOBAL scope.

Hot reload semantics:
    PUT writes the parameter row, then asks the running ingest
    pipeline to (a) reload its cached configs and (b) reset every
    cached detector for every device. Detectors are re-created lazily
    on the next sample, so the new thresholds take effect within one
    sample tick. No process restart required.

Per-device / per-sensor overrides land in Phase 4c. The endpoints here
all target GLOBAL scope.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hermes.api.deps import CurrentUser, DbSession
from hermes.db.models import Parameter, ParameterScope
from hermes.detection.config import (
    TypeAConfig,
    TypeBConfig,
    TypeCConfig,
    TypeDConfig,
)
from hermes.detection.db_config import (
    KEY_TYPE_A,
    KEY_TYPE_B,
    KEY_TYPE_C,
    KEY_TYPE_D,
    DbConfigProvider,
)

router = APIRouter()


# ─── Pydantic mirrors of the dataclass configs ─────────────────────
#
# The dataclasses in detection/config.py are the source of truth; these
# Pydantic models exist solely for HTTP I/O validation. Conversion goes
# both ways through ``encode_config`` / ``model_dump``.


class TypeAIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    T1: float = Field(default=1.0, gt=0)
    threshold_cv: float = Field(default=5.0, ge=0)
    debounce_seconds: float = Field(default=0.0, ge=0)
    init_fill_ratio: float = Field(default=0.9, gt=0, le=1.0)
    expected_sample_rate_hz: float = Field(default=100.0, gt=0)


class TypeBIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    T2: float = Field(default=5.0, gt=0)
    lower_threshold_pct: float = Field(default=5.0, ge=0)
    upper_threshold_pct: float = Field(default=5.0, ge=0)
    debounce_seconds: float = Field(default=0.0, ge=0)
    init_fill_ratio: float = Field(default=0.9, gt=0, le=1.0)
    expected_sample_rate_hz: float = Field(default=100.0, gt=0)


class TypeCIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    T3: float = Field(default=10.0, gt=0)
    threshold_lower: float = 0.0
    threshold_upper: float = 100.0
    debounce_seconds: float = Field(default=0.0, ge=0)
    init_fill_ratio: float = Field(default=0.9, gt=0, le=1.0)
    expected_sample_rate_hz: float = Field(default=100.0, gt=0)


class TypeDIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    T4: float = Field(default=10.0, gt=0)
    T5: float = Field(default=30.0, gt=0)
    tolerance_pct: float = Field(default=5.0, ge=0)
    debounce_seconds: float = Field(default=0.0, ge=0)
    init_fill_ratio: float = Field(default=0.9, gt=0, le=1.0)
    expected_sample_rate_hz: float = Field(default=100.0, gt=0)


# ─── Helpers ───────────────────────────────────────────────────────


def _provider_or_503(request: Request) -> DbConfigProvider:
    """Resolve the live DbConfigProvider; 503 if the pipeline isn't up."""
    provider = getattr(request.app.state, "config_provider", None)
    if not isinstance(provider, DbConfigProvider):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="config provider not initialised — ingest pipeline down",
        )
    return provider


async def _upsert_parameter(
    session: AsyncSession,
    package_id: object,
    key: str,
    value: dict[str, object],
) -> None:
    """
    Replace the GLOBAL parameter row for ``key`` under ``package_id``.

    Implementation: delete-then-insert so we don't have to express the
    composite uniqueness as a stable upsert constraint right now. A
    Phase-4c migration will add `(package_id, scope, device_id, sensor_id, key)`
    as a unique index and convert this to ON CONFLICT.
    """
    existing = (
        await session.execute(
            select(Parameter).where(
                Parameter.package_id == package_id,
                Parameter.scope == ParameterScope.GLOBAL,
                Parameter.device_id.is_(None),
                Parameter.sensor_id.is_(None),
                Parameter.key == key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.value = value
    else:
        session.add(
            Parameter(
                package_id=package_id,
                scope=ParameterScope.GLOBAL,
                device_id=None,
                sensor_id=None,
                key=key,
                value=value,
            )
        )


async def _apply_update(
    request: Request,
    session: AsyncSession,
    key: str,
    value_dict: dict[str, object],
) -> DbConfigProvider:
    """Persist + hot-reload + reset detectors. Returns the live provider."""
    provider = _provider_or_503(request)
    await _upsert_parameter(session, provider.package_id, key, value_dict)
    await session.flush()
    await provider.reload()
    pipeline = getattr(request.app.state, "ingest_pipeline", None)
    if pipeline is not None:
        engine = pipeline.detection_engine
        # Reset every cached device so the new config takes effect.
        for device_id in list(engine.device_ids()):
            engine.reset_device(device_id)
    return provider


# ─── Routes ────────────────────────────────────────────────────────


@router.get("/type_a", response_model=TypeAIn)
async def get_type_a(request: Request, user: CurrentUser) -> TypeAIn:
    del user
    cfg = _provider_or_503(request).type_a_for(0, 0)
    return TypeAIn(**asdict(cfg))


@router.put("/type_a", response_model=TypeAIn)
async def put_type_a(
    payload: Annotated[TypeAIn, ...],
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> TypeAIn:
    del user
    await _apply_update(request, session, KEY_TYPE_A, payload.model_dump())
    return TypeAIn(**asdict(_provider_or_503(request).type_a_for(0, 0)))


@router.get("/type_b", response_model=TypeBIn)
async def get_type_b(request: Request, user: CurrentUser) -> TypeBIn:
    del user
    cfg = _provider_or_503(request).type_b_for(0, 0)
    return TypeBIn(**asdict(cfg))


@router.put("/type_b", response_model=TypeBIn)
async def put_type_b(
    payload: Annotated[TypeBIn, ...],
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> TypeBIn:
    del user
    await _apply_update(request, session, KEY_TYPE_B, payload.model_dump())
    return TypeBIn(**asdict(_provider_or_503(request).type_b_for(0, 0)))


@router.get("/type_c", response_model=TypeCIn)
async def get_type_c(request: Request, user: CurrentUser) -> TypeCIn:
    del user
    cfg = _provider_or_503(request).type_c_for(0, 0)
    return TypeCIn(**asdict(cfg))


@router.put("/type_c", response_model=TypeCIn)
async def put_type_c(
    payload: Annotated[TypeCIn, ...],
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> TypeCIn:
    del user
    # Validate threshold ordering — thresholds are absolute, not bands;
    # lower > upper means the detector either always or never triggers.
    if payload.threshold_lower >= payload.threshold_upper:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="threshold_lower must be strictly less than threshold_upper",
        )
    await _apply_update(request, session, KEY_TYPE_C, payload.model_dump())
    return TypeCIn(**asdict(_provider_or_503(request).type_c_for(0, 0)))


@router.get("/type_d", response_model=TypeDIn)
async def get_type_d(request: Request, user: CurrentUser) -> TypeDIn:
    del user
    cfg = _provider_or_503(request).type_d_for(0, 0)
    return TypeDIn(**asdict(cfg))


@router.put("/type_d", response_model=TypeDIn)
async def put_type_d(
    payload: Annotated[TypeDIn, ...],
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> TypeDIn:
    del user
    await _apply_update(request, session, KEY_TYPE_D, payload.model_dump())
    return TypeDIn(**asdict(_provider_or_503(request).type_d_for(0, 0)))


# Silence unused-import warnings; the dataclass types are referenced via
# DbConfigProvider but Python's static analysers can't see the dynamic link.
_ = (TypeAConfig, TypeBConfig, TypeCConfig, TypeDConfig)
