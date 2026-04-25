"""
DbConfigProvider scope-walk invariants.

Doesn't need a database — we construct the provider and inject cached
state directly (the same state ``reload()`` would have built from
``parameters`` rows). The walk is pure Python.
"""

from __future__ import annotations

import uuid

from hermes.detection.config import TypeAConfig, TypeBConfig, TypeCConfig, TypeDConfig
from hermes.detection.db_config import DbConfigProvider, _ConfigCache


def _cache(threshold_cv: float = 5.0) -> _ConfigCache:
    """Build a _ConfigCache where Type A's threshold_cv distinguishes it."""
    return _ConfigCache(
        type_a=TypeAConfig(threshold_cv=threshold_cv),
        type_b=TypeBConfig(),
        type_c=TypeCConfig(),
        type_d=TypeDConfig(),
    )


def _make_provider() -> DbConfigProvider:
    return DbConfigProvider(uuid.uuid4())


def test_unconfigured_provider_returns_global_defaults() -> None:
    p = _make_provider()
    cfg = p.type_a_for(device_id=1, sensor_id=1)
    assert cfg.threshold_cv == 5.0  # dataclass default


def test_global_only_is_used_when_no_overrides() -> None:
    p = _make_provider()
    p._global = _cache(threshold_cv=11.0)
    assert p.type_a_for(1, 1).threshold_cv == 11.0
    assert p.type_a_for(2, 5).threshold_cv == 11.0


def test_device_override_replaces_global_for_that_device() -> None:
    p = _make_provider()
    p._global = _cache(threshold_cv=11.0)
    p._devices = {1: _cache(threshold_cv=22.0)}
    assert p.type_a_for(1, 3).threshold_cv == 22.0
    assert p.type_a_for(2, 3).threshold_cv == 11.0  # other device → global


def test_sensor_override_takes_precedence_over_device() -> None:
    p = _make_provider()
    p._global = _cache(threshold_cv=11.0)
    p._devices = {1: _cache(threshold_cv=22.0)}
    p._sensors = {(1, 5): _cache(threshold_cv=33.0)}
    assert p.type_a_for(1, 5).threshold_cv == 33.0  # sensor wins
    assert p.type_a_for(1, 6).threshold_cv == 22.0  # device fallback
    assert p.type_a_for(2, 5).threshold_cv == 11.0  # global fallback


def test_introspection_properties_return_immutable_copies() -> None:
    """Mutating the returned dicts must not corrupt internal state."""
    p = _make_provider()
    p._devices = {1: _cache(threshold_cv=22.0)}

    snapshot = p.device_overrides
    snapshot[2] = _cache(threshold_cv=999.0)
    assert 2 not in p._devices  # internal cache untouched
