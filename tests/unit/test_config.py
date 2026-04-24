"""
Settings loading smoke tests.

Keeps the env-var contract honest: a PR that drops or renames a setting
without a migration path will break these tests loudly.
"""

from __future__ import annotations

from hermes.config import Settings, get_settings


def test_settings_load_from_env_defaults() -> None:
    """With the conftest default env vars, Settings should construct cleanly."""
    get_settings.cache_clear()
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.hermes_api_port == 8080
    assert settings.mqtt_topic_adc == "stm32/adc"


def test_jwt_secret_is_at_least_32_chars() -> None:
    """Short secrets must be rejected — catch-all for misconfigured envs."""
    get_settings.cache_clear()
    settings = get_settings()
    assert len(settings.hermes_jwt_secret.get_secret_value()) >= 32


def test_settings_cache_is_stable() -> None:
    """Repeated `get_settings()` returns the same instance (lru_cache)."""
    get_settings.cache_clear()
    a = get_settings()
    b = get_settings()
    assert a is b
