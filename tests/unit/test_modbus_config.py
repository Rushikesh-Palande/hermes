"""
Unit tests for ``ModbusConfig`` (gap 7) — config validation only.

Polling itself needs an async loop + a Modbus simulator and lives in
the integration tier.
"""

from __future__ import annotations

import pytest

from hermes.ingest.modbus import ModbusConfig, parse_modbus_config


def test_minimal_valid_config_uses_documented_defaults() -> None:
    cfg = ModbusConfig.model_validate({"host": "10.0.0.1", "register_start": 100})
    assert cfg.host == "10.0.0.1"
    assert cfg.port == 502
    assert cfg.unit_id == 1
    assert cfg.register_start == 100
    assert cfg.register_count == 12
    assert cfg.scaling == 1.0
    assert cfg.poll_interval_ms == 100
    assert cfg.timeout_s == 1.0


def test_full_config_round_trips() -> None:
    cfg = ModbusConfig.model_validate(
        {
            "host": "plc.example.com",
            "port": 1502,
            "unit_id": 5,
            "register_start": 0,
            "register_count": 8,
            "scaling": 100.0,
            "poll_interval_ms": 250,
            "timeout_s": 2.5,
        }
    )
    assert cfg.unit_id == 5
    assert cfg.register_count == 8
    assert cfg.scaling == 100.0
    assert cfg.poll_interval_ms == 250


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", ""),
        ("port", 0),
        ("port", 70_000),
        ("unit_id", -1),
        ("unit_id", 256),
        ("register_count", 0),
        ("register_count", 13),
        ("scaling", 0.0),
        ("scaling", -1.0),
        ("poll_interval_ms", 5),
        ("poll_interval_ms", 60_001),
        ("timeout_s", 0.0),
        ("timeout_s", 31.0),
    ],
)
def test_invalid_values_rejected(field: str, value: object) -> None:
    payload = {"host": "h", "register_start": 0, field: value}
    # Some fields override defaults that valid baselines need; merge above.
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
        ModbusConfig.model_validate(payload)


def test_parse_modbus_config_returns_none_for_none_input() -> None:
    assert parse_modbus_config(None) is None


def test_parse_modbus_config_returns_none_for_invalid() -> None:
    """Malformed JSONB stays out of the manager (defence in depth)."""
    bad = {"host": "h"}  # missing register_start
    assert parse_modbus_config(bad) is None


def test_parse_modbus_config_returns_model_on_valid() -> None:
    cfg = parse_modbus_config({"host": "h", "register_start": 10})
    assert isinstance(cfg, ModbusConfig)
    assert cfg.register_start == 10
