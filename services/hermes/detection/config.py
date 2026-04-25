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
    init_fill_ratio: float = 0.9
    expected_sample_rate_hz: float = 100.0


@dataclass(frozen=True, slots=True)
class TypeBConfig:
    """
    Type B (post-window deviation) detector configuration.

    Fires when the latest sample falls outside a tolerance band centred
    on the T2-second rolling mean:

        lower = avg_T2 − (REF_VALUE × lower_threshold_pct) / 100
        upper = avg_T2 + (REF_VALUE × upper_threshold_pct) / 100
        fire when value < lower OR value > upper

    Tolerance percentages are asymmetric (upper and lower can differ).
    """

    enabled: bool = False
    T2: float = 5.0
    lower_threshold_pct: float = 5.0
    upper_threshold_pct: float = 5.0
    debounce_seconds: float = 0.0
    init_fill_ratio: float = 0.9
    expected_sample_rate_hz: float = 100.0


@dataclass(frozen=True, slots=True)
class TypeCConfig:
    """
    Type C (range-based on avg_T3) detector configuration.

    Fires when the T3-second rolling mean itself leaves absolute bounds:

        fire when avg_T3 < threshold_lower OR avg_T3 > threshold_upper

    Thresholds are raw sensor units, not percentages.
    """

    enabled: bool = False
    T3: float = 10.0
    threshold_lower: float = 0.0
    threshold_upper: float = 100.0
    debounce_seconds: float = 0.0
    init_fill_ratio: float = 0.9
    expected_sample_rate_hz: float = 100.0


@dataclass(frozen=True, slots=True)
class ModeSwitchingConfig:
    """
    Per-(device, sensor) mode-switching configuration (gap 3).

    Mode switching gates A/B/C/D detection by a sensor's current mode
    (POWER_ON / STARTUP / BREAK):

        * POWER_ON — initial state. No detection. Transitions to STARTUP
          once the sensor sustains ``raw_value > startup_threshold`` for
          ``startup_duration_seconds`` (with a transient-dip grace
          window of ``startup_reset_grace_s``).
        * STARTUP — running / active state. Detection runs normally.
          Transitions to BREAK if the sensor sustains
          ``raw_value < break_threshold`` for
          ``break_duration_seconds`` continuously, at which point a
          BREAK event fires with timestamp = the FIRST below-threshold
          sample's wall time (not the moment the duration elapsed).
        * BREAK — fault state. No detection. Recovers to STARTUP under
          the same condition that took POWER_ON → STARTUP. NO new BREAK
          event fires on this recovery.

    When ``enabled=False`` (the default — matches the legacy
    ``mode_switching_enabled`` config knob), every sensor is treated as
    if always in STARTUP and detection runs unconditionally.

    Per-sensor overrides are scope-resolved by the same SENSOR → DEVICE
    → GLOBAL walk used by Type A/B/C/D. The legacy system does not have
    a per-sensor ``enabled`` flag; it's device-wide. We carry it on the
    dataclass anyway because the storage shape is identical to the
    other detector configs and the API can accept it as long as it
    makes sense at the GLOBAL scope.
    """

    enabled: bool = False
    startup_threshold: float = 100.0
    break_threshold: float = 50.0
    # Minimum duration clamps match legacy (clamped at 0.001 s, line
    # 366-367 of legacy event_detector.py). Don't reduce these without
    # a contract update — operators may have set 0.0 expecting "fire as
    # fast as possible" and we must not change that semantics.
    startup_duration_seconds: float = 0.1
    break_duration_seconds: float = 2.0
    # Grace window for transient drops below startup_threshold while
    # still in POWER_ON / BREAK waiting to transition to STARTUP. A
    # brief dip < this duration is forgiven; a sustained one resets
    # the startup timer. Hardcoded 1.0 s in legacy (line 396).
    startup_reset_grace_s: float = 1.0


@dataclass(frozen=True, slots=True)
class TypeDConfig:
    """
    Type D (two-stage averaging on avg_T5) detector configuration.

    Hierarchical baselines (legacy parity, contract §6):

        Stage 1: avg_T4 = rolling mean of raw samples over last T4 s.
        Stage 2: each elapsed wall-clock second, average the avg_T4 values
                 falling in that second; append to one_sec_averages.
        Stage 3: avg_T5 = mean of the last T5 entries of one_sec_averages.

    Fires when ``avg_T3`` (from the paired Type C detector) leaves a
    SYMMETRIC band around avg_T5:

        lower = avg_T5 - (REF_VALUE × tolerance_pct) / 100
        upper = avg_T5 + (REF_VALUE × tolerance_pct) / 100
        fire when avg_T3 < lower OR avg_T3 > upper

    Legacy quirk preserved: tolerance is single-valued (symmetric) even
    though the legacy DB stored distinct upper/lower fields. Use
    ``tolerance_pct`` for both sides.
    """

    enabled: bool = False
    T4: float = 10.0
    T5: float = 30.0
    tolerance_pct: float = 5.0
    debounce_seconds: float = 0.0
    init_fill_ratio: float = 0.9
    expected_sample_rate_hz: float = 100.0


class DetectorConfigProvider(Protocol):
    """
    Resolves per-sensor detector config.

    The engine calls the appropriate ``type_X_for`` lazily on the first
    sample for that sensor, caches the result, and creates the detector.
    ``invalidate()`` (future) will be called from the config API after
    the operator saves a new threshold.
    """

    def type_a_for(self, device_id: int, sensor_id: int) -> TypeAConfig: ...
    def type_b_for(self, device_id: int, sensor_id: int) -> TypeBConfig: ...
    def type_c_for(self, device_id: int, sensor_id: int) -> TypeCConfig: ...
    def type_d_for(self, device_id: int, sensor_id: int) -> TypeDConfig: ...
    def mode_switching_for(self, device_id: int, sensor_id: int) -> ModeSwitchingConfig: ...


class StaticConfigProvider:
    """
    Returns a single config per event type, regardless of device or sensor.

    Used in tests and as the Phase 3b/c stand-in before the DB-backed
    provider lands. Safe to share across the entire engine because the
    dataclasses are frozen.
    """

    def __init__(
        self,
        type_a: TypeAConfig | None = None,
        type_b: TypeBConfig | None = None,
        type_c: TypeCConfig | None = None,
        type_d: TypeDConfig | None = None,
        mode_switching: ModeSwitchingConfig | None = None,
    ) -> None:
        self._type_a = type_a if type_a is not None else TypeAConfig()
        self._type_b = type_b if type_b is not None else TypeBConfig()
        self._type_c = type_c if type_c is not None else TypeCConfig()
        self._type_d = type_d if type_d is not None else TypeDConfig()
        self._mode_switching = (
            mode_switching if mode_switching is not None else ModeSwitchingConfig()
        )

    def type_a_for(self, device_id: int, sensor_id: int) -> TypeAConfig:
        del device_id, sensor_id
        return self._type_a

    def type_b_for(self, device_id: int, sensor_id: int) -> TypeBConfig:
        del device_id, sensor_id
        return self._type_b

    def type_c_for(self, device_id: int, sensor_id: int) -> TypeCConfig:
        del device_id, sensor_id
        return self._type_c

    def type_d_for(self, device_id: int, sensor_id: int) -> TypeDConfig:
        del device_id, sensor_id
        return self._type_d

    def mode_switching_for(self, device_id: int, sensor_id: int) -> ModeSwitchingConfig:
        del device_id, sensor_id
        return self._mode_switching
