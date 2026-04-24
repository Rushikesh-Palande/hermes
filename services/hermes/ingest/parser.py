"""
Parse raw MQTT payloads from STM32 ADC firmware into typed sensor dicts.

The STM32 publishes on `stm32/adc` (configurable) at ~123 Hz per device.
Each message carries two 6-element arrays (adc1 / adc2) covering all 12
channels, plus optional device identity and a millisecond-resolution
device-side timestamp.

This module has zero I/O and zero side effects — safe to call in tests
without any infrastructure.
"""

from __future__ import annotations

from typing import Any

# Number of sensors in each ADC group; firmware is fixed at 6+6=12 total.
_ADC_GROUP_SIZE = 6


def parse_stm32_adc_payload(payload: dict[str, Any]) -> dict[int, float]:
    """
    Map a raw STM32 MQTT message to ``{sensor_id: value}`` (1-indexed).

    Input contract (fields beyond these are silently ignored):
        - ``adc1``: list of up to 6 numeric values (sensors 1-6)
        - ``adc2``: list of up to 6 numeric values (sensors 7-12)
        - ``device_id``: int (optional, default 1 — handled by caller)
        - ``ts``: int milliseconds (optional, default None — handled by caller)

    Returns an empty dict when both adc arrays are absent or empty — the
    caller must treat empty output as a drop (no-op, no detector feed).

    Ported from legacy ``src/mqtt/parser.py``; signature preserved verbatim
    so golden-traffic replay can call either implementation and diff outputs.
    """
    sensor_values: dict[int, float] = {}

    # Slice to exactly _ADC_GROUP_SIZE so extra elements are dropped
    # rather than silently extending the sensor range.
    adc1: list[Any] = payload.get("adc1", [])[:_ADC_GROUP_SIZE]
    adc2: list[Any] = payload.get("adc2", [])[:_ADC_GROUP_SIZE]

    for i, raw in enumerate(adc1, start=1):
        sensor_values[i] = float(raw)
    for i, raw in enumerate(adc2, start=7):
        sensor_values[i] = float(raw)

    return sensor_values
