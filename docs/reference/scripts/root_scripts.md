# HERMES Root Scripts Reference

**Location:** `/home/embed/hammer/`

**Purpose:** Complete behavior-contract catalog of all 46 Python scripts at the root level (NOT inside `src/` or `tests/`). Intended as a migration checklist for the Phase 0.5 rewrite from CAN Analyzer to HERMES sensor dashboard.

**Generated:** 2026-04-23

---

## Executive Summary

- **Total Scripts:** 46 Python files at root
- **Total Lines:** ~12,000 LOC (dominated by `web_server.py`: 3,999 lines)
- **Primary Entry Point:** `web_server.py` (Flask app, MQTT consumer, live streaming, REST API)
- **Secondary Entry Point:** `wsgi.py` (Gunicorn WSGI wrapper)
- **Main Entry Script:** `run.sh` (starts server with gunicorn or Flask dev)
- **Critical Simulators/Publishers:** `modbus_slave_simulator.py`, `mqtt_test_publisher.py`, `run_mqtt_broker.py`

### Keep / Drop / Uncertain Decision Summary

**KEEP (11 scripts):**
- `web_server.py` — PRIMARY RUNTIME
- `wsgi.py` — WSGI entry point for gunicorn
- `mqtt_test_publisher.py` — Test harness for MQTT patterns
- `modbus_slave_simulator.py` — Hardware simulator
- `mqtt_config_db.py` — MQTT config persistence
- `services.py` — Shared application state
- `devices.py` — Device API routes
- `generate_docs.py` — Documentation builder
- `start_simulator_for_testing.py` — Quick start simulator
- `monitor_live_queues.py` — API perf monitor
- `pattern_detection.py` — Pattern detection library

**DROP (18 scripts)** — Legacy / one-off migrations:
- All `check_*.py` — Diagnostics for CAN database (not MQTT)
- All `migrate_*.py` — Schema migration utilities (one-off)
- All `fix_*.py` — Bug fixes / schema corrections (one-off)
- All `add_*.py` — Column additions (one-off)
- `create_wide_events_table.py`, `custom_board_config.py`, `config_custom_board_exact.py`
- `check_204_frames.py`, `fix_type_a_schema.py`

**UNCERTAIN (17 scripts)** — Active tests / diagnostics:
- All `test_*.py` files (11 scripts) — Currently used for validation
- All `monitor_*.py` files (2 scripts) — Performance diagnostics
- All `*_analysis.py` files (3 scripts) — Burst/pattern analysis
- `run_mqtt_broker.py`, `test_login.py`, `query_events.py`, `control_s10.py`

---

## Detailed Script Catalog

### 1. CORE RUNTIME (Entry Points & Framework)

#### `/home/embed/hammer/web_server.py` (3,999 lines)
**Status:** PRIMARY ENTRY POINT — ACTIVELY USED

**Purpose:** Main Flask application for HERMES sensor dashboard. Handles MQTT ingestion, event detection, REST API, live streaming (SSE), and web UI.

**Entry Point:** `python3 web_server.py` (standalone Flask) or via `wsgi.py` + gunicorn (production)

**Key Sections (by line range):**
- **Lines 1–100:** Imports, Flask app init, MQTT topic parsing, OTP config
- **Lines 112–155:** Device caching functions
- **Lines 156–240:** `mqtt_data_consumer()` thread (real-time ingestion, timestamp anchoring via `stm32_ts_offsets`)
- **Lines 237–316:** `init_mqtt_client()` and startup initialization
- **Lines 352–429:** Flask middleware, login_required decorator
- **Lines 430–588:** `LiveDataHub` class (in-memory ringbuffer for live streaming)
- **Lines 589–720:** Config database helpers
- **Lines 727–826:** Device sensor offset loading/refresh
- **Lines 827–916:** Event detector configuration appliers
- **Lines 958–996:** STM32 device auto-creation
- **Lines 1066–1189:** Auth routes (login, OTP request/verify, logout)
- **Lines 1190–1263:** Web UI template routes (dashboard, device-config, event-config, etc.)
- **Lines 1264–1444:** MQTT config REST API
- **Lines 1446–1466:** Auto-restart config API
- **Lines 1466–2124:** Device management API (CRUD, initialize, start, stop, status)
- **Lines 2123–2301:** Stats API, event list API, event data API
- **Lines 2301–2766:** Event config API (Type A per-device and per-sensor)
- **Lines 2575–2731:** Mode switching config API
- **Lines 2792–2893:** Sensor data and live data APIs
- **Lines 3010–3141:** SSE live stream endpoint `/api/live_stream`
- **Lines 3141–3939:** Averaging type (B/C/D) config, control, stats APIs
- **Lines 3939–3999:** App entry point (`if __name__ == '__main__'`)

**Global State Variables:**
- `stm32_ts_offsets` (line 156) — device_id → timestamp offset (for clock sync)
- `device_last_data_ts` (line 16) — device_id → last MQTT timestamp
- `otp_store` (line 58) — email → {otp_hash, salt, expires_at, attempts}
- `app.secret_key` (line 93) — Flask session key
- `HOT_RELOAD`, `RUN_MAIN`, `ENABLE_RUNTIME` (lines 60–62) — Dev mode flags

