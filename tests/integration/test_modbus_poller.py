"""
Integration tests for ``ModbusPoller`` and ``ModbusManager`` (gap 7).

These spin up a real pymodbus async server in-process on a free port,
seed its input registers with known values, then verify:

  * One poll cycle reads + decodes + invokes the callback.
  * The callback receives the expected ``{sensor_id: value}`` shape.
  * Scaling is applied correctly.
  * ``ModbusManager`` discovers a Modbus device in the ``devices``
    table, spawns a poller, and the poller produces snapshots.

The DB-touching ``ModbusManager`` test runs under the ``db`` marker so
it requires Postgres. The poller-only tests don't need a DB and run
in the unit tier... but it's cleanest to keep them all here under
``db`` since we already have the alt-port Postgres up and the
simulator setup is non-trivial.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from hermes.db.engine import async_session
from hermes.db.models import Device, DeviceProtocol
from hermes.ingest.modbus import ModbusConfig, ModbusManager, ModbusPoller

# ─── Test fixtures ────────────────────────────────────────────────


def _free_port() -> int:
    """Bind to port 0 to get an OS-assigned free port, close, return it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest_asyncio.fixture
async def modbus_server() -> AsyncIterator[int]:
    """Start a local pymodbus server with seeded input registers.

    Uses pymodbus 3.13's new SimData/SimDevice API (the older
    ModbusServerContext path is deprecated and incompatible with
    ``ModbusTcpServer.__init__`` in this version).

    Seeds 12 input registers (datatype=REGISTERS) at address 0 with
    values 100..111. ``listen()`` is synchronous so the test starts
    polling without a sleep-and-pray wait.
    """
    sim_data = SimData(
        address=0,
        count=12,
        values=[100 + i for i in range(12)],
        datatype=DataType.REGISTERS,
    )
    # The single-SimData form makes the same data available across the
    # device's register banks. Empty lists in the tuple form are
    # rejected by pymodbus 3.13's SimDevice validator, so this is the
    # cleanest path to a one-block test fixture.
    device = SimDevice(id=1, simdata=sim_data)

    port = _free_port()
    server = ModbusTcpServer(context=device, address=("127.0.0.1", port))
    await server.listen()
    serve_task = asyncio.create_task(server.serve_forever(), name="modbus-test-server")
    try:
        yield port
    finally:
        await server.shutdown()
        serve_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await serve_task


# ─── Poller tests ────────────────────────────────────────────────


@pytest.mark.db
@pytest.mark.asyncio
async def test_poller_reads_and_decodes_one_cycle(
    modbus_server: int,
) -> None:
    """One poll cycle must produce a snapshot with all 12 sensors."""
    port = modbus_server

    received: list[tuple[int, float, dict[int, float]]] = []

    def callback(device_id: int, ts: float, values: dict[int, float]) -> None:
        received.append((device_id, ts, dict(values)))

    cfg = ModbusConfig(
        host="127.0.0.1",
        port=port,
        register_start=0,
        register_count=12,
        scaling=1.0,
        poll_interval_ms=50,
        timeout_s=2.0,
    )
    poller = ModbusPoller(device_id=42, cfg=cfg, callback=callback)
    await poller.start()
    try:
        # Wait long enough for at least 2 cycles.
        for _ in range(40):
            if len(received) >= 1:
                break
            await asyncio.sleep(0.05)
    finally:
        await poller.stop()

    assert len(received) >= 1
    device_id, _ts, values = received[0]
    assert device_id == 42
    # Sensor IDs 1..12 mapped from registers 0..11.
    assert sorted(values) == list(range(1, 13))
    # Values must match the seeded input registers (100..111) at scaling=1.
    for sid in range(1, 13):
        assert values[sid] == float(99 + sid)  # register sid-1 holds 100+(sid-1) = 99+sid


@pytest.mark.db
@pytest.mark.asyncio
async def test_scaling_divides_raw_values(
    modbus_server: int,
) -> None:
    """``scaling=10`` means raw 100 → engineering value 10.0."""
    port = modbus_server
    received: list[dict[int, float]] = []

    def callback(_d: int, _t: float, values: dict[int, float]) -> None:
        received.append(dict(values))

    cfg = ModbusConfig(
        host="127.0.0.1",
        port=port,
        register_start=0,
        register_count=12,
        scaling=10.0,
        poll_interval_ms=50,
        timeout_s=2.0,
    )
    poller = ModbusPoller(device_id=7, cfg=cfg, callback=callback)
    await poller.start()
    try:
        for _ in range(40):
            if received:
                break
            await asyncio.sleep(0.05)
    finally:
        await poller.stop()

    assert received
    values = received[0]
    # Register 0 = 100, scaling=10 → 10.0; register 11 = 111, scaling=10 → 11.1.
    assert values[1] == pytest.approx(10.0)
    assert values[12] == pytest.approx(11.1)


