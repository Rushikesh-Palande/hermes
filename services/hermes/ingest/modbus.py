"""
Modbus TCP polling support — gap 7.

HERMES is MQTT-first; STM32 firmware publishes 12-channel ADC over
MQTT and that's the production path. But customers with legacy PLCs
sometimes need a Modbus-TCP option, and the schema has carried
``DeviceProtocol.MODBUS_TCP`` + a ``modbus_config`` JSONB since
alpha.5. This module finally activates it.

Architecture:

    ModbusManager (one per process)
        watches the ``devices`` table for protocol=modbus_tcp + active
        spawns / cancels one ModbusPoller task per such device
        refreshes its set every 5 s

    ModbusPoller (one per Modbus device)
        owns a pymodbus AsyncModbusTcpClient
        polls ``register_count`` registers starting at ``register_start``
        every ``poll_interval_ms``
        decodes each 16-bit word to a float via the ``scaling`` factor
        builds a ``{sensor_id: value}`` snapshot for sensor_id in 1..count
        invokes the downstream callback (same shape as MQTT path):
              callback(device_id, ts, sensor_values)

The downstream callback is the existing ``_process_snapshot`` helper
in ``hermes.ingest.main``, so Modbus-sourced data flows through the
exact same path as MQTT-sourced data: clock anchoring is skipped
(local poll time IS the wall clock), offsets apply, live ring buffer
fills, window buffer fills, detection runs, and the session-sample
writer archives.

Why pymodbus async (not the sync API):
    The whole pipeline lives on one asyncio event loop. A blocking
    Modbus read would freeze the MQTT consumer + SSE + DB writers for
    the duration of the timeout. AsyncModbusTcpClient hands off socket
    I/O to the loop, costing nothing while idle and only the read
    duration when active.

Failure modes:
    * Connect failure: poller logs and retries on the next interval.
      Devices that never connect just consume one log line every poll.
    * Read failure (timeout, exception result): logged, snapshot
      skipped, retry on next interval.
    * Decode mismatch (wrong register count): logged, snapshot skipped.

The poller never raises into the manager loop; one bad device
doesn't take down the others.

Out of scope (deliberate, follow-ups can land independently):
    * 32-bit float / int register types. The legacy code reads 12
      uint16 registers; we match that. A future ``register_layout``
      field on ``ModbusConfig`` can extend it.
    * Modbus RTU (serial). TCP only.
    * Read retries within a single poll cycle. The legacy did 3
      retries; we let the next poll be the retry, which is simpler
      and has the same long-run outcome.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field
from pymodbus.client import AsyncModbusTcpClient
from sqlalchemy import select

from hermes import metrics as _m
from hermes.db.engine import async_session
from hermes.db.models import Device, DeviceProtocol
from hermes.logging import get_logger

_log = get_logger(__name__, component="modbus")

SnapshotCallback = Callable[[int, float, dict[int, float]], None]


# ─── Config schema (validates the modbus_config JSONB) ────────────


class ModbusConfig(BaseModel):
    """Operator-configured Modbus TCP polling parameters.

    Stored in ``devices.modbus_config`` JSONB. The API layer validates
    this when the operator creates a Modbus device; runtime parsing
    here is a defence-in-depth check (an out-of-band UPDATE could
    write malformed JSON).
    """

    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(default=502, ge=1, le=65535)
    unit_id: int = Field(default=1, ge=0, le=247, description="Modbus slave id")
    register_start: int = Field(..., ge=0, le=65535)
    register_count: int = Field(default=12, ge=1, le=12)
    scaling: float = Field(
        default=1.0,
        gt=0.0,
        description="Engineering value = raw_uint16 / scaling. 1.0 means raw value.",
    )
    # Production legacy default was 100 ms (10 Hz). At 100 Hz the Modbus
    # device would need to keep up on the wire; 100 ms is the safe
    # default for typical PLCs.
    poll_interval_ms: int = Field(default=100, ge=10, le=60_000)
    timeout_s: float = Field(default=1.0, gt=0.0, le=30.0)


def parse_modbus_config(raw: dict[str, Any] | None) -> ModbusConfig | None:
    """Validate a JSONB blob; return None on invalid (with a warn log)."""
    if raw is None:
        return None
    try:
        return ModbusConfig.model_validate(raw)
    except Exception:
        _log.warning("modbus_config_invalid", raw_keys=sorted(raw.keys()))
        return None


# ─── Per-device poller ────────────────────────────────────────────


class ModbusPoller:
    """One pymodbus client + a polling task for a single device.

    ``ModbusManager`` owns the lifecycle. Don't construct directly
    outside that path or tests.
    """

    __slots__ = (
        "_device_id",
        "_cfg",
        "_callback",
        "_client",
        "_task",
        "_stop_event",
    )

    def __init__(
        self,
        *,
        device_id: int,
        cfg: ModbusConfig,
        callback: SnapshotCallback,
    ) -> None:
        self._device_id = device_id
        self._cfg = cfg
        self._callback = callback
        self._client: AsyncModbusTcpClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    @property
    def device_id(self) -> int:
        return self._device_id

    @property
    def config(self) -> ModbusConfig:
        return self._cfg

    async def start(self) -> None:
        """Open the TCP client and spawn the poll task."""
        if self._task is not None:
            return
        self._client = AsyncModbusTcpClient(
            host=self._cfg.host,
            port=self._cfg.port,
            timeout=self._cfg.timeout_s,
        )
        # Attempting connect synchronously here so a fast-failing
        # device fails the start cycle visibly. The poll loop will
        # retry on every cycle if this fails; we just log and continue.
        try:
            await self._client.connect()
        except Exception:
            _log.exception(
                "modbus_initial_connect_failed",
                device_id=self._device_id,
                host=self._cfg.host,
            )
        self._task = asyncio.create_task(
            self._poll_loop(),
            name=f"modbus-poll-{self._device_id}",
        )
        _m.MODBUS_POLLERS_ACTIVE.inc()
        _log.info(
            "modbus_poller_started",
            device_id=self._device_id,
            host=self._cfg.host,
            port=self._cfg.port,
            interval_ms=self._cfg.poll_interval_ms,
        )

    async def stop(self) -> None:
        """Cancel the task and close the client."""
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None
        _m.MODBUS_POLLERS_ACTIVE.dec()
        _log.info("modbus_poller_stopped", device_id=self._device_id)

    async def _poll_loop(self) -> None:
        """Poll the device at the configured interval until stopped."""
        interval_s = self._cfg.poll_interval_ms / 1000.0
        client = self._client
        callback = self._callback
        device_id = self._device_id
        register_start = self._cfg.register_start
        register_count = self._cfg.register_count
        scaling = self._cfg.scaling
        unit_id = self._cfg.unit_id

        while not self._stop_event.is_set():
            start = time.perf_counter()

            if client is None:
                # Shouldn't happen — start() always builds a client —
                # but guard explicitly so a None doesn't blow up the loop.
                break

            # Reconnect on the fly if the client dropped. pymodbus's
            # ``connected`` is a property; reconnecting is cheap when
            # already connected.
            if not client.connected:
                try:
                    await client.connect()
                except Exception:
                    _log.warning(
                        "modbus_reconnect_failed",
                        device_id=device_id,
                        host=self._cfg.host,
                    )
                    _m.MODBUS_READS_FAILED_TOTAL.labels(device_id=str(device_id)).inc()
                    await self._sleep_remaining(start, interval_s)
                    continue

            try:
                response = await client.read_input_registers(
                    address=register_start,
                    count=register_count,
                    device_id=unit_id,
                )
            except Exception:
                _log.warning(
                    "modbus_read_exception",
                    device_id=device_id,
                    address=register_start,
                )
                _m.MODBUS_READS_FAILED_TOTAL.labels(device_id=str(device_id)).inc()
                await self._sleep_remaining(start, interval_s)
                continue

            if response.isError() or not hasattr(response, "registers"):
                _log.warning(
                    "modbus_read_error_response",
                    device_id=device_id,
                    address=register_start,
                )
                _m.MODBUS_READS_FAILED_TOTAL.labels(device_id=str(device_id)).inc()
                await self._sleep_remaining(start, interval_s)
                continue

            registers: list[int] = list(response.registers)
            if len(registers) != register_count:
                _log.warning(
                    "modbus_unexpected_register_count",
                    device_id=device_id,
                    expected=register_count,
                    got=len(registers),
                )
                _m.MODBUS_READS_FAILED_TOTAL.labels(device_id=str(device_id)).inc()
                await self._sleep_remaining(start, interval_s)
                continue

            sensor_values: dict[int, float] = {
                idx + 1: float(raw) / scaling for idx, raw in enumerate(registers)
            }
            ts = time.time()
            try:
                callback(device_id, ts, sensor_values)
            except Exception:
                # The downstream is the same hot path MQTT uses; if it
                # raises we still want to keep polling. Log and move on.
                _log.exception(
                    "modbus_callback_failed",
                    device_id=device_id,
                )

            _m.MODBUS_READS_OK_TOTAL.labels(device_id=str(device_id)).inc()
            await self._sleep_remaining(start, interval_s)

    async def _sleep_remaining(self, started_at: float, interval_s: float) -> None:
        """Sleep so the next iteration starts ``interval_s`` after the last one.

        If the poll itself overran the interval, skip the sleep —
        better to fall behind a little than to drift further. Bounded
        by ``stop_event`` for prompt cancellation.
        """
        elapsed = time.perf_counter() - started_at
        remaining = max(0.0, interval_s - elapsed)
        if remaining <= 0.0:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=remaining)


# ─── Manager ──────────────────────────────────────────────────────


class ModbusManager:
    """Discovers Modbus devices in the DB and runs one poller per device.

    Refreshes the device set every ``refresh_interval_s`` (default 5 s)
    so devices added / removed / disabled via /api/devices come into
    effect within one cycle. The discovery query is bounded to active
    Modbus devices, so the cost is one tiny SELECT every 5 s — even
    on a busy DB this is invisible.
    """

    __slots__ = (
        "_callback",
        "_refresh_interval_s",
        "_pollers",
        "_refresh_task",
        "_stop_event",
    )

    def __init__(
        self,
        *,
        callback: SnapshotCallback,
        refresh_interval_s: float = 5.0,
    ) -> None:
        self._callback = callback
        self._refresh_interval_s = refresh_interval_s
        self._pollers: dict[int, ModbusPoller] = {}
        self._refresh_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Initial discovery + spawn the refresh loop."""
        await self._refresh_pollers()
        self._refresh_task = asyncio.create_task(
            self._refresh_loop(), name="modbus-manager-refresh"
        )
        _log.info("modbus_manager_started", poller_count=len(self._pollers))

    async def stop(self) -> None:
        """Cancel the refresh loop and stop every poller."""
        self._stop_event.set()
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._refresh_task
            self._refresh_task = None
        # Stop pollers in parallel so a hung TCP close doesn't
        # serialise everything else.
        await asyncio.gather(
            *(poller.stop() for poller in self._pollers.values()),
            return_exceptions=True,
        )
        self._pollers.clear()
        _log.info("modbus_manager_stopped")

    @property
    def device_ids(self) -> list[int]:
        return sorted(self._pollers.keys())

    async def _refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._refresh_interval_s)
            try:
                await self._refresh_pollers()
            except Exception:
                _log.exception("modbus_manager_refresh_failed")

    async def _refresh_pollers(self) -> None:
        """Reconcile in-memory poller set with the DB."""
        async with async_session() as session:
            rows = (
                (
                    await session.execute(
                        select(Device).where(
                            Device.protocol == DeviceProtocol.MODBUS_TCP,
                            Device.is_active.is_(True),
                        )
                    )
                )
                .scalars()
                .all()
            )

        wanted: dict[int, ModbusConfig] = {}
        for row in rows:
            cfg = parse_modbus_config(row.modbus_config)
            if cfg is None:
                continue
            wanted[row.device_id] = cfg

        # Tear down pollers no longer wanted (device disabled, deleted,
        # or had its config invalidated).
        to_drop = [d for d in self._pollers if d not in wanted]
        for device_id in to_drop:
            poller = self._pollers.pop(device_id)
            await poller.stop()

        # Bring up new pollers.
        for device_id, cfg in wanted.items():
            existing = self._pollers.get(device_id)
            if existing is not None and existing.config == cfg:
                continue  # unchanged
            if existing is not None:
                # Config changed — restart with new params.
                await existing.stop()
                self._pollers.pop(device_id, None)
            poller = ModbusPoller(device_id=device_id, cfg=cfg, callback=self._callback)
            self._pollers[device_id] = poller
            await poller.start()