**Dependencies:**
- **Internal (src/):** `src.database.mqtt_database`, `src.database.mqtt_config`, `src.app.services`, `src.devices.device_manager`, `src.modbus.modbus_tcp_device`, `src.detection.event_detector`, `src.events.avg_type_*`, `src.workers.worker_manager`, `src.utils.auto_restart`
- **External:** Flask, paho-mqtt, smtplib, threading, logging, secrets, hashlib

**Side Effects:**
- Subscribes to MQTT topic `stm32/adc` (via `init_mqtt_client()`)
- Publishes event data to MQTT `stm32/events/<device_id>/<sensor_id>/<type>`
- Reads/writes SQLite database `/mnt/ssd/mqtt_database/mqtt_database.db`
- Creates/updates device registry
- Spawns background MQTT consumer thread, worker threads for event detection
- Writes logs to `logs/latency.log` (if `LATENCY_LOG_SECONDS > 0`)
- Sends OTP emails via SMTP (if configured)

**Critical Behavior:**
- **Real ingestion path:** `mqtt_data_consumer()` (line 166) — NOT `src/mqtt/client.py`
  - Listens on MQTT, parses STM32 ADC payloads
  - Anchors device timestamp to server time using `stm32_ts_offsets` dict
  - Feeds data to `LiveDataHub.push()` for live streaming
  - Triggers event detectors per device
- **Live streaming:** `LiveDataHub` ringbuffer + SSE endpoint `/api/live_stream` (line 3010)
- **Authentication:** OTP-based (email) or hardcoded `admin/admin`
- **Auto-restart:** Monitors process health; restarts if stalled (via `src.utils.auto_restart`)

---

#### `/home/embed/hammer/wsgi.py` (20 lines)
**Status:** PRODUCTION ENTRY POINT — ACTIVELY USED

**Purpose:** Gunicorn WSGI wrapper. Calls Flask app initialization and starts MQTT client + STM32 device + auto-restart monitor for production deployment.

**Entry Point:** `gunicorn wsgi:app` (production)

**Key Code:**
```python
from web_server import app, init_mqtt_client, _ensure_stm32_device, db
init_mqtt_client()
_ensure_stm32_device()
init_auto_restart_monitor(db)
```

**Dependencies:** `web_server.py`, `src.utils.auto_restart`

**Side Effects:** Same as `web_server.py` but called once during worker startup (not per request).

---

### 2. MQTT & DEVICE SIMULATORS (Test Harnesses)

#### `/home/embed/hammer/mqtt_test_publisher.py` (177 lines)
**Status:** TEST HARNESS — ACTIVELY USED

**Purpose:** Standalone MQTT publisher that simulates sensor data patterns (ECG, sine wave, square wave, noise, steps). Used for testing dashboard without hardware.

**Entry Point:** `python3 mqtt_test_publisher.py`

**Patterns Generated:**
- ECG heartbeat
- Square wave (binary-like)
- Sine wave
- Random noise
- Stepped/discrete levels

**Dependencies:** `paho-mqtt`, `numpy`, `json`, `time`, `math`, `random`

**Side Effects:**
- Connects to MQTT broker (default: `broker.hivemq.com:1883`)
- Publishes to topics: `sensors/data/ecg`, `sensors/data/square`, `sensors/data/sine`, `sensors/data/noise`, `sensors/data/steps`
- Publishes every 0.1s (10 Hz)
- Logs message count every 50 messages

**CLI:** No arguments required; configure broker/port in code (lines 21–23)

---

#### `/home/embed/hammer/modbus_slave_simulator.py` (472 lines)
**Status:** SIMULATOR — ACTIVELY USED

**Purpose:** Local Modbus TCP slave simulator for testing ModbusTCPDevice without physical hardware. Supports 12 holding registers (matching 12-sensor system) with configurable data patterns.

**Entry Point:** `python3 modbus_slave_simulator.py` (or imported as `ModbusSlaveSimulator` class)

**Key Classes & Methods:**
- `ModbusSlaveSimulator(ip='127.0.0.1', port=5020, slave_id=1, register_count=12)` — Constructor
- `.start(pattern='sine', amplitude=5000, frequency=0.5, offset=5000)` — Start pattern
- `.stop()` — Stop simulator
- `.get_statistics()` — Return uptime, read count, pattern
- `.get_registers()` — Return current register values

**Patterns Supported:**
- `sine` — Smooth oscillation
- `square` — Binary-like
- `ramp` — Linear up/down
- `random` — Random values
- `static` — Constant values

**Dependencies:** `pymodbus`, `time`, `threading`, `math`, `random`

**Side Effects:**
- Binds to TCP port 5020 (default) / 127.0.0.1
- Responds to Modbus TCP read requests
- Updates register values in a background thread

**Usage Example:**
```python
sim = ModbusSlaveSimulator(port=5020)
sim.start(pattern='sine', amplitude=5000)
# Test happens here
sim.stop()
```

---

#### `/home/embed/hammer/run_mqtt_broker.py` (37 lines)
**Status:** OPTIONAL BROKER — RARELY USED

**Purpose:** Runs a local MQTT broker using the `amqtt` library (async MQTT broker).

**Entry Point:** `python3 run_mqtt_broker.py`

**Configuration:**
- TCP listener on `0.0.0.0:1883`
- WebSocket listener on `0.0.0.0:9001`

