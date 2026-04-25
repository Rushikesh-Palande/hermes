"""
DB-backed detector config provider.

Backs ``DetectorConfigProvider`` with rows from the ``parameters`` table,
scoped to the active session's package. Configs are cached in memory at
startup and after every ``reload()``; the hot path (one lookup per
sample) never round-trips to Postgres.

Schema layout — one parameter row per detector type per scope:

    key          = "type_a.config"
    value        = JSONB encoding of the dataclass (e.g. TypeAConfig)
    scope        = GLOBAL | DEVICE | SENSOR
    device_id    = NULL for GLOBAL, set for DEVICE / SENSOR
    sensor_id    = NULL for GLOBAL / DEVICE, set for SENSOR

Resolution walk for ``type_X_for(device_id, sensor_id)``:

    1. SENSOR  — exact match on (device_id, sensor_id)
    2. DEVICE  — match on device_id only
    3. GLOBAL  — fallback

Caches are keyed by a tuple so the hot path is one dict lookup per layer.

Defaults: when no row matches at any scope, the dataclass's own field
defaults are used. ``enabled`` defaults to False on every detector type
so a fresh deployment is silent until the operator turns one on.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from hermes.db.engine import async_session
from hermes.db.models import Parameter, ParameterScope
from hermes.detection.config import (
    TypeAConfig,
    TypeBConfig,
    TypeCConfig,
    TypeDConfig,
)
from hermes.logging import get_logger

_log = get_logger(__name__, component="detection")

# Parameter keys. Stable strings — changing them silently strands existing rows.
KEY_TYPE_A = "type_a.config"
KEY_TYPE_B = "type_b.config"
KEY_TYPE_C = "type_c.config"
KEY_TYPE_D = "type_d.config"

# Map detector key → dataclass type. Used by both ``reload()`` (decode)
# and the API layer (validation when accepting overrides).
KEY_TO_CLS: dict[str, type] = {
    KEY_TYPE_A: TypeAConfig,
    KEY_TYPE_B: TypeBConfig,
    KEY_TYPE_C: TypeCConfig,
    KEY_TYPE_D: TypeDConfig,
}


@dataclass(slots=True)
class _ConfigCache:
    """Per-scope cached configs, populated by ``reload()``."""

    type_a: TypeAConfig
    type_b: TypeBConfig
    type_c: TypeCConfig
    type_d: TypeDConfig


class DbConfigProvider:
    """
    Reads detector configs from ``parameters`` for the given package.

    Construct, call ``await reload()`` once at startup, then pass to the
    DetectionEngine. Mutating endpoints (PUT /api/config/...) call
    ``reload()`` after persisting and then ``engine.reset_device(...)``
    so newly cached detectors pick up the updated config.

    Thread-safety: the provider is read on the asyncio consumer task and
    written via ``reload()`` (also asyncio). Single-threaded by design.
    """

    def __init__(self, package_id: uuid.UUID) -> None:
        self._package_id = package_id
        # Fall back to dataclass defaults until reload() lands.
        self._global = _ConfigCache(
            type_a=TypeAConfig(),
            type_b=TypeBConfig(),
            type_c=TypeCConfig(),
            type_d=TypeDConfig(),
        )
        # device_id → _ConfigCache. None = no device override for that key.
        self._devices: dict[int, _ConfigCache] = {}
        # (device_id, sensor_id) → _ConfigCache.
        self._sensors: dict[tuple[int, int], _ConfigCache] = {}

    @property
    def package_id(self) -> uuid.UUID:
        return self._package_id

    async def reload(self) -> None:
        """Refetch every parameter for the active package and rebuild caches."""
        async with async_session() as session:
            rows = await session.execute(
                select(Parameter).where(Parameter.package_id == self._package_id)
            )
            params = rows.scalars().all()

        # Three buckets keyed by scope. Each bucket stores
        # ``{owner_key: {detector_key: raw_value}}`` so we can build one
        # _ConfigCache per (scope, owner) at the end.
        global_by_key: dict[str, Any] = {}
        device_by_key: dict[int, dict[str, Any]] = {}
        sensor_by_key: dict[tuple[int, int], dict[str, Any]] = {}

        for row in params:
            if row.key not in KEY_TO_CLS:
                continue  # unrelated parameter, ignore
            if row.scope == ParameterScope.GLOBAL:
                global_by_key[row.key] = row.value
            elif row.scope == ParameterScope.DEVICE and row.device_id is not None:
                device_by_key.setdefault(row.device_id, {})[row.key] = row.value
            elif (
                row.scope == ParameterScope.SENSOR
                and row.device_id is not None
                and row.sensor_id is not None
            ):
                sensor_by_key.setdefault((row.device_id, row.sensor_id), {})[row.key] = row.value

        self._global = _build_cache(global_by_key)
        self._devices = {did: _build_cache(by_key) for did, by_key in device_by_key.items()}
        self._sensors = {owner: _build_cache(by_key) for owner, by_key in sensor_by_key.items()}

        _log.info(
            "config_reloaded",
            package_id=str(self._package_id),
            type_a_enabled=self._global.type_a.enabled,
            type_b_enabled=self._global.type_b.enabled,
            type_c_enabled=self._global.type_c.enabled,
            type_d_enabled=self._global.type_d.enabled,
            device_overrides=len(self._devices),
            sensor_overrides=len(self._sensors),
        )

    # ─── DetectorConfigProvider protocol ───────────────────────────

    def type_a_for(self, device_id: int, sensor_id: int) -> TypeAConfig:
        return self._cache_for(device_id, sensor_id).type_a

    def type_b_for(self, device_id: int, sensor_id: int) -> TypeBConfig:
        return self._cache_for(device_id, sensor_id).type_b

    def type_c_for(self, device_id: int, sensor_id: int) -> TypeCConfig:
        return self._cache_for(device_id, sensor_id).type_c

    def type_d_for(self, device_id: int, sensor_id: int) -> TypeDConfig:
        return self._cache_for(device_id, sensor_id).type_d

    # ─── Override introspection (used by /api/config/overrides) ────

    @property
    def global_cache(self) -> _ConfigCache:
        return self._global

    @property
    def device_overrides(self) -> dict[int, _ConfigCache]:
        return dict(self._devices)

    @property
    def sensor_overrides(self) -> dict[tuple[int, int], _ConfigCache]:
        return dict(self._sensors)

    # ─── Internals ─────────────────────────────────────────────────

    def _cache_for(self, device_id: int, sensor_id: int) -> _ConfigCache:
        """Walk SENSOR → DEVICE → GLOBAL and return the first hit per field.

        We don't merge fields across scopes — an override row replaces the
        whole config for that detector type. This matches the legacy
        behaviour: per-sensor rows store full configs, not deltas.
        """
        sensor = self._sensors.get((device_id, sensor_id))
        if sensor is not None:
            return sensor
        device = self._devices.get(device_id)
        if device is not None:
            return device
        return self._global


# ─── Encoding helpers ────────────────────────────────────────────────


def encode_config(cfg: object) -> dict[str, Any]:
    """Serialise a TypeXConfig dataclass to a JSONB-friendly dict."""
    if not dataclasses.is_dataclass(cfg) or isinstance(cfg, type):
        raise TypeError(f"expected a dataclass instance, got {type(cfg).__name__}")
    return dataclasses.asdict(cfg)


def _build_cache(by_key: dict[str, Any]) -> _ConfigCache:
    """Build a _ConfigCache from a {detector_key: raw_value} map."""
    return _ConfigCache(
        type_a=_decode_or_default(by_key.get(KEY_TYPE_A), TypeAConfig),
        type_b=_decode_or_default(by_key.get(KEY_TYPE_B), TypeBConfig),
        type_c=_decode_or_default(by_key.get(KEY_TYPE_C), TypeCConfig),
        type_d=_decode_or_default(by_key.get(KEY_TYPE_D), TypeDConfig),
    )


def _decode_or_default(raw: Any, cls: Any) -> Any:
    """
    Build a dataclass from a JSONB dict, ignoring unknown keys.

    ``cls`` is typed as ``Any`` because mypy cannot bridge the
    ``dataclasses.fields(TypeVar)`` constraint here; the caller binds it
    to a known dataclass type and the return is narrowed at the call
    site by tuple-positional assignment.
    """
    if not isinstance(raw, dict):
        return cls()
    field_names = {f.name for f in dataclasses.fields(cls)}
    filtered = {k: v for k, v in raw.items() if k in field_names}
    try:
        return cls(**filtered)
    except TypeError:
        _log.warning("config_decode_failed_using_defaults", cls=cls.__name__)
        return cls()
