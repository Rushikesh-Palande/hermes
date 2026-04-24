# Legacy Modbus TCP Subsystem

**Status:** Legacy / Optional  
**Priority for Rewrite:** PRESERVE WITH SHIM (low urgency)  
**Target Audience:** Phase 0.5 behavior-contract capture for HERMES v2 dashboard rewrite

---

## Executive Summary

The Hammer codebase includes a complete Modbus TCP polling implementation (`src/modbus/modbus_tcp_device.py`) that was designed to support generic PLC and industrial device integration. It is fully functional but **not used by default** — the system prefers MQTT from STM32. The Modbus subsystem is read-only, supports up to 12 sensors per device, and integrates into the same event-detection pipeline as MQTT.

**Migration recommendation:** PRESERVE as an optional shim for Phase 0.5. Do not drop. It is isolated, well-tested, and useful for customers with legacy PLCs.

---

## 1. High-Level Data Flow

```
Device Creation
    ↓
[UI: device_detail.html selects "Modbus TCP/IP"]
    ↓
POST /api/devices (device_name, ip_address, port, slave_id, register_start, scaling, sample_rate_hz)
    ↓
DeviceManager.create_device() → devices table (device_id, device_name, topic=NULL, is_active=1)
    ↓
User clicks "Start" → EventDetector spawned for device_id
    ↓
[Backend: ModbusTCPDevice instantiated via Device factory in app startup]
    ↓
open_device() [creates ModbusTcpClient, configures parameters]
    ↓
init_channel(0) [attempts client.connect()]
    ↓
start_channel(0) [spawns _poll_worker() background thread]
    ↓
_poll_worker() [loop]
  ├─ _read_sensor_registers() [reads 12× input registers (16-bit)]
  ├─ _create_frame_info() [frames as sensor_values dict]
  ├─ _receive_buffer.append() [thread-safe deque]
  ├─ sleep to target poll interval [drift compensation]
  └─ (every 100 polls: log timing diagnostics)
    ↓
EventDetector.add_frame_data() [receives frames via receive_frames()]
    ↓
add_sensor_data(sensor_values, timestamp) [same path as MQTT]
    ↓
Event Detection + Snapshot Queuing [shared with MQTT]
    ↓
Plots + Worker Commands (no device differences downstream)
```

**Key insight:** Modbus and MQTT converge at `EventDetector.add_sensor_data()`. The frame wrapping is different, but sensor data is identical.

---

## 2. ModbusTCPDevice Class

**File:** `/home/embed/hammer/src/modbus/modbus_tcp_device.py`  
**Parent:** `Device` (from `src.devices.device_manager`)  
**Dependency:** `pymodbus` 3.x (via `ModbusTcpClient`)

### Constructor & Initialization

| Method | Line | Purpose |
|--------|------|---------|
| `__init__()` | 51–91 | Initialize device state, worker thread, error tracking, stats. Creates default parameters. |
| `_create_default_parameters()` | 93–103 | Add parameters: `ip_address`, `port`, `slave_id`, `register_start`, `register_count`, `modbus_scaling`, `timeout`, `read_retries`, `sample_rate_hz`. |

### Lifecycle Methods

| Method | Line | Input | Output | Side Effects |
|--------|------|-------|--------|--------------|
| `open_device()` | 105–153 | none | `True` / `False` | Creates `ModbusTcpClient(host, port, timeout, retries)` and sets `self.is_open = True`. Handles pymodbus version compatibility (retries kwarg). |
| `close_device()` | 155–173 | none | `True` | Stops polling thread, closes client, sets `is_open = False`. |
| `init_channel(channel, baud_rate=500, mode=0)` | 193–223 | `channel` (0 or 1); `baud_rate`, `mode` ignored | `True` / `False` | Calls `client.connect()`. Records `modbus_errors` stat if exception. |
| `start_channel(channel)` | 225–249 | `channel` (0 or 1) | `True` / `False` | Spawns `_poll_worker` daemon thread; only channel 0 is used. Sets `poll_running = True`. |

### Polling & Data Acquisition