**Dependencies:** `amqtt`, `asyncio`, `logging`

**Side Effects:**
- Binds to ports 1883 (MQTT) and 9001 (WebSocket)
- Logs broker startup and activity

**Status:** Alternative to Mosquitto; mostly used in docker-compose or isolated testing.

---

### 3. MQTT & DEVICE CONFIGURATION

#### `/home/embed/hammer/mqtt_config_db.py` (157 lines)
**Status:** CONFIG STORAGE — ACTIVELY USED

**Purpose:** Lightweight SQLite store for MQTT broker configuration (single-row table). Kept separate from main database to isolate concerns.

**Entry Point:** Imported by `web_server.py`; instantiated as module-level singleton `mqtt_db`.

**Key Class:**
- `MQTTConfigDB(db_path='/home/embed/hammer/mqtt_database.db')` — Constructor

**Methods:**
- `.get_config()` → Dict with broker, port, base_topic, websocket_url, username, password, enabled
- `.save_config(config: dict)` → Bool

**Database Table:**
```sql
CREATE TABLE mqtt_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    broker TEXT,
    port INTEGER,
    base_topic TEXT,
    websocket_url TEXT,
    username TEXT,
    password TEXT,
    enabled INTEGER,
    updated_at TEXT
)
```

**Dependencies:** `sqlite3`, `threading`, `os`

**Side Effects:**
- Creates `mqtt_database.db` if missing
- Thread-safe with lock (`.lock`)

**Default Values:**
- broker: `localhost`
- port: `1883`
- base_topic: `canbus/sensors/data`
- websocket_url: `ws://localhost:9001/mqtt`

---

#### `/home/embed/hammer/devices.py` (200 lines)
**Status:** API ROUTES — ACTIVELY USED

**Purpose:** Flask Blueprint for device CRUD API. All device management routes (`/api/devices/*`).

**Entry Point:** Imported by `web_server.py`; registered as blueprint `devices_bp`.

**Routes:**
- `GET /api/devices` — List all devices (with cache)
- `POST /api/devices` — Create device
- `GET /api/devices/<id>` — Get device detail
- `PUT /api/devices/<id>` — Update device
- `DELETE /api/devices/<id>` — Delete device
- `POST /api/devices/<id>/initialize` — Initialize device
- `POST /api/devices/<id>/start` — Start event detection
- `POST /api/devices/<id>/stop` — Stop event detection
- `GET /api/devices/<id>/status` — Get device status (running, active)

**Dependencies:** `src.app.services`, Flask

**Key Functions:**
- `get_cached_devices()` — 5s cache TTL
- `invalidate_devices_cache()` — Manual invalidation on create/update/delete

**Side Effects:**
- Creates EventDetector instances on `start`
- Modifies `services.event_detectors` dict
- Overlays runtime `is_active` based on MQTT data flow (4s timeout)

---

#### `/home/embed/hammer/services.py` (81 lines)
**Status:** SHARED STATE — ACTIVELY USED

**Purpose:** Global application state (database connections, device managers, worker threads, MQTT config, live data hub, event detectors).

**Entry Point:** Imported by all Flask routes and `web_server.py`.

**Key Global Variables:**
```python
db = None                           # CANDatabase (main Flask thread)
worker_db = None                    # CANDatabase (background workers)
device_manager = None               # DeviceManager
protocol_manager = None             # ProtocolManager
event_detectors = {}                # {device_id_str: EventDetector}
device_instances = {}               # {device_id: device instance}
device_poll_threads = {}            # {device_id: Thread}
device_poll_stop_events = {}        # {device_id: threading.Event}
device_last_data_ts = {}            # {device_id_str: float} last data timestamp
worker_manager = None               # GlobalWorkerManager
mqtt_client = None                  # MQTT client
mqtt_enabled = False                # MQTT enabled flag
mqtt_broker, mqtt_port              # MQTT broker address
mqtt_base_topic                     # MQTT topic prefix
live_data = None                    # LiveDataHub ringbuffer
avg_detector_b, c, d = None, None, None  # Averaging detectors
_devices_cache = {...}              # Device list cache (5s TTL)
LIVE_BUFFER_MAX_SAMPLES = 2000      # Ringbuffer size
TOTAL_SENSORS = 12
```

**Dependencies:** `threading`, `os`

**Side Effects:** None directly; acts as a namespace for shared state.

---

### 4. DOCUMENTATION & UTILITIES

#### `/home/embed/hammer/generate_docs.py` (382 lines)
**Status:** DOC GENERATOR — OCCASIONALLY USED

**Purpose:** Generates polished `.docx` documentation from markdown reference files.

**Entry Point:** `python3 generate_docs.py`

**Outputs:**
- `docs/API_REFERENCE.docx`
- `docs/DATABASE_SCHEMA.docx`

**Dependencies:** `python-docx`, `re`, `docx.shared`, `docx.oxml`

**Key Functions:**
- `_add_toc()` — Insert table of contents
- `_shade_cell()`, `_shade_para()` — Cell/paragraph background shading
- Markdown to DOCX conversion with custom styling (navy headers, inline code)

**Side Effects:** Creates/overwrites `.docx` files in `docs/` directory.

---

### 5. TEST & MONITORING SCRIPTS

#### `/home/embed/hammer/test_comprehensive.py` (519 lines)
**Status:** TEST SUITE — ACTIVELY USED

