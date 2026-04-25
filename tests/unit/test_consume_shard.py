"""
Unit tests for Layer 3 shard filtering and live_only mode in ``_consume``.

These tests pre-fill the asyncio handoff queue with synthetic STM32
payloads spread across 20 device_ids and run ``_consume`` against an
in-memory live buffer. After draining we inspect ``LiveDataHub.devices()``
to verify only the expected slice of devices landed in this shard's
buffers.

The shard math is intentionally identity-preserving: every device_id
hashes to exactly one shard via ``device_id % shard_count``, so the
union of all shards' device sets equals the full set, with no overlap.
"""

from __future__ import annotations

import asyncio
import time

import orjson
import pytest

from hermes.config import get_settings
from hermes.detection.config import StaticConfigProvider, TypeAConfig
from hermes.detection.engine import DetectionEngine
from hermes.detection.sink import LoggingEventSink
from hermes.detection.window_buffer import EventWindowBuffer
from hermes.ingest.clock import ClockRegistry
from hermes.ingest.live_data import LiveDataHub
from hermes.ingest.main import _consume
from hermes.ingest.offsets import OffsetCache

DEVICES = list(range(1, 21))  # 20 devices, IDs 1..20


def _payload(device_id: int) -> bytes:
    return orjson.dumps(
        {
            "device_id": device_id,
            "ts": int(time.time() * 1000),
            "adc1": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
            "adc2": [20.0, 21.0, 22.0, 23.0, 24.0, 25.0],
        }
    )


async def _drain(
    queue: asyncio.Queue[tuple[bytes, float]],
    *,
    detection: DetectionEngine | None,
    shard_count: int,
    shard_index: int,
) -> LiveDataHub:
    settings = get_settings()
    clocks = ClockRegistry(drift_threshold_s=settings.mqtt_drift_threshold_s)
    offsets = OffsetCache()
    live = LiveDataHub(maxlen=settings.live_buffer_max_samples)
    window = EventWindowBuffer()
    stop_event = asyncio.Event()

    consumer = asyncio.create_task(
        _consume(
            queue,
            clocks,
            offsets,
            live,
            window,
            detection,
            stop_event,
            shard_count=shard_count,
            shard_index=shard_index,
        )
    )
    while not queue.empty():  # noqa: ASYNC110 — bounded test drain
        await asyncio.sleep(0.01)
    stop_event.set()
    await consumer
    return live


def _make_engine() -> DetectionEngine:
    return DetectionEngine(
        StaticConfigProvider(TypeAConfig(enabled=False)),
        LoggingEventSink(),
    )


@pytest.mark.asyncio
async def test_default_single_shard_accepts_all_devices() -> None:
    """``shard_count=1`` is the alpha.13 behaviour — every device lands."""
    queue: asyncio.Queue[tuple[bytes, float]] = asyncio.Queue()
    for d in DEVICES:
        queue.put_nowait((_payload(d), time.time()))

    live = await _drain(queue, detection=_make_engine(), shard_count=1, shard_index=0)
    assert sorted(live.devices()) == DEVICES


@pytest.mark.asyncio
async def test_shard_zero_of_four_accepts_devices_with_id_mod_four_eq_zero() -> None:
    """4-shard split, index 0 owns devices where id % 4 == 0 (4, 8, 12, 16, 20)."""
    queue: asyncio.Queue[tuple[bytes, float]] = asyncio.Queue()
    for d in DEVICES:
        queue.put_nowait((_payload(d), time.time()))

    live = await _drain(queue, detection=_make_engine(), shard_count=4, shard_index=0)
    assert sorted(live.devices()) == [4, 8, 12, 16, 20]


@pytest.mark.asyncio
async def test_shard_two_of_four_accepts_devices_with_id_mod_four_eq_two() -> None:
    queue: asyncio.Queue[tuple[bytes, float]] = asyncio.Queue()
    for d in DEVICES:
        queue.put_nowait((_payload(d), time.time()))

    live = await _drain(queue, detection=_make_engine(), shard_count=4, shard_index=2)
    assert sorted(live.devices()) == [2, 6, 10, 14, 18]


@pytest.mark.asyncio
async def test_all_shards_combined_cover_all_devices_with_no_overlap() -> None:
    """Union of shard outputs == full device set; intersection == empty."""
    seen_total: set[int] = set()
    seen_per_shard: list[set[int]] = []
    for shard_index in range(4):
        queue: asyncio.Queue[tuple[bytes, float]] = asyncio.Queue()
        for d in DEVICES:
            queue.put_nowait((_payload(d), time.time()))
        live = await _drain(
            queue,
            detection=_make_engine(),
            shard_count=4,
            shard_index=shard_index,
        )
        devices = set(live.devices())
        seen_per_shard.append(devices)
        # Each shard must be disjoint from every prior shard.
        assert not (devices & seen_total), f"shard {shard_index} overlaps prior"
        seen_total |= devices

    assert seen_total == set(DEVICES)
    # Each shard owns roughly DEVICES // 4 devices.
    for s in seen_per_shard:
        assert len(s) == 5


@pytest.mark.asyncio
async def test_live_only_mode_fills_live_buffer_without_detection() -> None:
    """``detection=None`` mirrors the API process in multi-shard mode."""
    queue: asyncio.Queue[tuple[bytes, float]] = asyncio.Queue()
    for d in DEVICES:
        queue.put_nowait((_payload(d), time.time()))

    live = await _drain(queue, detection=None, shard_count=1, shard_index=0)
    # Live buffer fills normally — SSE keeps working.
    assert sorted(live.devices()) == DEVICES
    # And we didn't crash trying to call detect_feed() on None.