| Method | Line | Purpose |
|--------|------|---------|
| `_poll_worker()` | 281–367 | **Infinite loop** (background thread): Poll at configured Hz, handle drift compensation, exponential backoff on errors, buffer frames. Loop exits when `poll_running=False` or `_poll_stop_event` is set. |
| `_read_sensor_registers()` | 369–457 | **Core Modbus I/O:** Uses `client.read_input_registers(address=register_start, count=register_count, device_id=slave_id)`. Converts 16-bit unsigned values (0–65535) to floats via `scaling` factor. Returns `dict {1: val1, 2: val2, ..., 12: val12}` or `None` on error. Implements retry loop with 5ms backoff between attempts. |
| `_create_frame_info(sensor_values)` | 459–513 | Wraps sensor dict into frame object compatible with `EventDetector`. Enforces monotonic timestamps (detects NTP backwards jumps). Sets `source='modbus_tcp'`, `sensor_values={...}` (critical). |
| `receive_frames(channel, max_frames=100, validate_counter=False)` | 260–279 | `channel` (unused), `max_frames` | List of frames from `_receive_buffer` (thread-safe deque, max 500 frames). Pops consumed frames. |

### Configuration & Utility

| Method | Line | Purpose |
|--------|------|---------|
| `send_frame(channel, can_id, data, extended=False, use_counter=False)` | 251–258 | **Stub:** Returns `True` (Modbus is read-only). |
| `reset_stats()` | 515–532 | Zeroes all counters: `ch0_rx`, `bytes_received`, `modbus_errors`, `successful_polls`, `failed_polls`, etc. |
| `get_device_info()` | 534–548 | Returns config dict: `{device_type: 'Modbus TCP/IP', ip_address, port, slave_id, register_start, register_count, scaling, sample_rate_hz}`. |
| `_stop_poll_worker(join_timeout=5.0)` | 175–191 | Gracefully stop worker thread: set stop event, wait for join, clean up reference. Prints warnings if timeout exceeded. |

---

## 3. Polling Loop Design

**Location:** `_poll_worker()` (line 281)  
**Rate:** Configurable `sample_rate_hz` (default 100 Hz, range 1–1000 Hz)  
**Interval:** `1.0 / sample_rate_hz` seconds  
**Drift Compensation:** Uses target-time approach (accumulates time debt, resync if >100ms behind)

### Error Handling & Reconnect

| Scenario | Behavior |
|----------|----------|
| Socket closed | On next poll: `client.is_socket_open()` returns False → attempt immediate `client.connect()` |
| Modbus read fails | `_consecutive_errors++`; if ≥3 errors, exponential backoff starts (50ms → 100ms → 200ms → capped 500ms). Backoff timeout resets next poll time. |
| Timeout (pymodbus default 0.3s) | Read returns error → treated as poll failure → backoff logic applies. |
| NTP backwards jump | Detected in `_create_frame_info()`: if new timestamp ≤ last timestamp, force +1µs and count `_backwards_jumps`. |
| Register read mismatch | Logs occasional timing diagnostics every 100 polls. For Sensor 3 (register index 2), logs raw vs. scaled value. |

### Register Configuration

- **Modbus Function Code:** `0x04` (Read Input Registers)
- **Register Width:** 16-bit unsigned (0–65535)
- **Count:** `register_count` (default 12, matches 12-sensor system)
- **Start Address:** `register_start` (default 0)
- **Scaling:** `modbus_scaling` (default 100.0); formula: `sensor_value = register / scaling`
- **Retries:** `read_retries` (default 1 attempt, max 2); 5ms backoff between retries

### Statistics Tracked

```python
stats = {
    'ch0_rx': int,                    # Total successful polls
    'successful_polls': int,           # Alias for ch0_rx
    'failed_polls': int,               # Read errors / timeouts
    'modbus_errors': int,              # Exceptions + disconnects
    'valid_frames': int,               # Frames added to buffer
    'last_valid_frame_time': float,    # Unix timestamp of last good read
    'last_frames': List[Dict],         # Last 50 frame_info objects
    'rx_frames': List[Dict],           # Last 200 frame_info objects
    'bytes_received': int,             # (Not used for Modbus)
}
```

---

## 4. Simulator: `modbus_slave_simulator.py`

**File:** `/home/embed/hammer/modbus_slave_simulator.py`  
**Purpose:** Emulate a Modbus TCP slave (PLC) on localhost for testing  
**Default Port:** 5020 (avoids conflict with standard port 502)

### CLI Arguments