**Purpose:** End-to-end test of all 4 event types (A, B, C, D) across all 12 sensors. Uses live MQTT data.

**Entry Point:** `python3 test_comprehensive.py`

**Tests:**
1. Configuration verification
2. All sensors test (all 4 event types)
3. Event storage verification
4. Event API fetch verification

**Dependencies:** `src.database.mqtt_database`, `paho-mqtt`, `json`, `time`

**Side Effects:**
- Connects to MQTT broker (localhost:1883)
- Publishes test data to `stm32/adc`
- Reads events from database
- Prints formatted tables of results

---

#### `/home/embed/hammer/test_live_hardware_ttl.py` (697 lines)
**Status:** INTEGRATION TEST — ACTIVELY USED

**Purpose:** Live hardware test using real STM32 data. Tests event detection accuracy, TTL behavior (duplicate filtering, Event D priority), and database persistence.

**Entry Point:** `python3 test_live_hardware_ttl.py`

**Requirements:**
- STM32 hardware sending data at 100 Hz
- MQTT broker running (mosquitto)
- Server running with TTL enabled
- Physical access to sensors for manual triggers

**Dependencies:** `src.database.mqtt_database`, `src.mqtt.parser`, `paho-mqtt`

**Side Effects:**
- Monitors live MQTT data from `stm32/adc`
- Configures thresholds based on sensor values
- Waits for manual sensor triggers
- Verifies TTL expiry timing
- Queries database for saved events

---

#### `/home/embed/hammer/test_real_data_events.py` (534 lines)
**Status:** DATA-DRIVEN TEST — ACTIVELY USED

**Purpose:** Real-time integration test using LIVE sensor data from SSE stream. Tests event B, C, D algorithms + TTL + buffer sizing.

**Entry Point:** `python3 test_real_data_events.py [--capture N]`

**CLI Options:**
- `--capture N` — Collect N seconds of fresh live data before testing

**Dependencies:** `src.events.avg_type_*`, live stream SSE parsing

**Side Effects:**
- Captures live sensor data (in-memory only, no DB writes)
- Runs event detection on captured data
- Reports pass/fail for each test

---

#### `/home/embed/hammer/test_performance_benchmark.py` (518 lines)
**Status:** BENCHMARK — ACTIVELY USED

**Purpose:** Comprehensive performance benchmark measuring MQTT ingestion latency, event detection speed, TTL accuracy, database operations, memory/CPU usage, and end-to-end event flow timing.

**Entry Point:** `python3 test_performance_benchmark.py`

**Measurements:**
- MQTT callback latency
- Event detection latency (Type A/B/C/D)
- TTL expiry timing accuracy
- Database insert/query speed
- Memory usage (RSS/VMS)
- CPU usage (%)
- Mode switching overhead

**Dependencies:** `paho-mqtt`, `psutil`, `sqlite3`, `time`, `statistics`

**Side Effects:**
- Connects to MQTT
- Subscribes to `stm32/adc`
- Collects timing data over 60s test window (5s warmup + 60s test + 5s cooldown)
- Prints detailed performance report

---

#### `/home/embed/hammer/test_mode_switching_live.py` (382 lines)
**Status:** MODE TEST — ACTIVELY USED

**Purpose:** Live mode switching test with real hardware data. Verifies automatic mode transitions (POWER_ON → STARTUP → BREAK) based on sensor values.

**Entry Point:** `python3 test_mode_switching_live.py`

**Passive test** — modes switch automatically based on sensor values, events only trigger in STARTUP mode.

**Dependencies:** `paho-mqtt`, `sqlite3`, `src.database.mqtt_database`

---

#### `/home/embed/hammer/test_ttl_with_events.py` (282 lines)
**Status:** TTL TEST — ACTIVELY USED

**Purpose:** TTL verification test with real event triggering. Monitors live sensor data, waits for manual sensor presses, tracks TTL timers, verifies Event D priority.

**Entry Point:** `python3 test_ttl_with_events.py`

**Interaction:** **MANUAL** — Press sensors during test to trigger events.

**TTL Values (hardcoded):**
- Type A: 5.0s
- Type B: 10.0s
- Type C: 3.0s
- Type D: 8.0s

**Dependencies:** `paho-mqtt`, `sqlite3`

---

#### `/home/embed/hammer/test_all_sensors.py` (170 lines)
**Status:** SENSOR TEST — ACTIVELY USED

**Purpose:** Verify event detection on all 12 sensors. Fetches recent events, counts by sensor and type, reports pass/fail for each sensor.

**Entry Point:** `python3 test_all_sensors.py`

**Dependencies:** `src.database.mqtt_database`

---

#### `/home/embed/hammer/test_s10_events.py` (79 lines)
**Status:** SENSOR 10 TEST — ACTIVELY USED

**Purpose:** Verify sensor 10 events are saved and fetchable. Tests database storage and API fetch.

**Entry Point:** `python3 test_s10_events.py`

**Dependencies:** `src.database.mqtt_database`

---

#### `/home/embed/hammer/test_login.py` (7 lines)
**Status:** SMOKE TEST — OCCASIONALLY USED

**Purpose:** Quick login test. POSTs to `/api/auth/login` with default credentials.

**Entry Point:** `python3 test_login.py`

**Dependencies:** `requests`

