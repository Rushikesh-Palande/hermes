"""
/api/config — read and write detector thresholds.

Operator-facing surface for the four event-detection types.

GLOBAL config (applies to every device + sensor unless overridden):
    GET    /api/config/type_{a,b,c,d}
    PUT    /api/config/type_{a,b,c,d}

Per-device + per-sensor overrides:
    GET    /api/config/{type}/overrides
    PUT    /api/config/{type}/overrides/device/{device_id}
    DELETE /api/config/{type}/overrides/device/{device_id}
    PUT    /api/config/{type}/overrides/sensor/{device_id}/{sensor_id}
    DELETE /api/config/{type}/overrides/sensor/{device_id}/{sensor_id}

Resolution at run-time walks SENSOR → DEVICE → GLOBAL — see
``DbConfigProvider._cache_for``. An override row replaces the WHOLE
config for that detector type at that scope (no field-level merging) —
matches the legacy per-sensor behaviour.

Hot reload semantics:
    Every PUT / DELETE writes the parameter row, then asks the running
    ingest pipeline to reload its cached configs and reset every cached
    detector for every device. Detectors are re-created lazily on the
    next sample, so the new thresholds take effect within one tick.
    No process restart required.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
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

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> TypeCIn:
        if self.threshold_lower >= self.threshold_upper:
            # Pydantic surfaces this as a 422 with the message in `detail`.
            raise ValueError("threshold_lower must be strictly less than threshold_upper")
        return self


class TypeDIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    T4: float = Field(default=10.0, gt=0)
    T5: float = Field(default=30.0, gt=0)
    tolerance_pct: float = Field(default=5.0, ge=0)
    debounce_seconds: float = Field(default=0.0, ge=0)
    init_fill_ratio: float = Field(default=0.9, gt=0, le=1.0)
    expected_sample_rate_hz: float = Field(default=100.0, gt=0)


# Type-name → Pydantic class + parameter key. Used by the override
# endpoints which take the type as a path parameter.
_PYDANTIC_FOR_TYPE: dict[str, type[BaseModel]] = {
    "type_a": TypeAIn,
    "type_b": TypeBIn,
    "type_c": TypeCIn,
    "type_d": TypeDIn,
}

_KEY_FOR_TYPE: dict[str, str] = {
    "type_a": KEY_TYPE_A,
    "type_b": KEY_TYPE_B,
    "type_c": KEY_TYPE_C,
    "type_d": KEY_TYPE_D,
}

# FastAPI Path-parameter constraint that rejects unknown type names with
# a clean 422 instead of a 500 from a missing dict key.
TypeName = Literal["type_a", "type_b", "type_c", "type_d"]


# ─── Override response shapes ─────────────────────────────────────


class SensorOverrideOut(BaseModel):
    device_id: int
    sensor_id: int
    config: dict[str, Any]


class OverridesOut(BaseModel):
    """Layered view of every override for one detector type."""

    devices: dict[str, dict[str, Any]]  # device_id (as str) → config dict
    sensors: list[SensorOverrideOut]


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
    *,
    scope: ParameterScope,
    device_id: int | None,
    sensor_id: int | None,
) -> None:
    """
    Replace the parameter row identified by
    (package_id, scope, device_id, sensor_id, key).
    """
    where = [
        Parameter.package_id == package_id,
        Parameter.scope == scope,
        Parameter.key == key,
    ]
    if device_id is None:
        where.append(Parameter.device_id.is_(None))
    else:
        where.append(Parameter.device_id == device_id)
    if sensor_id is None:
        where.append(Parameter.sensor_id.is_(None))
    else:
        where.append(Parameter.sensor_id == sensor_id)

    existing = (await session.execute(select(Parameter).where(*where))).scalar_one_or_none()
    if existing is not None:
        existing.value = value
    else:
        session.add(
            Parameter(
                package_id=package_id,
                scope=scope,
                device_id=device_id,
                sensor_id=sensor_id,
                key=key,
                value=value,
            )
        )


async def _delete_parameter(
    session: AsyncSession,
    package_id: object,
    key: str,
    *,
    scope: ParameterScope,
    device_id: int | None,
    sensor_id: int | None,
) -> bool:
    """Delete the matching parameter row. Returns True iff a row was removed."""
    where = [
        Parameter.package_id == package_id,
        Parameter.scope == scope,
        Parameter.key == key,
    ]
    if device_id is None:
        where.append(Parameter.device_id.is_(None))
    else:
        where.append(Parameter.device_id == device_id)
    if sensor_id is None:
        where.append(Parameter.sensor_id.is_(None))
    else:
        where.append(Parameter.sensor_id == sensor_id)
    existing = (await session.execute(select(Parameter).where(*where))).scalar_one_or_none()
    if existing is None:
        return False
    await session.delete(existing)
    return True


async def _commit_and_reload(request: Request, session: AsyncSession) -> DbConfigProvider:
    """
    Commit the route's parameter changes, then refresh the live provider
    and drop cached detectors.

    The commit MUST happen before ``provider.reload()`` because the
    provider opens its own session via ``async_session()`` — that fresh
    transaction can't see this route's uncommitted INSERT/UPDATE rows.
    """
    await session.commit()
    provider = _provider_or_503(request)
    await provider.reload()
    pipeline = getattr(request.app.state, "ingest_pipeline", None)
    if pipeline is not None:
        # Detection engine is None in multi-shard live_only mode (the
        # API process subscribes to MQTT only for SSE; detection runs in
        # separate hermes-ingest@N processes and reloads via Postgres
        # NOTIFY — see DbConfigProvider.start_listener).
        engine = pipeline.detection_engine
        if engine is not None:
            for device_id in list(engine.device_ids()):
                engine.reset_device(device_id)
    # Tell the detection shards (if any) to reload + reset. Single-
    # process deployments have no listeners; emitting NOTIFY there is a
    # no-op, so we always emit unconditionally.
    await _notify_config_changed(provider)
    return provider


async def _notify_config_changed(provider: DbConfigProvider) -> None:
    """
    Emit ``NOTIFY hermes_config_changed`` so multi-shard detection
    processes reload their config provider + reset cached detectors.
    """
    from sqlalchemy import text

    from hermes.db.engine import async_session

    async with async_session() as session:
        # Postgres NOTIFY runs as part of the current transaction; we
        # commit explicitly to publish the message immediately.
        await session.execute(
            text("SELECT pg_notify('hermes_config_changed', :payload)"),
            {"payload": str(provider.package_id)},
        )
        await session.commit()


def _cache_to_dict(cache: object, type_name: TypeName) -> dict[str, Any]:
    """Pull the matching dataclass off a _ConfigCache and asdict() it."""
    attr = type_name  # _ConfigCache fields are named type_a / type_b / type_c / type_d
    return asdict(getattr(cache, attr))


def _validate_or_422(type_name: TypeName, payload: dict[str, Any]) -> BaseModel:
    """
    Run Pydantic validation against the right TypeXIn model and re-raise
    failures as ``HTTPException(422)`` so FastAPI surfaces the same shape
    it would for a parameter-binding error. Without this, a manual
    ``model_validate`` inside the handler raises ``ValidationError`` and
    bubbles as a 500.

    ``include_context=False`` is required: Pydantic v2's default error
    output includes the underlying ``ValueError`` instance in
    ``ctx.error``, which is not JSON-serialisable and would crash the
    response writer with a ``TypeError`` before the 422 reaches the
    client.
    """
    pyd_cls = _PYDANTIC_FOR_TYPE[type_name]
    try:
        return pyd_cls.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(include_url=False, include_context=False),
        ) from exc


# ─── Routes — global config ────────────────────────────────────────


@router.get("/type_a", response_model=TypeAIn)
async def get_type_a(request: Request, user: CurrentUser) -> TypeAIn:
    del user
    return TypeAIn(**asdict(_provider_or_503(request).type_a_for(0, 0)))


@router.put("/type_a", response_model=TypeAIn)
async def put_type_a(
    payload: TypeAIn, request: Request, user: CurrentUser, session: DbSession
) -> TypeAIn:
    del user
    provider = _provider_or_503(request)
    await _upsert_parameter(
        session,
        provider.package_id,
        KEY_TYPE_A,
        payload.model_dump(),
        scope=ParameterScope.GLOBAL,
        device_id=None,
        sensor_id=None,
    )
    await _commit_and_reload(request, session)
    return TypeAIn(**asdict(_provider_or_503(request).type_a_for(0, 0)))


@router.get("/type_b", response_model=TypeBIn)
async def get_type_b(request: Request, user: CurrentUser) -> TypeBIn:
    del user
    return TypeBIn(**asdict(_provider_or_503(request).type_b_for(0, 0)))


@router.put("/type_b", response_model=TypeBIn)
async def put_type_b(
    payload: TypeBIn, request: Request, user: CurrentUser, session: DbSession
) -> TypeBIn:
    del user
    provider = _provider_or_503(request)
    await _upsert_parameter(
        session,
        provider.package_id,
        KEY_TYPE_B,
        payload.model_dump(),
        scope=ParameterScope.GLOBAL,
        device_id=None,
        sensor_id=None,
    )
    await _commit_and_reload(request, session)
    return TypeBIn(**asdict(_provider_or_503(request).type_b_for(0, 0)))


@router.get("/type_c", response_model=TypeCIn)
async def get_type_c(request: Request, user: CurrentUser) -> TypeCIn:
    del user
    return TypeCIn(**asdict(_provider_or_503(request).type_c_for(0, 0)))


@router.put("/type_c", response_model=TypeCIn)
async def put_type_c(
    payload: TypeCIn, request: Request, user: CurrentUser, session: DbSession
) -> TypeCIn:
    del user
    provider = _provider_or_503(request)
    await _upsert_parameter(
        session,
        provider.package_id,
        KEY_TYPE_C,
        payload.model_dump(),
        scope=ParameterScope.GLOBAL,
        device_id=None,
        sensor_id=None,
    )
    await _commit_and_reload(request, session)
    return TypeCIn(**asdict(_provider_or_503(request).type_c_for(0, 0)))


@router.get("/type_d", response_model=TypeDIn)
async def get_type_d(request: Request, user: CurrentUser) -> TypeDIn:
    del user
    return TypeDIn(**asdict(_provider_or_503(request).type_d_for(0, 0)))


@router.put("/type_d", response_model=TypeDIn)
async def put_type_d(
    payload: TypeDIn, request: Request, user: CurrentUser, session: DbSession
) -> TypeDIn:
    del user
    provider = _provider_or_503(request)
    await _upsert_parameter(
        session,
        provider.package_id,
        KEY_TYPE_D,
        payload.model_dump(),
        scope=ParameterScope.GLOBAL,
        device_id=None,
        sensor_id=None,
    )
    await _commit_and_reload(request, session)
    return TypeDIn(**asdict(_provider_or_503(request).type_d_for(0, 0)))


# ─── Routes — overrides (per-device + per-sensor) ──────────────────


@router.get("/{type_name}/overrides", response_model=OverridesOut)
async def list_overrides(type_name: TypeName, request: Request, user: CurrentUser) -> OverridesOut:
    """Return every per-device and per-sensor override for one detector type."""
    del user
    provider = _provider_or_503(request)
    devices = {
        str(did): _cache_to_dict(cache, type_name)
        for did, cache in provider.device_overrides.items()
    }
    sensors = [
        SensorOverrideOut(
            device_id=did,
            sensor_id=sid,
            config=_cache_to_dict(cache, type_name),
        )
        for (did, sid), cache in provider.sensor_overrides.items()
    ]
    sensors.sort(key=lambda s: (s.device_id, s.sensor_id))
    return OverridesOut(devices=devices, sensors=sensors)


@router.put(
    "/{type_name}/overrides/device/{device_id}",
    response_model=dict,
)
async def put_device_override(
    type_name: TypeName,
    device_id: Annotated[int, Path(ge=1, le=999)],
    payload: dict[str, Any],
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> dict[str, Any]:
    """
    Upsert the device-scope override for one detector type. The payload
    must validate against the detector's Pydantic model (Type{A,B,C,D}In).
    """
    del user
    validated = _validate_or_422(type_name, payload)
    provider = _provider_or_503(request)
    await _upsert_parameter(
        session,
        provider.package_id,
        _KEY_FOR_TYPE[type_name],
        validated.model_dump(),
        scope=ParameterScope.DEVICE,
        device_id=device_id,
        sensor_id=None,
    )
    await _commit_and_reload(request, session)
    return validated.model_dump()


@router.delete(
    "/{type_name}/overrides/device/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_device_override(
    type_name: TypeName,
    device_id: Annotated[int, Path(ge=1, le=999)],
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> None:
    del user
    provider = _provider_or_503(request)
    removed = await _delete_parameter(
        session,
        provider.package_id,
        _KEY_FOR_TYPE[type_name],
        scope=ParameterScope.DEVICE,
        device_id=device_id,
        sensor_id=None,
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no {type_name} device override for device {device_id}",
        )
    await _commit_and_reload(request, session)


@router.put(
    "/{type_name}/overrides/sensor/{device_id}/{sensor_id}",
    response_model=dict,
)
async def put_sensor_override(
    type_name: TypeName,
    device_id: Annotated[int, Path(ge=1, le=999)],
    sensor_id: Annotated[int, Path(ge=1, le=12)],
    payload: dict[str, Any],
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> dict[str, Any]:
    del user
    validated = _validate_or_422(type_name, payload)
    provider = _provider_or_503(request)
    await _upsert_parameter(
        session,
        provider.package_id,
        _KEY_FOR_TYPE[type_name],
        validated.model_dump(),
        scope=ParameterScope.SENSOR,
        device_id=device_id,
        sensor_id=sensor_id,
    )
    await _commit_and_reload(request, session)
    return validated.model_dump()


@router.delete(
    "/{type_name}/overrides/sensor/{device_id}/{sensor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_sensor_override(
    type_name: TypeName,
    device_id: Annotated[int, Path(ge=1, le=999)],
    sensor_id: Annotated[int, Path(ge=1, le=12)],
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> None:
    del user
    provider = _provider_or_503(request)
    removed = await _delete_parameter(
        session,
        provider.package_id,
        _KEY_FOR_TYPE[type_name],
        scope=ParameterScope.SENSOR,
        device_id=device_id,
        sensor_id=sensor_id,
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(f"no {type_name} sensor override for device {device_id} sensor {sensor_id}"),
        )
    await _commit_and_reload(request, session)


# Suppress unused-import warnings; the dataclass types are referenced via
# DbConfigProvider but Python's static analysers can't see the dynamic link.
_ = (TypeAConfig, TypeBConfig, TypeCConfig, TypeDConfig)