@pytest.mark.db
@pytest.mark.asyncio
async def test_poller_with_no_server_does_not_blow_up() -> None:
    """A connect failure logs and the loop survives until stopped."""
    received: list[object] = []

    def callback(_d: int, _t: float, _v: dict[int, float]) -> None:
        received.append(1)

    cfg = ModbusConfig(
        host="127.0.0.1",
        port=_free_port(),  # nothing listening
        register_start=0,
        poll_interval_ms=50,
        timeout_s=0.2,
    )
    poller = ModbusPoller(device_id=1, cfg=cfg, callback=callback)
    await poller.start()
    try:
        await asyncio.sleep(0.3)
    finally:
        await poller.stop()
    # No data should have arrived because the server isn't there.
    assert received == []


# ─── Manager tests ──────────────────────────────────────────────


@pytest.mark.db
@pytest.mark.asyncio
async def test_manager_discovers_modbus_device_and_polls(
    modbus_server: int,
) -> None:
    """Inserting a Modbus device into the DB must spawn a poller."""
    port = modbus_server

    # Seed a Modbus device row.
    async with async_session() as s:
        s.add(
            Device(
                device_id=22,
                name="modbus-fake",
                protocol=DeviceProtocol.MODBUS_TCP,
                modbus_config={
                    "host": "127.0.0.1",
                    "port": port,
                    "register_start": 0,
                    "register_count": 12,
                    "scaling": 1.0,
                    "poll_interval_ms": 50,
                    "timeout_s": 2.0,
                },
            )
        )
        await s.commit()

    received: list[tuple[int, dict[int, float]]] = []

    def callback(device_id: int, _ts: float, values: dict[int, float]) -> None:
        received.append((device_id, dict(values)))

    manager = ModbusManager(callback=callback, refresh_interval_s=10.0)
    await manager.start()
    try:
        assert manager.device_ids == [22]
        for _ in range(40):
            if received:
                break
            await asyncio.sleep(0.05)
    finally:
        await manager.stop()

    assert received
    device_id, values = received[0]
    assert device_id == 22
    assert values[1] == 100.0
    assert values[12] == 111.0


@pytest.mark.db
@pytest.mark.asyncio
async def test_manager_picks_up_new_device_after_refresh(
    modbus_server: int,
) -> None:
    """Devices added AFTER manager.start() are caught by the refresh loop."""
    port = modbus_server

    received: list[int] = []

    def callback(device_id: int, _ts: float, _v: dict[int, float]) -> None:
        received.append(device_id)

    manager = ModbusManager(callback=callback, refresh_interval_s=0.3)
    await manager.start()
    try:
        # Initially no Modbus devices.
        assert manager.device_ids == []

        async with async_session() as s:
            s.add(
                Device(
                    device_id=33,
                    name="late",
                    protocol=DeviceProtocol.MODBUS_TCP,
                    modbus_config={
                        "host": "127.0.0.1",
                        "port": port,
                        "register_start": 0,
                        "poll_interval_ms": 50,
                    },
                )
            )
            await s.commit()

        # Wait for the refresh loop to pick it up + a poll cycle to fire.
        for _ in range(40):
            if 33 in received:
                break
            await asyncio.sleep(0.05)
        assert 33 in received
    finally:
        await manager.stop()


@pytest.mark.db
@pytest.mark.asyncio
async def test_manager_drops_poller_when_device_disabled(
    modbus_server: int,
) -> None:
    """Setting ``is_active=False`` must tear down the poller on the next refresh."""
    port = modbus_server

    async with async_session() as s:
        s.add(
            Device(
                device_id=44,
                name="bye",
                protocol=DeviceProtocol.MODBUS_TCP,
                modbus_config={
                    "host": "127.0.0.1",
                    "port": port,
                    "register_start": 0,
                    "poll_interval_ms": 50,
                },
            )
        )
        await s.commit()

    manager = ModbusManager(callback=lambda *_: None, refresh_interval_s=0.3)
    await manager.start()
    try:
        assert manager.device_ids == [44]

        # Disable the device.
        async with async_session() as s:
            row = await s.get(Device, 44)
            assert row is not None
            row.is_active = False
            await s.commit()

        # Wait for refresh to drop it.
        for _ in range(20):
            if not manager.device_ids:
                break
            await asyncio.sleep(0.1)
        assert manager.device_ids == []
    finally:
        await manager.stop()