---

#### `/home/embed/hammer/monitor_live_queues.py` (361 lines)
**Status:** PERF MONITOR — ACTIVELY USED

**Purpose:** Monitor running web server API performance in real-time. Watches hardware buffer levels, software queue depths, frame drop rates, anomaly detection.

**Entry Point:** `python3 monitor_live_queues.py [--interval 0.1]`

**CLI Options:**
- `--interval` — Check interval (default 0.1s)

**API Endpoints Monitored:**
- `GET /api/stats` — Hardware buffer + queue stats
- `GET /api/live_sensor_data` — Live data availability

**Side Effects:** Polls server API; tracks samples, anomalies, latency stats.

---

#### `/home/embed/hammer/monitor_live_ttl.py` (263 lines)
**Status:** TTL MONITOR — ACTIVELY USED

**Purpose:** Real-time TTL and event detection monitor. Watches live hardware data, displays sensor values, active TTL timers, events saved after expiry, algorithm accuracy.

**Entry Point:** `python3 monitor_live_ttl.py`

**Passive monitor** — just watches the system in action.

**Dependencies:** `paho-mqtt`, `src.database.mqtt_database`, `json`, `time`

---

#### `/home/embed/hammer/monitor_blocking.py` (159 lines)
**Status:** DEBUG MONITOR — OCCASIONALLY USED

**Purpose:** Real-time blocking monitor. Run parallel to `web_server.py` to detect UI freeze causes.

**Entry Point:** `python3 monitor_blocking.py` (Terminal 2, while `web_server.py` runs in Terminal 1)

**Checks:**
- Database lock status
- WAL file size (pending writes)
- Stats file changes (UI update rate)
- Freeze event detection

**Dependencies:** `sqlite3`, `os`, `time`

---

#### `/home/embed/hammer/parallel_monitor.py` (179 lines)
**Status:** DEBUG MONITOR — OCCASIONALLY USED

**Purpose:** Parallel freeze monitor. Run in Terminal 2 while `web_server.py` runs in Terminal 1.

**Entry Point:** `python3 parallel_monitor.py`

**Checks:**
- Database lock status
- WAL file size
- Stats file changes
- Freeze detection with timestamps

**Dependencies:** `sqlite3`, `os`, `time`

---

#### `/home/embed/hammer/analyze_live_server.py` (96 lines)
**Status:** PERF ANALYZER — OCCASIONALLY USED

**Purpose:** Analyze running server performance via API endpoints. Measures MQTT stats, live data availability, API latency, system CPU/memory.

**Entry Point:** `python3 analyze_live_server.py`

**Measurements:**
- MQTT connection status & message rate
- Live sensor data samples available
- API latency (10 requests, avg/min/max)
- Gunicorn worker CPU/memory

**Dependencies:** `requests`, `psutil`, `json`, `time`

---

#### `/home/embed/hammer/profile_realtime_performance.py` (240 lines)
**Status:** PROFILER — OCCASIONALLY USED

**Purpose:** Real-time performance profiler for 100 Hz MQTT ingestion. Identifies exact bottlenecks in the data pipeline.

**Entry Point:** `python3 profile_realtime_performance.py`

**Profiles:**
- Memory (RSS, VMS, Python heap)
- CPU usage
- Thread count
- Top memory allocations

**Dependencies:** `tracemalloc`, `psutil`, `threading`, `src.app.services`

---

### 6. PATTERN ANALYSIS & TESTING

#### `/home/embed/hammer/pattern_detection.py` (748 lines)
**Status:** PATTERN LIBRARY — ACTIVELY USED

**Purpose:** Real-time pattern detector for sensor data streams. Detects patterns (constant, sine, square, ramp, noise, bursts, saturation, dropouts, etc.) without buffering large data.

**Entry Point:** Imported by other scripts; main class is `PatternDetector`.

**Key Classes:**
- `PatternType` (Enum) — UNKNOWN, CONSTANT, SQUARE_WAVE, SINE_WAVE, SMOOTH_CURVE, RANDOM_NOISE, TREND_UP, TREND_DOWN, STEP_CHANGE, SPIKES, BURSTS, PERIODIC, SATURATION, DROPOUTS
- `PatternDetector(window_size=100, sample_rate=100.0)` — Constructor
  - `.add_sample(value, timestamp=None)` — Add sample
  - `.detect_pattern()` → (PatternType, confidence, dict)

**Dependencies:** `numpy`, `scipy`, `deque`

**Side Effects:** None; pure analysis class.

---

#### `/home/embed/hammer/burst_pattern_analysis.py` (248 lines)
**Status:** ANALYSIS SCRIPT — DIAGNOSTIC

**Purpose:** Analyze different burst patterns to predict frame loss based on observed hardware behavior (USB polling latency, buffer capacity).

**Entry Point:** `python3 burst_pattern_analysis.py`

**Constants:**
- Software polling: 10,000 Hz (0.1ms)
- USB latency: 1–2ms
- Hardware buffer: ~150 frames

**Predicts:** Which burst configurations achieve 0% loss.

**Dependencies:** `json`, `datetime`

**Side Effects:** Prints analysis tables.

---

#### `/home/embed/hammer/detailed_burst_analysis.py` (267 lines)
**Status:** ANALYSIS SCRIPT — DIAGNOSTIC