```bash
python3 modbus_slave_simulator.py \
  --ip 127.0.0.1 \
  --port 5020 \
  --slave-id 1 \
  --pattern {static|ramp|sine|cosine|square|random} \
  --amplitude 5000 \
  --frequency 0.5 \
  --duty-cycle 0.5
```

### Data Patterns

| Pattern | Generator | Parameters | Formula |
|---------|-----------|------------|---------|
| `static` | (none) | `initial_values: List[int]` | Register values stay constant (initialized in `.start()` or default [1000, 2000, ..., 12000]) |
| `ramp` | `_generate_ramp_pattern()` (line 316) | `max_value=10000`, `period=10.0s` | Triangle wave: 0→max→0 over period, synchronized across all 12 registers |
| `sine` | `_generate_sine_pattern()` (line 334) | `amplitude=5000`, `frequency=0.5Hz`, `offset=5000` | `value = offset + amp × sin(2π·f·t)` for all sensors |
| `cosine` | `_generate_cosine_pattern()` (line 349) | Same as sine | `value = offset + amp × cos(2π·f·t)` for all sensors |
| `square` | `_generate_square_pattern()` (line 364) | `amplitude=5000`, `frequency=0.5Hz`, `offset=5000`, `duty_cycle=0.5` | Digital HIGH/LOW: if phase < duty_cycle then offset+amp else offset−amp |
| `random` | `_generate_random_pattern()` (line 388) | `min_value=0`, `max_value=10000` | Random int per sample for each register independently |
| `custom` | `_generate_custom_pattern()` (line 397) | `update_func=Callable[[ModbusSlaveSimulator], None]` | User supplies lambda/function that modifies `self.registers` directly |

### Internal Architecture

| Component | Details |
|-----------|---------|
| **Server Thread** | `_run_server()` (line 240): Runs `StartTcpServer(context, address)` (pymodbus 3.x) on separate thread. Blocking call; runs until simulator stops. |
| **Pattern Worker** | `_pattern_worker()` (line 260): Runs at 1000 Hz (1ms updates) to generate pattern values. Updates both holding registers (FC3) and input registers (FC4) in `server_context` via `setValues()`. |
| **Datastore** | `_create_datastore()` (line 217): Creates mirrored HR/IR blocks (12 registers each + 100 padding). Syncs both FC3 and FC4 to maintain compatibility with clients that read either. |
| **Register State** | `self.registers`: List[int] of current values (thread-protected by `register_lock`). Datastore reads from this. |

### Key Methods

| Method | Line | Purpose |
|--------|------|---------|
| `start(pattern, initial_values, **kwargs)` | 80–134 | Start server thread + pattern worker. Initialize registers from `initial_values` or defaults. |
| `stop()` | 136–156 | Stop pattern worker, stop server thread, cleanup. |
| `set_register(address, value)` | 158–178 | Manually set one register (0–12 for sensors). Updates both HR and IR in datastore. |
| `set_pattern(pattern, **kwargs)` | 180–193 | Dynamically change pattern without restarting. |
| `get_registers()` | 195–198 | Return thread-safe copy of current register values. |
| `get_statistics()` | 200–213 | Return dict: {reads, writes, connections, uptime, running, pattern}. |

### Usage Example

```python
from modbus_slave_simulator import ModbusSlaveSimulator

# Spawn simulator with sine wave
sim = ModbusSlaveSimulator(ip='127.0.0.1', port=5020, slave_id=1)
sim.start(pattern='sine', amplitude=5000, frequency=0.5)

# Now connect ModbusTCPDevice to 127.0.0.1:5020
# ...test...

sim.stop()
```

---

## 5. Device Schema Integration

**Database Table:** `devices` (SQLite)

```sql
CREATE TABLE devices (
    device_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name    TEXT NOT NULL,
    is_active      INTEGER DEFAULT 1,
    topic          TEXT,  -- MQTT topic (NULL for Modbus)
    created_at     TEXT,
    updated_at     TEXT
);
```

### Protocol Distinction

There is **no explicit `protocol` column** in the schema. Instead:
- **MQTT devices:** Have non-NULL `topic` (e.g., "stm32/sensors/1")
- **Modbus devices:** Have NULL `topic`

