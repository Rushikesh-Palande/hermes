"""
Per-detector configuration.

Frozen dataclasses keep configs hashable and cheap to pass around.
They carry no defaults-from-DB logic; loading + merge lives in a
``ConfigProvider`` (also defined here) so the detectors can stay pure.

The initial provider is a ``StaticConfigProvider`` used during Phase 3b
development and tests. Phase 3e replaces it with a DB-backed provider
that reads ``event_config_type_a`` and the ``_per_sensor`` overrides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Legacy constant used in Type B and Type D tolerance bands:
#   bound = avg ± (REF_VALUE × tolerance_pct / 100)
# With REF_VALUE=100 this simplifies to `bound = avg ± tolerance_pct`,
# but the code is written out explicitly so the formula survives any
# future change to REF_VALUE.
REF_VALUE: float = 100.0


@dataclass(frozen=True, slots=True)
class TypeAConfig:
    """
    Type A (variance / CV%) detector configuration.

    ``T1`` is the sliding-window length in seconds; the detector fires
    when CV%(t) > ``threshold_cv`` over ``(t - T1, t]``. Legacy parity:
    only the lower threshold is checked (upper was loaded but unused).
    """

    enabled: bool = False
    T1: float = 1.0
    threshold_cv: float = 5.0
    debounce_seconds: float = 0.0
    # When window_count reaches this fraction of `T1 × expected_rate`,
    # the detector flips to "warm" and starts emitting events. Legacy
    # hardcodes 0.9 for Type A.
    init_fill_ratio: float = 0.9
    # Used only for buffer sizing and the init-fill threshold — the
    # window itself slides on real timestamps, not sample count.
    expected_sample_rate_hz: float = 100.0


class DetectorConfigProvider(Protocol):
    """
    Resolves per-sensor detector config.

    The engine calls ``type_a_for(device_id, sensor_id)`` lazily on the
    first sample for that sensor, caches the result, and creates the
    detector. ``invalidate()`` (future) will be called from the config
    API after the operator saves a new threshold.
    """

    def type_a_for(self, device_id: int, sensor_id: int) -> TypeAConfig: ...


class StaticConfigProvider:
    """
    Returns a single ``TypeAConfig`` regardless of device or sensor.

    Used in tests and as the Phase 3b stand-in before the DB-backed
    provider lands. Safe to share across the entire engine because the
    dataclass is frozen.
    """

    def __init__(self, type_a: TypeAConfig) -> None:
        self._type_a = type_a

    def type_a_for(self, device_id: int, sensor_id: int) -> TypeAConfig:
        del device_id, sensor_id
        return self._type_a