**Purpose:** More rigorous burst analysis with statistical model. Based on empirical data: 5 msg/10ms → 0.4% loss.

**Entry Point:** `python3 detailed_burst_analysis.py`

**Dependencies:** `statistics`, `json`, `datetime`

---

#### `/home/embed/hammer/reverse_analysis.py` (203 lines)
**Status:** ANALYSIS SCRIPT — DIAGNOSTIC

**Purpose:** Reverse-engineer burst configuration from observed loss rate.

**Entry Point:** `python3 reverse_analysis.py`

**Inputs:** Loss percentage

**Outputs:** Estimated configuration (messages per 10ms)

**Dependencies:** (built-in)

---

#### `/home/embed/hammer/pattern_showcase.py` (168 lines)
**Status:** DEMO SCRIPT — TESTING

**Purpose:** Automated pattern showcase for Modbus simulator. Cycles through patterns (static, sine slow/fast, ramp, random) every 30s.

**Entry Point:** `python3 pattern_showcase.py`

**Setup:**
1. Run this script in Terminal 1
2. Run `python3 web_server.py` in Terminal 2
3. Open `http://localhost:8080` in browser
4. Create/start Modbus device (127.0.0.1:5020)
5. Watch dashboard — patterns change every 30s

**Dependencies:** `modbus_slave_simulator`, `time`

---

#### `/home/embed/hammer/diagnose_live_accuracy.py` (346 lines)
**Status:** DIAGNOSTIC TEST — OCCASIONALLY USED

**Purpose:** Automated event detection accuracy test using real STM32 hardware data. Baseline analysis, false positive check, threshold validation, algorithm accuracy simulation.

**Entry Point:** `python3 diagnose_live_accuracy.py`

**Tests:**
1. Baseline analysis — measures normal sensor variation
2. False positive check — verifies events don't trigger on normal data
3. Threshold validation — checks if thresholds are appropriate
4. Algorithm accuracy — simulates threshold violations

**Dependencies:** `paho-mqtt`, `src.database.mqtt_database`, `json`, `statistics`

---

#### `/home/embed/hammer/hardware_buffer_diagnostic.py` (272 lines)
**Status:** DIAGNOSTIC TOOL — LEGACY

**Purpose:** Monitor USB-CAN adapter hardware buffer in real-time. Prove whether frame drops are caused by hardware overflow or software bottlenecks.

**Entry Point:** `python3 hardware_buffer_diagnostic.py`

**Evidence Collected:**
- Hardware buffer depth over time
- Maximum buffer depth
- Polling frequency
- Software processing speed
- Where drops occur (hardware vs software)

**Dependencies:** `ctypes`, `platform`, `statistics`, `json` (platform-specific DLL/SO loading)

**Status:** Legacy — tied to USB-CAN hardware (not MQTT-based HERMES).

---

### 7. START-UP UTILITIES & SIMULATORS

#### `/home/embed/hammer/start_simulator_for_testing.py` (116 lines)
**Status:** QUICK START — ACTIVELY USED

**Purpose:** Quick start script for Modbus simulator testing with web UI. Pre-configures simulator with optimal testing settings.

**Entry Point:** `python3 start_simulator_for_testing.py`

**Configuration:**
- IP: 127.0.0.1
- Port: 5020
- Pattern: Sine wave (amplitude=5000, frequency=0.5 Hz, offset=5000)
- Registers: 12 (sensors)

**Next Steps Printed:**
1. Run `python3 web_server.py` in another terminal
2. Open `http://localhost:8080`
3. Create Modbus device (127.0.0.1:5020)
4. Initialize → Start → View Dashboard

**Dependencies:** `modbus_slave_simulator`, `time`, `sys`

---

#### `/home/embed/hammer/control_s10.py` (176 lines)
**Status:** UTILITY — OCCASIONALLY USED

**Purpose:** Interactive control for sensor 10. Real-time manual control of sensor 10 values via MQTT.

**Entry Point:** `python3 control_s10.py`

**Usage:** Send sensor 10 values via MQTT (adc2[2]).

**Dependencies:** `paho-mqtt`, `json`, `time`

---

### 8. DATABASE UTILITIES & ONE-OFF MIGRATIONS

These are **DROP** candidates — one-off migrations and schema fixes. Listed here for completeness.

#### `/home/embed/hammer/check_db.py` (32 lines)
**Status:** DIAGNOSTIC — LEGACY

**Purpose:** Check total frames in CAN database.

**Entry Point:** `python3 check_db.py`

**Queries:** `SELECT COUNT(*) FROM can_frames`

---

#### `/home/embed/hammer/check_devices.py` (19 lines)
**Status:** DIAGNOSTIC — LEGACY

**Purpose:** Check existing devices in database.

**Entry Point:** `python3 check_devices.py`

---

#### `/home/embed/hammer/check_latest_timestamp.py` (18 lines)
**Status:** DIAGNOSTIC — LEGACY

**Purpose:** Check latest event timestamp.

**Entry Point:** `python3 check_latest_timestamp.py`

---

#### `/home/embed/hammer/check_timestamps.py` (81 lines)
**Status:** DIAGNOSTIC — LEGACY

**Purpose:** Timestamp validation tool. Check if Modbus TCP timestamps are monotonically increasing.

**Entry Point:** `python3 check_timestamps.py`

---