**Device Parameters** (configuration stored separately, not shown in schema):
- Modbus devices use `parameters` list in `Device` object to store `ip_address`, `port`, `slave_id`, `register_start`, `register_count`, `modbus_scaling`, `timeout`, `read_retries`, `sample_rate_hz`
- MQTT devices have no device-level parameters (topic is the only metadata)

### Device Factory

**File:** `/home/embed/hammer/devices.py` (standalone factory, not in src/)

The application instantiates a singleton `ModbusTCPDevice` or other protocol classes as needed. **Modbus devices are NOT currently auto-instantiated by the UI** — the UI POST shows the option but the backend `/api/devices` endpoint does not spawn a Modbus device object. (See section 7 for API details.)

---

## 6. UI Integration

**File:** `/home/embed/hammer/templates/device_detail.html`

### Device Type Selector

**Line 1395:** Dropdown shows option `<option value="modbus_tcp">Modbus TCP/IP</option>` alongside MQTT (default).

### Modbus-Specific Form Fields

**Lines 1408–1451:** Hidden `<div id="modbus-tcp-fields">` contains:

| Field | HTML ID | Type | Default | Notes |
|-------|---------|------|---------|-------|
| IP Address | `modbus-ip` | text | 192.168.1.100 | Host of Modbus slave |
| Port | `modbus-port` | number | 502 | TCP port |
| Slave ID | `modbus-slave-id` | number | 1 | Range 1–247 |
| Register Start | `modbus-register-start` | number | 0 | Modbus address of first register |
| Scaling | `modbus-scaling` | number | 100 | Divisor for 16-bit values |
| Poll Interval | `modbus-poll-interval` | number | 8 | Seconds between polls (**NB:** UI shows seconds; backend uses Hz) |

### JavaScript Logic

**Lines 2394–2401:** Show/hide Modbus fields based on device type:
```javascript
const modbusFields = document.getElementById('modbus-tcp-fields');
if (deviceType === 'modbus_tcp') {
    modbusFields.style.display = 'block';
}
```

**Lines 2429–2438:** On form submit, serialize Modbus fields:
```javascript
if (deviceType === 'modbus_tcp') {
    requestBody.ip_address = document.getElementById('modbus-ip').value;
    requestBody.port = parseInt(document.getElementById('modbus-port').value);
    requestBody.slave_id = parseInt(document.getElementById('modbus-slave-id').value);
    requestBody.register_start = parseInt(document.getElementById('modbus-register-start').value);
    requestBody.modbus_scaling = parseFloat(document.getElementById('modbus-scaling').value);
    const pollIntervalMs = parseInt(document.getElementById('modbus-poll-interval').value);
    // (poll interval not explicitly sent; uses sample_rate_hz from defaults)
}
```

**Lines 2786–2787:** Load Modbus device data into form:
```javascript
if (deviceInfo && deviceInfo.device_type === 'modbus_tcp') {
    const pollInput = document.getElementById('modbus-poll-interval');
    // (populate from device config)
}
```

---

## 7. Endpoint Inventory

**File:** `/home/embed/hammer/src/app/routes/devices.py`

