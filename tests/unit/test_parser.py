"""
parse_stm32_adc_payload invariants.

These tests lock in the byte-for-byte input / output contract with the
legacy parser so golden-traffic replay can diff new vs. old without
false positives.
"""

from __future__ import annotations

from hermes.ingest.parser import parse_stm32_adc_payload


def test_full_payload_produces_twelve_sensors() -> None:
    payload = {"adc1": [1, 2, 3, 4, 5, 6], "adc2": [7, 8, 9, 10, 11, 12]}
    result = parse_stm32_adc_payload(payload)
    assert result == {i: float(i) for i in range(1, 13)}


def test_values_are_coerced_to_float() -> None:
    result = parse_stm32_adc_payload({"adc1": [42], "adc2": []})
    assert result == {1: 42.0}
    assert isinstance(result[1], float)


def test_missing_arrays_are_dropped_silently() -> None:
    # No adc1 / adc2 at all → empty dict. Caller treats empty as a no-op.
    assert parse_stm32_adc_payload({}) == {}
    assert parse_stm32_adc_payload({"device_id": 1}) == {}


def test_extra_elements_beyond_six_are_truncated() -> None:
    # Firmware is fixed at 6+6; anything beyond is a protocol error we
    # quietly drop rather than crash on.
    payload = {"adc1": [1, 2, 3, 4, 5, 6, 999, 1000], "adc2": []}
    assert parse_stm32_adc_payload(payload) == {1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0, 5: 5.0, 6: 6.0}


def test_partial_arrays_partially_populate_sensors() -> None:
    # Hardware can send fewer than 6 values per bank if a channel misfires.
    payload = {"adc1": [10, 20], "adc2": [30]}
    assert parse_stm32_adc_payload(payload) == {1: 10.0, 2: 20.0, 7: 30.0}


def test_sensor_numbering_is_1_indexed_and_gap_free() -> None:
    # adc1 covers 1-6, adc2 covers 7-12. There is no sensor 0.
    result = parse_stm32_adc_payload({"adc1": [0, 0, 0, 0, 0, 0], "adc2": [0, 0, 0, 0, 0, 0]})
    assert sorted(result.keys()) == list(range(1, 13))