#### `/home/embed/hammer/check_204_frames.py` (45 lines)
**Status:** DIAGNOSTIC — LEGACY

**Purpose:** Check CAN ID 0x204 frames for mixed data patterns.

**Entry Point:** `python3 check_204_frames.py`

---

#### `/home/embed/hammer/migrate_events_table.py` (237 lines)
**Status:** ONE-OFF MIGRATION — LEGACY

**Purpose:** Migrate events table from wide format (event-only) to continuous sensor data format.

**Entry Point:** `python3 migrate_events_table.py`

**Actions:**
1. Renames current events table to events_old_backup
2. Creates new events table with continuous sensor data structure
3. Preserves old data for reference

**Side Effects:** Modifies database schema.

---

#### `/home/embed/hammer/migrate_per_sensor_ttl.py` (94 lines)
**Status:** ONE-OFF MIGRATION — LEGACY

**Purpose:** Add per-sensor TTL configuration to event_config_type_a_per_sensor table.

**Entry Point:** `python3 migrate_per_sensor_ttl.py`

**Rationale:** Allow different TTL values for each sensor/inverter.

---

#### `/home/embed/hammer/add_ttl_columns.py` (118 lines)
**Status:** ONE-OFF MIGRATION — LEGACY

**Purpose:** Add TTL (Time To Live) columns to event config tables.

**Entry Point:** `python3 add_ttl_columns.py`

**Actions:**
- Adds `ttl_seconds` column to `event_config_type_a` (default 5.0s)
- Adds `ttl_seconds` column to `event_config_type_b` (default 10.0s)
- Adds `ttl_seconds` column to `event_config_type_c` (default 3.0s)
- Adds `ttl_seconds` column to `event_config_type_d` (default 8.0s)

---

#### `/home/embed/hammer/fix_type_a_schema.py` (50 lines)
**Status:** ONE-OFF FIX — LEGACY

**Purpose:** Remove device_id NOT NULL constraint from event_config_type_a table.

**Entry Point:** `python3 fix_type_a_schema.py`

---

#### `/home/embed/hammer/create_wide_events_table.py` (56 lines)
**Status:** ONE-OFF CREATION — LEGACY

**Purpose:** Recreate events table in WIDE format (66 columns: 12 sensor values + 48 event flags + metadata).

**Entry Point:** `python3 create_wide_events_table.py`

**Schema:** sr_no, device_id, timestamp, event_datetime, sensor{1..12}_value, sensor{1..12}_event_{a,b,c,d}, notes, data_window

---

### 9. CONFIGURATION FILES (ONE-OFF)

#### `/home/embed/hammer/custom_board_config.py` (61 lines)
**Status:** CONFIGURATION — LEGACY

**Purpose:** Configuration for custom Modbus board (thread sensor on Register 4, Function 04).

**Entry Point:** Imported or executed standalone.

**Content:** Device configuration dict + troubleshooting guide for alternating data patterns.

---

#### `/home/embed/hammer/config_custom_board_exact.py` (37 lines)
**Status:** CONFIGURATION — LEGACY

**Purpose:** Custom board exact data plotting (no filtering).

**Entry Point:** Imported or executed standalone.

**Note:** Designed to plot EXACT data as received, including alternating values (no filtering).

---

### 10. MISC UTILITIES

#### `/home/embed/hammer/query_events.py` (13 lines)
**Status:** QUICK QUERY — OCCASIONALLY USED

**Purpose:** Quick HTTP GET to `/api/event/list?limit=10` to fetch recent events.

**Entry Point:** `python3 query_events.py`

**Endpoint:** `http://192.168.1.149:8080/api/event/list?limit=10` (hardcoded IP)

**Dependencies:** `requests`, `json`

---

## Global State & Configuration

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DEV_HOT_RELOAD` | 0 | Enable Flask hot reload |
| `WERKZEUG_RUN_MAIN` | "false" | Werkzeug dev server flag |
| `LATENCY_LOG_SECONDS` | 0 | Latency logging duration (0 = disabled) |
| `LATENCY_LOG_PATH` | `logs/latency.log` | Latency log file path |
| `LATENCY_LOG_DEVICE` | 1 | Device ID to log latency for |
| `LIVE_BUFFER_MAX_SAMPLES` | 2500 | Ringbuffer size for live streaming |
| `LIVE_STREAM_INTERVAL_S` | 0.1 | SSE stream cadence (100 ms = 10 Hz) |
| `SENSOR_LOG_ENABLED` | 0 | Enable extra live sensor logging |
| `SENSOR` | (none) | Filter event avg logs by sensor/type |
| `MQTT_EVENT_DEBUG` | 0 | Verbose MQTT event publish logs |
| `ALLOWED_EMAILS_PATH` | `emails.txt` | Allowlist for email OTP login |
| `OTP_EXPIRY_SECONDS` | 300 | OTP validity duration (5 min) |
| `OTP_MAX_ATTEMPTS` | 5 | Max OTP verification attempts |
| `OTP_RESEND_SECONDS` | 60 | Min time between OTP resends |
| `OTP_MAX_PER_HOUR` | 5 | Max OTP requests per hour |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` | (none) | Email configuration for OTP |

### Key Databases