| Endpoint | Method | Parameters | Modbus Behavior | Notes |
|----------|--------|------------|-----------------|-------|
| `/api/devices` | GET | none | Returns all devices (Modbus + MQTT mixed). **Modbus devices have `topic=NULL`.** | Caches results for 30s. Overlays `is_active` based on last data timestamp. |
| `/api/devices` | POST | `{device_name, topic}` | Creates device in DB only. **No protocol selection.** `topic` is optional (set NULL for Modbus). | Does NOT instantiate Modbus driver. Factory is loaded separately at app startup. |
| `/api/devices/<id>` | GET | `<device_id>` | Fetches device record. | Returns schema fields only (no runtime state). |
| `/api/devices/<id>` | PUT | `{device_name, is_active, topic, ...}` | Updates allowed fields. Modbus-specific params (`ip_address`, `port`, etc.) are **stored in Device object's `parameters` list, not in API request.** | Device object must be modified directly or via device manager. |
| `/api/devices/<id>` | DELETE | `<device_id>` | Deletes device record. | Does not stop running Modbus worker threads (potential resource leak if not cleaned up elsewhere). |
| `/api/devices/<id>/initialize` | POST | none | Returns hardcoded message: **"Device uses MQTT ingestion; no connection required"** | **Bug/Limitation:** Modbus devices are not handled here. This endpoint is MQTT-only. |
| `/api/devices/<id>/start` | POST | none | Creates `EventDetector` for device if not already running. Spawns detection thread. | **Does NOT start Modbus polling.** Assumes MQTT data arrives externally. Modbus polling must be started separately (via Device object's `start_channel()` method, not exposed via API). |
| `/api/devices/<id>/stop` | POST | none | Stops `EventDetector` for device. | **Does NOT stop Modbus polling.** Worker thread continues unless explicitly closed. |
| `/api/devices/<id>/status` | GET | none | Returns `{device_id, running, topic, is_active}`. | `running` reflects EventDetector state, not Modbus driver state. |

### Gap Analysis

**The Modbus API integration is incomplete:**

1. **No protocol selection in POST:** Clients cannot specify `"protocol": "modbus_tcp"`. Topic field is the only distinguisher.
2. **No device parameters endpoint:** Clients cannot POST `ip_address`, `port`, `slave_id`, etc. These must be set via a different mechanism (direct DB or device object mutation).
3. **initialize/start/stop endpoints assume MQTT:** They do not spawn or manage Modbus driver threads.
4. **No polling status endpoint:** Clients cannot query Modbus worker state (running, last read time, error count).

---

## 8. Integration with EventDetector

**File:** `/home/embed/hammer/src/detection/event_detector.py`

### Frame Data Ingestion

**Method:** `add_frame_data(frame_list)` (not shown, but called by device polling loops)

EventDetector receives frames from **both** MQTT and Modbus:

- **MQTT:** Frames arrive via Flask route `/api/add_frame_data` (user/sensor publishes JSON)
- **Modbus:** Frames arrive via `ModbusTCPDevice.receive_frames()` (pulled by app loop)

The key bridge is `add_sensor_data(sensor_values, timestamp)` (line 970):

```python
def add_sensor_data(self, sensor_values: Dict[int, float], timestamp: float, raw_frames: List[Dict] = None):
    """
    Add sensor data from any source (MQTT or Modbus).
    sensor_values: {1: 50.0, 2: 100.5, ..., 12: 75.3}
    timestamp: Unix epoch (float)
    raw_frames: List of frame dicts (for debugging)
    """
    for sensor_id in range(1, 13):
        if sensor_id in sensor_values:
            value = sensor_values[sensor_id]
            # ... process thresholds, events, snapshots ...
```

**Frame Structure for Modbus** (from `_create_frame_info()`, line 459):

```python
frame_info = {
    'type': 'RX',
    'channel': 0,
    'id': f"0x{self.base_can_id:03X}",       # Device's base CAN ID (legacy)
    'dlc': 8,
    'data': "50.00 100.50 75.30 25.75",      # First 4 sensors (display)
    'decoded_data': "S1:50.00, S2:100.50, ..., S12:...",  # All sensors
    'sensor_values': sensor_values,           # **Critical: Dict {1: 50.0, ...}**
    'raw_data_bytes': [],
    'packet_size': 8,
    'frame_type': 'Standard',
    'timestamp': int(timestamp * 1000000),   # Microseconds
    'time_received': timestamp,               # Seconds (float)
    'seq': seq_num,
    'buffer_indices': [...],
    'device_id': self.device_id,
    'source': 'modbus_tcp'                   # **Modbus marker**
}
```

**Convergence:** The `sensor_values` dict is identical between Modbus and MQTT. Detection algorithms downstream are protocol-agnostic.

---

## 9. Migration Decision

### Assessment

| Criterion | Evaluation |
|-----------|-----------|
| **Current Usage** | Not used by default. CLAUDE.md lists as "legacy/optional". No production deployments known. |
| **Code Quality** | Well-structured, robust error handling, drift compensation, comprehensive test suite (5 test files). No obvious bugs. |
| **Feature Completeness** | Reads 12 input registers (16-bit), scales, polls, integrates into event detection. No write capability. Suitable for PLCs/sensors. |
| **Test Coverage** | 5 test files: loopback, multi-slave, 16-bit patterns, real Modbus, diagnostics. Simulator is feature-complete. |
| **Dependencies** | `pymodbus` (optional import; graceful fallback if missing). No tight coupling to core. |
| **Maintenance Burden** | Low. Isolated module, no changes needed for MQTT rewrite. |
| **API Gap** | Significant. POST endpoint does not support protocol selection. No parameter update endpoint. Initialize/start/stop endpoints are MQTT-only. |

### Recommendation: **PRESERVE** (Low Priority)

**Rationale:**

1. **Isolation:** Modbus code lives in `src/modbus/` and `ModbusTCPDevice` class. Does not interfere with MQTT refactor.
2. **Legacy Customer Value:** Some users may have Modbus PLCs (e.g., Siemens S7-1200, ABB drives). Dropping would block their use case.
3. **Integration Path Proven:** EventDetector already converges Modbus and MQTT at `add_sensor_data()`. Minimal rewrite needed.
4. **Test Infrastructure:** Simulator + 5 test files mean you can verify behavior without hardware.

**Phase 0.5 Action Items:**

1. **Document API contract:** Explicitly define POST `/api/devices` to accept optional `protocol` field (default "mqtt").
2. **Add parameter endpoint:** POST `/api/devices/<id>/parameters` to set Modbus-specific config (ip_address, port, scaling, etc.).
3. **Update initialize/start/stop:** Add protocol branching to spawn Modbus driver threads when `protocol='modbus_tcp'`.
4. **Bug Fix:** Resource cleanup in `/api/devices/<id>/delete` must stop Modbus worker (call `close_device()`).
5. **UI Completion:** Enable Modbus device creation flow (currently shows option but POST endpoint doesn't support it).

**Phase 1.0 Options (Choose One):**

- **A. Full Re-implementation:** Port Modbus client to async pattern (FastAPI/async-await) for consistency with MQTT refactor.
- **B. Shim (Recommended):** Keep existing threaded driver, wrap with API layer that abstracts protocol selection.
- **C. Drop:** Delete `src/modbus/`, device_detail.html Modbus fields, update CLAUDE.md. Only viable if no customers have Modbus PLCs.

---

## Appendix A: File Locations & Line References

| File | Purpose | Key Lines |
|------|---------|-----------|
| `/home/embed/hammer/src/modbus/modbus_tcp_device.py` | Core driver | 18–549 (ModbusTCPDevice class definition) |
| `/home/embed/hammer/src/modbus/__init__.py` | Module root | (empty) |
| `/home/embed/hammer/src/devices/modbus_tcp.py` | Symlink or copy? | Same content as modbus_tcp_device.py |
| `/home/embed/hammer/modbus_slave_simulator.py` | Simulator | 21–472 (ModbusSlaveSimulator class + main) |
| `/home/embed/hammer/templates/device_detail.html` | UI | 1395–2451 (Modbus fields + JS logic) |
| `/home/embed/hammer/src/app/routes/devices.py` | API routes | 27–201 (6 endpoints) |
| `/home/embed/hammer/src/detection/event_detector.py` | Event pipeline | 970–1057 (add_sensor_data method) |
| `/home/embed/hammer/src/database/mqtt_database.py` | Schema | 102–136 (devices table), 1380–1420 (create_device) |
| `/home/embed/hammer/CLAUDE.md` | Project docs | Line 11 (legacy note), 76–77 (CLI example), 182–183 (file tree) |

---

## Appendix B: Modbus TCP Protocol Details (For Reference)

| Aspect | Value |
|--------|-------|
| **Transport** | TCP/IP |
| **Default Port** | 502 (standard); 5020 (simulator, non-privileged) |
| **Function Codes Used** | FC 0x04 (Read Input Registers) only |
| **Data Type** | 16-bit unsigned (0–65535) |
| **Byte Order** | Big-endian (standard Modbus) |
| **Client Library** | `pymodbus` 3.x |
| **Slave ID Range** | 1–247 (as per Modbus spec) |

---

## Appendix C: EventDetector Snapshot Integration

When Modbus data arrives, EventDetector stores snapshots in the database for worker commands:

**Snapshot Path:**
1. `add_sensor_data(sensor_values={...}, timestamp=T)` → line 970
2. Worker manager queues snapshot: `(device_id, timestamp, sensor_values)` → line 1057
3. Snapshots retrieved by `/api/workers/<id>/execute` (separate subsystem)

**Modbus snapshots are indistinguishable from MQTT snapshots to downstream workers.**

---

**Document Generated:** 2026-04-23  
**Source System:** Hammer (HERMES Phase 0 dashboard)  
**Prepared for:** HERMES v2 rewrite planning
