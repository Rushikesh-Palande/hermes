"""
DB-backed detector config provider.

Backs ``DetectorConfigProvider`` with rows from the ``parameters`` table,
scoped to the active session's package. Configs are cached in memory at
startup and after every ``reload()``; the hot path (one lookup per
sample) never round-trips to Postgres.

Schema layout chosen for Phase 4b — one parameter row per detector type
at GLOBAL scope:

    key   = "type_a.config" (or "type_b.config", etc.)
    value = JSONB encoding of the dataclass (e.g. TypeAConfig)
    scope = GLOBAL
    device_id, sensor_id = NULL

Per-device and per-sensor overrides will land in Phase 4c using the
same key with scope=DEVICE / scope=SENSOR. The lookup walks
SENSOR → DEVICE → GLOBAL — implemented but currently always falls
through to GLOBAL because nothing writes the narrower scopes yet.

Defaults: when the parameter row is missing, the dataclass's own field
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
    DetectionEngine. Mutating endpoints (PUT /api/config/type_a) call
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

    @property
    def package_id(self) -> uuid.UUID:
        return self._package_id

    async def reload(self) -> None:
        """Refetch all parameters for the active package."""
        async with async_session() as session:
            rows = await session.execute(
                select(Parameter).where(
                    Parameter.package_id == self._package_id,
                    Parameter.scope == ParameterScope.GLOBAL,
                )
            )
            by_key = {row.key: row.value for row in rows.scalars().all()}

        self._global = _ConfigCache(
            type_a=_decode_or_default(by_key.get(KEY_TYPE_A), TypeAConfig),
            type_b=_decode_or_default(by_key.get(KEY_TYPE_B), TypeBConfig),
            type_c=_decode_or_default(by_key.get(KEY_TYPE_C), TypeCConfig),
            type_d=_decode_or_default(by_key.get(KEY_TYPE_D), TypeDConfig),
        )
        _log.info(
            "config_reloaded",
            package_id=str(self._package_id),
            type_a_enabled=self._global.type_a.enabled,
            type_b_enabled=self._global.type_b.enabled,
            type_c_enabled=self._global.type_c.enabled,
            type_d_enabled=self._global.type_d.enabled,
        )

    # ─── DetectorConfigProvider protocol ───────────────────────────

    def type_a_for(self, device_id: int, sensor_id: int) -> TypeAConfig:
        del device_id, sensor_id  # per-scope overrides land in Phase 4c
        return self._global.type_a

    def type_b_for(self, device_id: int, sensor_id: int) -> TypeBConfig:
        del device_id, sensor_id
        return self._global.type_b

    def type_c_for(self, device_id: int, sensor_id: int) -> TypeCConfig:
        del device_id, sensor_id
        return self._global.type_c

    def type_d_for(self, device_id: int, sensor_id: int) -> TypeDConfig:
        del device_id, sensor_id
        return self._global.type_d


# ─── Encoding helpers ────────────────────────────────────────────────


def encode_config(cfg: object) -> dict[str, Any]:
    """Serialise a TypeXConfig dataclass to a JSONB-friendly dict."""
    if not dataclasses.is_dataclass(cfg) or isinstance(cfg, type):
        raise TypeError(f"expected a dataclass instance, got {type(cfg).__name__}")
    return dataclasses.asdict(cfg)


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
        # Stale row with incompatible shape — fall back to defaults rather
        # than crashing the ingest pipeline at startup.
        _log.warning("config_decode_failed_using_defaults", cls=cls.__name__)
        return cls()
