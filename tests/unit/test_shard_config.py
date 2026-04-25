"""
Unit tests for the Layer 3 shard config validation in ``Settings``.

Covers the model_validator that enforces shard-math invariants:
    * shard_count >= 1
    * 0 <= shard_index < shard_count
    * mode='shard' implies shard_count > 1

These checks live in ``Settings._validate_shard_config`` and run at
process start so a misconfigured deployment fails fast rather than
silently routing every device through one process labelled "shard 0
of 1".
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from hermes.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Each test sets its own env vars; clear the cached singleton."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _build(env: dict[str, str]) -> Settings:
    """Build a Settings via the env-var path (matches production)."""
    # Settings reads from os.environ; conftest provides the required
    # baseline (DATABASE_URL, JWT_SECRET, etc), we just layer on shard
    # specifics for each case.
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return Settings()  # type: ignore[call-arg]
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_default_is_single_process_all_mode() -> None:
    """The zero-config default behaves like alpha.13: single process."""
    settings = _build({})
    assert settings.hermes_ingest_mode == "all"
    assert settings.hermes_shard_count == 1
    assert settings.hermes_shard_index == 0


def test_valid_multi_shard_config_accepted() -> None:
    settings = _build(
        {
            "HERMES_INGEST_MODE": "shard",
            "HERMES_SHARD_COUNT": "4",
            "HERMES_SHARD_INDEX": "2",
        }
    )
    assert settings.hermes_shard_count == 4
    assert settings.hermes_shard_index == 2


def test_live_only_mode_accepts_shard_count_one() -> None:
    """The API process runs as live_only with shard_count=1 — valid."""
    settings = _build(
        {
            "HERMES_INGEST_MODE": "live_only",
            "HERMES_SHARD_COUNT": "1",
            "HERMES_SHARD_INDEX": "0",
        }
    )
    assert settings.hermes_ingest_mode == "live_only"


def test_shard_count_zero_rejected() -> None:
    with pytest.raises(ValueError, match="shard_count"):
        _build({"HERMES_SHARD_COUNT": "0"})


def test_shard_index_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="shard_index"):
        _build({"HERMES_SHARD_COUNT": "4", "HERMES_SHARD_INDEX": "4"})


def test_shard_mode_with_count_one_rejected() -> None:
    """A single 'shard' is a misconfig — operator probably forgot count>1."""
    with pytest.raises(ValueError, match="shard_count > 1"):
        _build(
            {
                "HERMES_INGEST_MODE": "shard",
                "HERMES_SHARD_COUNT": "1",
                "HERMES_SHARD_INDEX": "0",
            }
        )


def test_negative_shard_index_rejected() -> None:
    with pytest.raises(ValueError, match="shard_index"):
        _build({"HERMES_SHARD_COUNT": "4", "HERMES_SHARD_INDEX": "-1"})