| Database | Path | Purpose |
|----------|------|---------|
| Main | `/mnt/ssd/mqtt_database/mqtt_database.db` | Events, device config, sensor data |
| MQTT Config | `mqtt_database.db` (at root or src/) | Broker settings (single row) |
| CAN Legacy | `can_frames.db` | Legacy CAN bus data (deprecated in MQTT era) |

---

## Summary: Keep / Drop / Uncertain

### KEEP (11 scripts) — Required for new rewrite

1. **web_server.py** (3,999 lines) — PRIMARY RUNTIME; must preserve MQTT ingestion path, live streaming, all REST API endpoints
2. **wsgi.py** (20 lines) — Production entry point
3. **mqtt_test_publisher.py** (177 lines) — Test harness for pattern generation
4. **modbus_slave_simulator.py** (472 lines) — Hardware simulator
5. **mqtt_config_db.py** (157 lines) — Broker config persistence
6. **services.py** (81 lines) — Shared state namespace
7. **devices.py** (200 lines) — Device CRUD API
8. **generate_docs.py** (382 lines) — Doc generation
9. **start_simulator_for_testing.py** (116 lines) — Quick start for testing
10. **monitor_live_queues.py** (361 lines) — API perf monitor
11. **pattern_detection.py** (748 lines) — Pattern detection library

### DROP (18 scripts) — One-off migrations, legacy CAN tools

All `check_*.py`, `migrate_*.py`, `fix_*.py`, `add_*.py`, plus:
- `create_wide_events_table.py`
- `custom_board_config.py`
- `config_custom_board_exact.py`
- `check_204_frames.py`
- `hardware_buffer_diagnostic.py` (USB-CAN hardware, not MQTT)

**Rationale:** These are schema migrations, diagnostics, and legacy CAN bus tools. The rewrite should start with a clean database schema; these migrations won't apply.

### UNCERTAIN (17 scripts) — Active tests / diagnostics

**Test Suite (11):**
- `test_comprehensive.py` — End-to-end event test
- `test_live_hardware_ttl.py` — Integration test with hardware
- `test_real_data_events.py` — Data-driven event test
- `test_performance_benchmark.py` — Performance baseline
- `test_mode_switching_live.py` — Mode switching test
- `test_ttl_with_events.py` — TTL verification test
- `test_all_sensors.py` — All-sensor test
- `test_s10_events.py` — Sensor 10 test
- `test_login.py` — Login smoke test
- `burst_pattern_analysis.py` — Burst analysis
- `detailed_burst_analysis.py` — Detailed burst analysis

**Monitoring/Diagnostics (4):**
- `monitor_live_ttl.py` — TTL monitor
- `monitor_blocking.py` — Freeze detection
- `parallel_monitor.py` — Parallel freeze detection
- `analyze_live_server.py` — API perf analyzer

**Utilities (2):**
- `control_s10.py` — Manual sensor 10 control
- `query_events.py` — Quick event query
- `run_mqtt_broker.py` — Optional local MQTT broker
- `profile_realtime_performance.py` — Performance profiler
- `reverse_analysis.py` — Loss rate analysis
- `diagnose_live_accuracy.py` — Accuracy diagnostic
- `pattern_showcase.py` — Modbus pattern demo

**Decision:** Keep these if they provide value in testing/validation. Drop if the new dashboard has equivalent built-in diagnostics (e.g., live stats page, system health endpoint).

---

## Migration Notes

### Critical Behavior to Preserve

1. **MQTT Ingestion:** `mqtt_data_consumer()` thread in `web_server.py` (line 166)
   - Real-time queue; processes ~100 Hz @ 12 sensors
   - Timestamp anchoring via `stm32_ts_offsets` dict
   - Must feed `LiveDataHub` for live streaming

2. **Live Streaming:** `LiveDataHub` ringbuffer + SSE endpoint `/api/live_stream`
   - 2.5K sample max (configurable)
   - 0.1s update rate (10 Hz) (configurable)
   - Critical for real-time UI responsiveness

3. **Event Detection:** Per-device EventDetector instances
   - Background workers (SQLite safe)
   - TTL-based event coalescing
   - Event D priority (clears A/B/C)

4. **Database Layout:** Wide event table with 12 sensors × 4 types
   - All-sensor-data-in-one-row design
   - Simplifies event correlation

5. **API Endpoints:** 60+ routes (see `web_server.py` lines 1066–3999)
   - Auth, devices, events, live data, MQTT config, averaging algorithms
   - Most are REST + JSON; some are SSE (live_stream)

### Testing Recommendation

Before dropping any `test_*.py` script, ensure the new dashboard has equivalent:
- Smoke tests (login, API health)
- Integration tests (multi-sensor event detection)
- Regression tests (TTL behavior, mode switching)
- Performance benchmarks (latency, throughput, memory)

---

## References

- **Main Entry:** `/home/embed/hammer/web_server.py` + `./run.sh`
- **Production Deployment:** `wsgi.py` + gunicorn (see `run.sh` line 52–62)
- **MQTT Topic:** `stm32/adc` (STM32 → server)
- **Event Publish:** `stm32/events/<device_id>/<sensor_id>/<type>` (server → downstream)
- **Database:** `/mnt/ssd/mqtt_database/mqtt_database.db`
- **Config DB:** `mqtt_database.db` (MQTT broker settings)
- **Docs:** `README.md` (quick start, API list)

---

**End of Reference Document**
