# HERMES Sensor Dashboard — Operational & Deployment Files Reference

**Document Date:** 2026-04-23  
**Purpose:** Complete behavior-contract capture of all operational files in the legacy system before full rewrite to .deb packaging  
**Audience:** DevOps engineers, systems architects, Hermes rewrite team  
**Status:** Phase 0.5 — Complete file catalog with semantics and production implications

---

## Table of Contents

1. [Quick Summary](#quick-summary)
2. [Root-Level Operational Files](#root-level-operational-files)
3. [Documentation Files](#documentation-files)
4. [Deployment Flow — Current System](#deployment-flow--current-system)
5. [Secrets Inventory](#secrets-inventory)
6. [Preservation Plan](#preservation-plan)
7. [Database & Persistence](#database--persistence)
8. [Systemd Integration (None Currently)](#systemd-integration-none-currently)

---

## Quick Summary

| Category | File Count | Key Files | Status |
|----------|-----------|-----------|--------|
| **Start/Stop Scripts** | 2 | `run.sh`, `stop.sh` | Production active |
| **Installation** | 1 | `install.sh` | Deployment/setup |
| **Containerization** | 2 | `Dockerfile`, `.dockerignore` | Optional; not standard deploy |
| **Configuration** | 3 | `.env`, `emails.txt`, `requirements.txt` | Production secrets + dependencies |
| **WSGI/Entry** | 2 | `wsgi.py`, main `web_server.py` | Gunicorn entry point |
| **Utility Scripts** | 5 | `configure_all_sensors.sh`, `trigger_s10_demo.sh`, etc. | Demo/test/manual ops |
| **System Config** | 3 | `pytest.ini`, `.gitignore`, `.dockerignore` | Development/CI |
| **Documentation** | 50+ | `docs/*.md`, architecture diagrams | Knowledge base |
| **NO systemd units** | 0 | — | **Gap: Must design for rewrite** |

---

## Root-Level Operational Files

### 1. **run.sh** — Start the Application Server

**Path:** `/home/embed/hammer/run.sh`  
**Purpose:** Launch the HERMES sensor dashboard. Autodetects production vs. dev mode. Primary entry point for live system startup.

**Mode:** Production/Development hybrid  
**Invoked by:** Manual CLI, systemd (in new design), cron jobs, Docker ENTRYPOINT  

**Content Line-by-Line:**

| Line(s) | Code | Purpose |
|---------|------|---------|
| 1-8 | Bash header + `SCRIPT_DIR` setup | Safety: isolate working directory |
| 11-12 | `PYTHONPYCACHEPREFIX` | Keep `__pycache__/` centralized, not scattered (good for SD card on RPi) |
| 15-17 | Activate `.venv/bin/activate` | Optional virtual environment support |
| 20-25 | Source `.env` file | Load environment variables for config (SMTP, MQTT broker, logging flags) |
| 28-48 | Help text (`--help`) | Document all logging options (SENSOR, LATENCY_LOG_SECONDS, MQTT_EVENT_DEBUG, etc.) |
| 49-51 | `--dev` flag → `DEV_HOT_RELOAD=1 python3 web_server.py` | Flask dev server with auto-reload; **NOT for production** |
| 52-62 | **Production path:** gunicorn with gthread worker | **ACTIVE in production:** `--workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:8080` |
| 63-65 | Fallback: direct Flask server | Graceful degradation if gunicorn not installed |

**Critical Parameters:**

```bash
# Production mode (when gunicorn installed):
gunicorn \
  --worker-class gthread          # Thread-based, not fork (safe for MQTT subscriptions) \
  --workers 1                     # Single process (multi-process adds complexity to MQTT) \
  --threads 8                     # Enough for request handling + MQTT callbacks \
  --timeout 120                   # Long timeout for slow client uploads \
  --graceful-timeout 0            # Don't wait on graceful shutdown (systemd timeout will kill) \
  --bind 0.0.0.0:8080            # Listen on all interfaces, port 8080 (default) \
  --access-logfile -              # Log to stdout (picked up by systemd journal) \
  wsgi:app                        # Load Flask app from wsgi.py module
```

**Environment Variables Supported:**

| Var | Default | Purpose |
|-----|---------|---------|
| `DEV_HOT_RELOAD` | (unset) | If `1`, restart Flask on template/static changes (dev only) |
| `SENSOR_LOG_ENABLED` | (unset) | If `1`, enable per-event average logging to `logs/sensor.log` |
| `SENSOR` | (unset) | Filter logging by sensor, e.g. `"1A"`, `"1A,3B"`, `"1A 3B 5"` |
| `LATENCY_LOG_SECONDS` | (unset) | Enable latency logging for N seconds after startup |
| `LATENCY_LOG_PATH` | `logs/latency.log` | Where to write latency data |
| `LATENCY_LOG_DEVICE` | `1` | Device ID to monitor for latency |
| `MQTT_EVENT_DEBUG` | (unset) | If `1`, verbose MQTT event publish logs |
| `LIVE_BUFFER_MAX_SAMPLES` | `2000` | In-memory circular buffer size per sensor |
| `LIVE_STREAM_INTERVAL_S` | `0.1` | SSE push cadence (100 ms) |

**Associated Files:**

- Imports: `wsgi.py` (via Gunicorn), `web_server.py` (direct or via WSGI)
- Loads: `.env` (optional overrides)
- Activates: `.venv/bin/activate` (optional Python venv)

**Production vs Dev:**

- **Production:** gunicorn with 1 worker, 8 threads, timeout 120s (default), listens on `0.0.0.0:8080`
- **Development:** `./run.sh --dev` → Flask dev server with hot reload on port 5000 (hardcoded in `web_server.py`)
- **Fallback:** Plain Python if gunicorn not installed (slower, single-threaded)

---

### 2. **stop.sh** — Stop the Application Server

**Path:** `/home/embed/hammer/stop.sh`  
**Purpose:** Gracefully shut down the running server on port 8080.

**Mode:** Operations (manual or systemd)  
**Invoked by:** Manual CLI, systemd ExecStop, Docker entrypoint override  

**Content Line-by-Line:**

| Line(s) | Code | Purpose |
|---------|------|---------|
| 4 | `PORT=8080` | Hard-coded port (matches `run.sh`) |
| 7 | `lsof -ti:$PORT` | Find process IDs listening on the port |
| 9-11 | Error handling: exit 0 if none found | Idempotent: safe to call when already stopped |
| 17 | `kill $PIDS` (SIGTERM) | Signal graceful shutdown |
| 20-26 | Loop up to 5s, check if port freed | Wait for graceful exit |
| 29-34 | If still running: `kill -9` (SIGKILL) | Force kill after grace period |
| 36-41 | Final check + exit code | Return 0 if stopped, 1 if failed |

**Shutdown Semantics:**

1. **Graceful phase (0–5s):** SIGTERM to all PIDs → Flask/Gunicorn close connections, flush databases
2. **Force phase (5s+):** SIGKILL → Immediate termination (data may be in-flight)
3. **Idempotency:** Returns success even if already stopped

**Critical Behavior:**

```bash
# Graceful (preferred):
kill $PIDS  # → gunicorn closes connections, MQTT unsubscribes, databases flush

# Force (if grace timeout exceeded):
kill -9 $PIDS  # → Immediate exit; SQLite WAL ensures crash recovery
```

**Production Integration:**

This is suitable for systemd `ExecStop=` directive:

```ini
[Service]
ExecStart=/home/embed/hammer/run.sh
ExecStop=/home/embed/hammer/stop.sh
TimeoutStopSec=10
```

---

### 3. **install.sh** — Installation & Dependency Setup

**Path:** `/home/embed/hammer/install.sh`  
**Purpose:** One-time setup: verify Python 3, install pip, download Python dependencies, detect CPU architecture, configure USB permissions, set up udev rules.

**Mode:** Installation (one-time)  
**Invoked by:** First deployment, manual setup script, Docker build (optional)  

**Content Line-by-Line:**

| Line(s) | Code | Purpose |
|---------|------|---------|
| 10-15 | Check Python 3 exists | Fail early if runtime not available |
| 20-25 | Install pip3 if missing | Ensure package manager available |
| 30 | `pip3 install -r requirements.txt` | Install Flask, MQTT, Modbus, etc. |
| 33-55 | Detect CPU architecture (`uname -m`) | Choose correct native library: `x64`, `x32`, `arm64`, `arm32` |
| 57-61 | Verify library exists for arch | Warn if `Secondary-lib-dll/linux-lib/*/libcontrolcan.so` missing (CAN driver) |
| 64-83 | **Linux only:** Create udev rule for USB-CAN device | Enable non-root access to USB device (vendor `04d8`, product `0053`) |
| 82 | `sudo usermod -a -G plugdev $USER` | Add user to plugdev group (requires sudo) |

**Architecture Detection:**

```bash
case $ARCH in
    x86_64)    LIB_PATH="Secondary-lib-dll/linux-lib/x64/libcontrolcan.so" ;;
    i686|i386) LIB_PATH="Secondary-lib-dll/linux-lib/x32/libcontrolcan.so" ;;
    aarch64)   LIB_PATH="Secondary-lib-dll/linux-lib/arm64/libcontrolcan.so" ;;  # Raspberry Pi 4
    armv7l)    LIB_PATH="Secondary-lib-dll/linux-lib/arm32/libcontrolcan.so" ;;  # Raspberry Pi 3
esac
```

**USB Permissions (udev rule):**

```bash
# File: /etc/udev/rules.d/99-usb-can.rules
SUBSYSTEM=="usb", ATTR{idVendor}=="04d8", ATTR{idProduct}=="0053", MODE="0666", GROUP="plugdev"
```

Allows any user in `plugdev` group to access the Waveshare USB-CAN-B adapter without sudo.

**Production Notes:**

- **Idempotent:** Safe to run multiple times (skips if udev rule already exists)
- **Requires sudo:** For pip, udev rules, usermod
- **Post-Install:** Requires logout/login for group membership to take effect
- **Not in CI/CD:** Should be baked into Docker image or .deb package

---

### 4. **wsgi.py** — Gunicorn Entry Point

**Path:** `/home/embed/hammer/wsgi.py`  
**Purpose:** Bootstrap module for Gunicorn. Imports Flask app and runs initialization functions that normally run in `if __name__ == '__main__'` blocks.

**Mode:** Production (only via Gunicorn)  
**Invoked by:** Gunicorn worker process: `gunicorn wsgi:app`  

**Content Line-by-Line:**

```python
# Line 1-5: Docstring
"""
Gunicorn WSGI entry point.
Imports the Flask app from web_server.py and runs startup initialisation
(MQTT client + STM32 device + auto-restart monitor) that normally runs in __main__.
"""

# Lines 6-8: Initialize print statements (visible in gunicorn logs)
print("[WSGI] Starting initialization...")

# Line 8: Import Flask app + startup functions from web_server.py
from web_server import app, init_mqtt_client, _ensure_stm32_device, db

# Line 9: Import auto-restart monitor from utils
from src.utils.auto_restart import init_auto_restart_monitor

# Lines 11-12: Start MQTT client (connects, subscribes, starts loop_start())
print("[WSGI] Calling init_mqtt_client()...")
init_mqtt_client()

# Lines 14-15: Create/verify device_id=1, instantiate EventDetector, start detection threads
print("[WSGI] Calling _ensure_stm32_device()...")
_ensure_stm32_device()

# Lines 17-18: Start daily auto-restart monitor
print("[WSGI] Calling init_auto_restart_monitor()...")
init_auto_restart_monitor(db)

# Line 20: Print completion
print("[WSGI] Initialization complete!")
```

**Initialization Order:**

1. **MQTT client:** Connects to broker, subscribes to `stm32/adc`, starts background listen thread
2. **STM32 device:** Creates device_id=1 in DB if not present, instantiates EventDetector for detection (Types A/B/C/D)
3. **Auto-restart monitor:** Schedules a daily graceful restart at configured time (e.g., 2 AM)

**Critical Detail:** All 3 steps must complete before Gunicorn accepts HTTP requests. If any fail, Gunicorn will report error but may still start (depending on exception handling in `web_server.py`).

**Production Behavior:**

- Called once per Gunicorn master process
- With `--workers 1`, initialization runs only once
- With `--workers N`, initialization runs in the master but not in worker forks (fork happens after)
- MQTT client subscriptions are thread-safe (paho-mqtt client uses internal locks)

---

### 5. **web_server.py** — Main Flask Application

**Path:** `/home/embed/hammer/web_server.py` (3688 lines)  
**Purpose:** The active production entry point. Defines Flask app, all routes, MQTT handlers, event detection orchestration, and database management.

**Mode:** Production  
**Invoked by:** Direct (`python3 web_server.py`) or via Gunicorn (`wsgi.py`)  

**Structure Summary:**

| Section | Lines | Purpose |
|---------|-------|---------|
| Imports & Init | ~200 | Flask, MQTT, database, services |
| Global State | ~100 | Module-level `db`, `worker_manager`, `live_data_hub`, `app` instances |
| `__main__` block | ~50 | Dev mode entry: starts MQTT, device setup, runs Flask dev server |
| Auth routes | ~200 | `/login`, `/api/auth/*` (username/password + OTP) |
| Dashboard routes | ~150 | `/`, `/device-config`, `/event-config`, `/ttl-config`, `/system-config` |
| Device CRUD API | ~200 | `/api/devices` (list, get, create, update, delete, start, stop) |
| Sensor data API | ~300 | `/api/sensor_data`, `/api/live_sensor_data`, `/api/live_stream` (SSE) |
| Event config API | ~400 | `/api/event/config/*` (Types A/B/C/D, global + per-sensor) |
| MQTT API | ~100 | `/api/mqtt/config`, `/api/mqtt/status` |
| System API | ~100 | `/api/system/auto-restart/*`, `health` endpoints |
| MQTT handler | ~300 | `on_message()` callback; parses STM32 payload, feeds to event detector |
| Startup funcs | ~100 | `init_mqtt_client()`, `_ensure_stm32_device()`, apply_avg_configs_to_detector()` |

**Key Global Variables:**

```python
# Database connections
db = MQTTDatabase("/media/embedsquare/PI_DATABASE/mqtt_database/mqtt_database.db")
worker_db = MQTTDatabase(...)  # Separate conn for worker threads

# Shared state
device_manager = DeviceManager(database=db)
live_data_hub = LiveDataHub(max_samples=2500, total_sensors=12)
worker_manager = GlobalWorkerManager(database=worker_db, ...)
event_detectors = {}  # Dict[device_id, EventDetector]

# MQTT
mqtt_client = None
mqtt_enabled = False
mqtt_broker = "localhost"
mqtt_port = 1883
mqtt_base_topic = "canbus/sensors/data"

# Flask app
app = Flask(__name__)
app.secret_key = "YOUR_SECRET_KEY"  # For session cookies
```

**Critical Execution Path (MQTT message → event detection):**

```
MQTT Broker publishes on stm32/adc
         ↓
on_message(client, userdata, msg) callback triggered
         ↓
parse_stm32_adc_payload(msg.payload)  # Extract device_id, ts, adc1[], adc2[]
         ↓
event_detectors[device_id].add_sample(sensor_id, value)  # Feed to detector
         ↓
EventDetector runs event detection (Type A/B/C/D) every 100ms
         ↓
If event triggered: publish_event_mqtt(device_id, sensor_id, event_type, ...)
         ↓
write_event_to_db() async in worker thread pool
```

**Database Path (Critical for Raspberry Pi):**

```python
db_path = "/media/embedsquare/PI_DATABASE/mqtt_database/mqtt_database.db"  # SSD mount
# Fallback if not mounted:
db_path = "src/database/mqtt_database.db"  # In app directory
```

This assumes an external SSD mounted at `/media/embedsquare/PI_DATABASE/`. If unmounted, falls back to SD card (slower, shorter lifespan).

**Production Issues to Note:**

- **Hardcoded paths:** Database path, log directories assume specific mount points
- **Secret key:** `"YOUR_SECRET_KEY"` is a placeholder; must be set to random value in production
- **MQTT blocking:** If broker is down, app hangs during startup (no timeout on connect)
- **No request logging:** Uses Gunicorn's `--access-logfile -` for HTTP logs, not Flask's
- **No systemd integration:** Health checks, restart timers must be managed by systemd unit file

---

### 6. **requirements.txt** — Python Dependencies

**Path:** `/home/embed/hammer/requirements.txt`  
**Purpose:** Pin list of exact (or minimum) versions for pip to install.

**Content:**

```txt
flask>=2.0.0
flask-cors>=3.0.0
pymodbus>=3.0.0
paho-mqtt>=2.1.0
```

**Critical Observations:**

1. **gunicorn is NOT listed** — Must be installed separately or assumed pre-installed
2. **SQLite3:** No explicit dependency (built into Python)
3. **bcrypt/passlib:** Not listed; authentication uses basic SHA-256 (see code review)
4. **No pinned versions:** Uses `>=` (minimum); allows upstream updates → potential compatibility risk
5. **Modbus support:** `pymodbus>=3.0.0` included but optional (not all deployments use it)

**Production Recommendation:**

For Raspberry Pi deployment, should use pinned versions:

```txt
flask==2.3.2
flask-cors==4.0.0
pymodbus==3.4.0
paho-mqtt==2.1.0
gunicorn==21.2.0
werkzeug==2.3.0
```

---

### 7. **.env** — Secrets & Configuration

**Path:** `/home/embed/hammer/.env`  
**Purpose:** Environment variable overrides for runtime config (SMTP credentials, MQTT broker, logging flags).

**Sensitive Content:**

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=atharvaopenapi@gmail.com
SMTP_PASS='REDACTED-ROTATE-IMMEDIATELY'           # ⚠️ EXPOSED APP PASSWORD
SMTP_FROM=atharvaopenapi@gmail.com
ALLOWED_EMAILS_PATH=emails.txt
```

**🚨 CRITICAL SECURITY ISSUE:**

- **Gmail App Password is EXPOSED in repository** (`REDACTED-ROTATE-IMMEDIATELY`)
- This is a **shared development credential**, not production-safe
- **Action Required:** Revoke immediately before rewrite ships; use password rotation in Gmail settings

**Production Use:**

In production, `.env` should **NOT be committed**. Instead:

```bash
# Option 1: systemd environment file
/etc/default/hermes-sensor-dashboard
[Contains: SMTP_USER, SMTP_PASS, MQTT_BROKER, etc.]

# Option 2: Docker secrets / K8s ConfigMap
# Option 3: Environment variable injection at deploy time

# Option 4: .env file stored in /etc/hermes/ with restricted permissions
# Sourced at runtime: `set -a; source /etc/hermes/hermes.env; set +a`
```

**Loaded by:**

```bash
# In run.sh (lines 20-25):
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
fi
```

The `set -a` ensures all variables are exported to child processes (including Gunicorn).

---

### 8. **emails.txt** — Email Allowlist for OTP Login

**Path:** `/home/embed/hammer/emails.txt`  
**Purpose:** Allowlist of email addresses allowed to use OTP login (one per line).

**Content:**

```
# Allowed login emails (one per line)
admin@example.com

atharva@embedsquare.com
cbidap@embedsquare.com
rushikesh@embedsquare.com
surya@embedsquare.com
```

**Usage:**

When user requests OTP login with email `X@Y.com`:

1. Check if `X@Y.com` is in `emails.txt`
2. If yes: generate OTP, send via SMTP
3. If no: reject request

**Loaded by:**

```python
# In web_server.py
ALLOWED_EMAILS_PATH = os.getenv("ALLOWED_EMAILS_PATH", "emails.txt")
with open(ALLOWED_EMAILS_PATH) as f:
    allowed_emails = {line.strip() for line in f if line.strip() and not line.strip().startswith('#')}
```

**Production Notes:**

- Stored in application directory (not secrets store) → acceptable for allowlist but should migrate to database
- Comments (lines starting with `#`) are ignored
- Empty lines are ignored
- Case-sensitive match (email canonicalization not applied)

---

### 9. **Dockerfile** — Container Image Definition

**Path:** `/home/embed/hammer/Dockerfile`  
**Purpose:** Optional containerization for development/testing. Not the primary deployment method.

**Content:**

```dockerfile
FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    libc-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose port 8080 (the default port in web_server.py)
EXPOSE 8080

# Command to run the application
CMD ["python3", "web_server.py"]
```

**Key Details:**

- **Base:** `python:3.9-slim` (lightweight, ~150 MB)
- **Dependencies:** `gcc`, `libc-dev` (required to build Python C extensions, e.g., MQTT)
- **No gunicorn:** Runs Flask dev server directly → not suitable for production
- **Port:** 8080 (matches `run.sh`)
- **Entry:** `python3 web_server.py` (dev mode, not WSGI)

**Production Gaps:**

- Should use gunicorn instead of Flask dev server
- Should include `--user` to avoid running as root
- Should add health check
- No systemd socket activation
- No logging configuration for container orchestration

---

### 10. **.dockerignore** — Docker Build Exclusions

**Path:** `/home/embed/hammer/.dockerignore`  
**Purpose:** Reduce Docker image size by excluding unnecessary files.

**Content:**

```
.git
.github
.gitignore
.claude
CLAUDE.md
AGENTS.md
CHANGELOG.md
README.md
__pycache__/
*.pyc
...
docs/
tests/
*.log
*.db
```

**Effect:**

- **Excluded:** Git metadata, build artifacts, logs, databases, documentation
- **Included:** Source code, `requirements.txt`, `Dockerfile`
- **Result:** Cleaner image, faster builds

---

### 11. **.gitignore** — Git Exclusions

**Path:** `/home/embed/hammer/.gitignore`  
**Purpose:** Prevent committing sensitive files, build artifacts, OS-specific files.

**Key Patterns:**

```bash
.env              # 🚨 But currently COMMITTED (leaked secrets!)
.venv/
__pycache__/
*.db
*.log
/venv/
```

**Critical Issue:**

- `.env` is in `.gitignore` **but the file is already in the repository** (git history)
- Any credentials in `.env` are leaked (e.g., SMTP password)
- **Mitigation:** Use `git rm --cached .env` before rewrite, add to `.gitignore`, rotate all credentials

---

### 12. **pytest.ini** — Pytest Configuration

**Path:** `/home/embed/hammer/pytest.ini`  
**Purpose:** Pytest options (test discovery, output format).

**Content:**

```ini
[pytest]
addopts = -s
testpaths = tests
```

**Settings:**

- `-s`: Show print statements (don't capture stdout)
- `testpaths = tests`: Only run tests in `tests/` directory

**Usage:**

```bash
pytest                 # Run all tests in tests/
pytest -v              # Verbose mode
pytest tests/test_foo.py  # Run specific test file
```

---

## Documentation Files

### Analysis of Key Docs

| File | Path | Purpose | Up-to-Date? | Preserve? |
|------|------|---------|------------|-----------|
| **PROJECT_OVERVIEW.md** | `docs/PROJECT_OVERVIEW.md` | Complete architecture + entry points | ✅ Yes | ✅ Rewrite for new architecture |
| **DATABASE_SCHEMA.md** | `docs/DATABASE_SCHEMA.md` | SQLite schema, pragma settings | ✅ Yes | ✅ Port to rewrite |
| **API_REFERENCE.md** | `docs/API_REFERENCE.md` | REST/MQTT endpoints, payloads | ✅ Yes | ✅ Update for new endpoints |
| **MQTT_LOCAL_BROKER_SETUP.md** | `docs/MQTT_LOCAL_BROKER_SETUP.md` | Broker config (mosquitto) | ✅ Yes | ✅ Port configurations |
| **EVENT_A.md, EVENT_B.md, etc.** | `docs/events/EVENT_*.md` | Algorithm specifications | ✅ Yes | ✅ Core logic preserved |
| **MODBUS_SIMULATOR_WEB_TESTING.md** | `docs/MODBUS_SIMULATOR_WEB_TESTING.md` | Legacy Modbus testing | ⚠️ Optional | ❌ Archive (not in rewrite scope) |
| **RASPBERRY_PI_OPTIMIZATION.md** | `docs/guides/RASPBERRY_PI_OPTIMIZATION.md` | ARM tuning | ✅ Yes | ✅ Port best practices |

---

## Deployment Flow — Current System

### As-Is: Manual Deployment (Current Production)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. CLONE REPOSITORY                                                       │
└─────────────────────────────────────────────────────────────────────────┘
$ git clone https://github.com/embedsquare/hammer.git
$ cd hammer

┌─────────────────────────────────────────────────────────────────────────┐
│ 2. INSTALL DEPENDENCIES                                                   │
└─────────────────────────────────────────────────────────────────────────┘
$ sudo apt update
$ sudo apt install -y python3 python3-pip
$ pip3 install -r requirements.txt      # Flask, MQTT, Modbus
$ pip3 install gunicorn                 # Separate from requirements.txt ⚠️

┌─────────────────────────────────────────────────────────────────────────┐
│ 3. HARDWARE / PERMISSIONS SETUP (if using USB-CAN)                        │
└─────────────────────────────────────────────────────────────────────────┘
$ ./install.sh
  → Detects CPU arch
  → Finds correct libcontrolcan.so
  → Creates /etc/udev/rules.d/99-usb-can.rules
  → Adds user to plugdev group
  → Requires logout/login

┌─────────────────────────────────────────────────────────────────────────┐
│ 4. CONFIGURE ENVIRONMENT                                                  │
└─────────────────────────────────────────────────────────────────────────┘
Edit .env (or set shell env vars):
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=your-email@gmail.com
  SMTP_PASS=your-app-password      # 16-char app password from Gmail
  ALLOWED_EMAILS_PATH=emails.txt

Edit emails.txt:
  atharva@embedsquare.com
  cbidap@embedsquare.com
  ...

┌─────────────────────────────────────────────────────────────────────────┐
│ 5. ENSURE EXTERNAL SSD IS MOUNTED                                          │
└─────────────────────────────────────────────────────────────────────────┘
$ mkdir -p /media/embedsquare/PI_DATABASE/mqtt_database
$ mount /dev/sda1 /media/embedsquare/PI_DATABASE    # Exact path critical
$ sqlite3 /media/embedsquare/PI_DATABASE/mqtt_database/mqtt_database.db ".tables"

┌─────────────────────────────────────────────────────────────────────────┐
│ 6. START MQTT BROKER (if not remote)                                      │
└─────────────────────────────────────────────────────────────────────────┘
$ sudo apt install -y mosquitto mosquitto-clients
$ sudo systemctl start mosquitto
$ sudo systemctl enable mosquitto

┌─────────────────────────────────────────────────────────────────────────┐
│ 7. START APPLICATION                                                      │
└─────────────────────────────────────────────────────────────────────────┘
$ cd /home/embed/hammer
$ ./run.sh         # Launches gunicorn on port 8080
              OR
$ ./run.sh --dev   # Flask dev server with hot reload

Monitor logs:
  tail -f logs/sensor.log         (if SENSOR_LOG_ENABLED=1)
  tail -f logs/event_a_avg.log    (event detection logs)

┌─────────────────────────────────────────────────────────────────────────┐
│ 8. VERIFY & ACCESS                                                        │
└─────────────────────────────────────────────────────────────────────────┘
$ curl http://localhost:8080/
$ open http://192.168.1.115:8080  (in browser)

Login:
  Username: admin
  Password: admin
  OR
  Email: atharva@embedsquare.com → Get OTP via SMTP

┌─────────────────────────────────────────────────────────────────────────┐
│ 9. STOP APPLICATION                                                       │
└─────────────────────────────────────────────────────────────────────────┘
$ ./stop.sh         # Sends SIGTERM to gunicorn; waits 5s then SIGKILL
                    # Returns exit code 0 if stopped, 1 if failed
```

### To-Be: Systemd Unit + .deb Package (Rewrite Target)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. INSTALL .DEB PACKAGE                                                   │
└─────────────────────────────────────────────────────────────────────────┘
$ sudo apt update
$ sudo apt install ./hermes-sensor-dashboard-1.0.deb

  Contents:
    /opt/hermes/                              # Application directory
      ├── web_server.py
      ├── wsgi.py
      ├── requirements.txt
      ├── static/
      └── templates/
    /etc/hermes/
      ├── hermes-sensor-dashboard.conf        # Main config
      ├── mqtt-config.yml                     # MQTT broker settings
      ├── allowed-emails.txt                  # Email allowlist
      └── logging.conf                        # Log format
    /etc/systemd/system/
      └── hermes-sensor-dashboard.service     # Systemd unit
    /var/lib/hermes/
      └── mqtt_database.db                    # SQLite data directory
    /var/log/hermes/
      └── sensor-dashboard.log                # Application logs

┌─────────────────────────────────────────────────────────────────────────┐
│ 2. CONFIGURE (one-time, survives updates)                                 │
└─────────────────────────────────────────────────────────────────────────┘
Edit /etc/hermes/hermes-sensor-dashboard.conf:
  [smtp]
  host=smtp.gmail.com
  port=587
  user=your-email@gmail.com
  password=your-app-password      # Read from /etc/hermes/secrets.env
                                  # with mode 0600, owner root:root

Edit /etc/hermes/allowed-emails.txt:
  atharva@embedsquare.com
  ...

┌─────────────────────────────────────────────────────────────────────────┐
│ 3. START SERVICE                                                          │
└─────────────────────────────────────────────────────────────────────────┘
$ sudo systemctl start hermes-sensor-dashboard
$ sudo systemctl enable hermes-sensor-dashboard
$ sudo systemctl status hermes-sensor-dashboard

Logs:
  $ journalctl -u hermes-sensor-dashboard -f
  $ sudo tail -f /var/log/hermes/sensor-dashboard.log

┌─────────────────────────────────────────────────────────────────────────┐
│ 4. VERIFY                                                                 │
└─────────────────────────────────────────────────────────────────────────┘
$ curl http://localhost:8080/
$ sudo systemctl status hermes-sensor-dashboard

┌─────────────────────────────────────────────────────────────────────────┐
│ 5. STOP / RESTART                                                         │
└─────────────────────────────────────────────────────────────────────────┘
$ sudo systemctl stop hermes-sensor-dashboard
$ sudo systemctl restart hermes-sensor-dashboard

Systemd handles:
  - Graceful shutdown (ExecStop timeout)
  - Auto-restart on crash (Restart=on-failure)
  - Dependency ordering (Before=, After=)
  - Logging (stdout/stderr → journalctl)
  - Socket activation (optional future)

┌─────────────────────────────────────────────────────────────────────────┐
│ 6. UPDATE                                                                 │
└─────────────────────────────────────────────────────────────────────────┘
$ sudo apt update
$ sudo apt install --only-upgrade hermes-sensor-dashboard

  .deb Pre-install hook:
    $ systemctl stop hermes-sensor-dashboard
    $ systemctl mask hermes-sensor-dashboard

  .deb Post-install hook:
    $ systemctl unmask hermes-sensor-dashboard
    $ systemctl start hermes-sensor-dashboard
    $ systemctl status hermes-sensor-dashboard
```

---

## Secrets Inventory

### Current Exposed Credentials

| Secret | Location | Exposure | Status | Action |
|--------|----------|----------|--------|--------|
| **Gmail App Password** | `.env` (line 4) | Repository (git history) | 🚨 **CRITICAL** | Revoke immediately |
| **SMTP User** | `.env` (line 3) | Repository | ⚠️ Medium | Migrate to secure store |
| **Email Allowlist** | `emails.txt` | Repository | ✅ Low | OK (PII acceptable in this context) |
| **MQTT Broker Creds** | `web_server.py` (hardcoded) | Source code | ⚠️ Medium | Extract to `.env` |
| **Session Secret Key** | `web_server.py` (hardcoded as `"YOUR_SECRET_KEY"`) | Source code | 🚨 **CRITICAL** | Generate random, store in secrets |

### Pre-Rewrite Rotation Required

```bash
# 1. Gmail account: Revoke the leaked app password
#    → https://myaccount.google.com/apppasswords
#    → Select "Mail" and "Windows/Linux/Custom app" → Delete

# 2. Generate new Gmail App Password
#    → Same URL, create new password

# 3. Encrypt in production environment
#    Option A (systemd):
      /etc/default/hermes-sensor-dashboard (mode 0600)
      SMTP_PASS=<new-password>
      
    Option B (.deb):
      Include in conffile /etc/hermes/secrets.env (mode 0600, owned by root:root)
      source /etc/hermes/secrets.env before starting service

# 4. Generate session secret
#    python3 -c "import os; print(os.urandom(32).hex())"
#    Store in /etc/hermes/secrets.env or systemd EnvironmentFile

# 5. Verify no credentials in code
#    git log --full-history --all -- .env
#    git log --full-history --all -- wsgi.py
#    # If found, use git-filter-repo to scrub history before public release
```

### Post-Rewrite Security Model

```
┌──────────────────────────────────────────────────────────┐
│ REWRITE: Secrets Management                              │
├──────────────────────────────────────────────────────────┤
│ Option 1: systemd EnvironmentFile (recommended)           │
│  /etc/hermes/secrets.env (mode 0600, owner root)          │
│  → Sourced by /etc/systemd/system/...service              │
│  → Never logged, never committed                          │
│                                                            │
│ Option 2: .deb conffile (if distributed)                  │
│  /etc/hermes/hermes.conf                                  │
│  → Marked as conffile in dpkg; survives upgrades          │
│  → Manual: put SMTP_PASS in separate /etc/hermes/secrets  │
│                                                            │
│ Option 3: Environment variable injection (CI/CD)          │
│  $ hermes_start.sh                                        │
│    export SMTP_PASS=$(aws ssm get-parameter ...)          │
│    systemctl start hermes-sensor-dashboard                │
│                                                            │
│ Option 4: Kubernetes Secret + ConfigMap                   │
│  apiVersion: v1                                           │
│  kind: Secret                                             │
│  metadata:                                                │
│    name: hermes-secrets                                   │
│  data:                                                    │
│    SMTP_PASS: base64(...)  # Encrypted at rest            │
└──────────────────────────────────────────────────────────┘
```

---

## Preservation Plan

### Which Operational Files to Port

| File | Current | Target | Notes |
|------|---------|--------|-------|
| **run.sh** | Shell script | Systemd unit + wrapper | Extract gunicorn command, convert to `ExecStart=` |
| **stop.sh** | Shell script | Systemd unit | Use `ExecStop=` with `TimeoutStopSec=10` |
| **install.sh** | Bash setup | .deb postinst + udev rules | Package architecture detection, move to dpkg |
| **wsgi.py** | WSGI module | ✅ Port 1:1 | No changes needed |
| **web_server.py** | Flask app | ✅ Port 1:1 (initially) | Refactor to blueprints/factory in Phase 1 |
| **requirements.txt** | Pip list | debian/control | Convert `>=` to pinned versions for `.deb` |
| **.env** | Config file | `/etc/hermes/hermes.conf` | Migrate to systemd EnvironmentFile or conffile |
| **emails.txt** | Email allowlist | Database + API | Move to `allowed_emails` table; API endpoint to manage |
| **Dockerfile** | Container | Multi-stage Dockerfile (new) | Switch to gunicorn, add health check, slim base |
| **pytest.ini** | Test config | ✅ Port 1:1 | Keep for CI/CD |
| **.gitignore** | VCS config | Update | Add `.deb` build artifacts, debian/ dir |

### New Files to Create for Rewrite

```
hermes-sensor-dashboard/
├── debian/                                  # .deb packaging
│   ├── control                              # Package metadata, dependencies
│   ├── postinst                             # Post-install: systemd enable, udev, mkdir
│   ├── prerm                                # Pre-remove: systemd stop
│   ├── postrm                               # Post-remove: cleanup
│   ├── rules                                # dh_auto_* targets
│   ├── install                              # File install rules
│   └── hermes-sensor-dashboard.service      # Systemd unit
├── etc/hermes/                              # Config templates
│   ├── hermes-sensor-dashboard.conf         # Main config (INI or YAML)
│   ├── mqtt-config.yml                      # MQTT broker settings
│   ├── allowed-emails.txt                   # Email allowlist (can be DB eventually)
│   └── secrets.env.example                  # Template for /etc/hermes/secrets.env
├── systemd/                                 # Systemd integration
│   ├── hermes-sensor-dashboard.service      # Service unit
│   ├── hermes-sensor-dashboard.socket       # Optional: socket activation
│   └── hermes-sensor-dashboard.timer        # Optional: scheduled restart
├── scripts/                                 # Helper scripts
│   ├── migrate-from-old.sh                  # Data migration (if changing DB schema)
│   └── health-check.sh                      # Systemd HealthCheck or cron job
└── DEPLOYMENT.md                            # Step-by-step .deb install guide
```

### Systemd Unit File Template

```ini
# /etc/systemd/system/hermes-sensor-dashboard.service

[Unit]
Description=HERMES Sensor Dashboard — Flask MQTT Sensor Monitoring
Documentation=http://localhost:8080
Wants=network-online.target mosquitto.service
After=network-online.target mosquitto.service
StartLimitIntervalSec=0
StartLimitBurst=0

[Service]
Type=notify
WorkingDirectory=/opt/hermes/
User=hermes
Group=hermes
ProtectSystem=strict
ProtectHome=yes
NoNewPrivileges=true

# Environment
EnvironmentFile=/etc/hermes/hermes-sensor-dashboard.conf
EnvironmentFile=-/etc/hermes/secrets.env           # Optional; credentials
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONDONTWRITEBYTECODE=1"
Environment="PYTHONPYCACHEPREFIX=/tmp/hermes-pycache"

# Startup
ExecStartPre=/usr/bin/install -d /var/lib/hermes /var/log/hermes
ExecStartPre=/usr/bin/chown hermes:hermes /var/lib/hermes /var/log/hermes
ExecStart=/usr/bin/gunicorn \
    --worker-class=gthread \
    --workers=1 \
    --threads=8 \
    --timeout=120 \
    --graceful-timeout=0 \
    --bind=0.0.0.0:8080 \
    --access-logfile=/var/log/hermes/access.log \
    --error-logfile=/var/log/hermes/error.log \
    --log-level=info \
    --max-requests=1000 \
    --max-requests-jitter=100 \
    wsgi:app

# Restart policy
Restart=on-failure
RestartSec=5
StartLimitInterval=300
StartLimitBurst=3

# Shutdown
KillMode=mixed
KillSignal=SIGTERM
ExecStop=/bin/kill -TERM $MAINPID
TimeoutStopSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hermes-sensor-dashboard

# Security
PrivateTmp=yes
ProtectClock=yes
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes

[Install]
WantedBy=multi-user.target
```

### Migration Checklist

```
Phase 0: Pre-Rewrite (NOW)
  ☐ Revoke leaked Gmail App Password
  ☐ Generate new Gmail App Password
  ☐ Document all environment variables in use
  ☐ Document all config files and their semantics
  ☐ Create migration guide from old system

Phase 1: Build .deb Package
  ☐ Create debian/ directory
  ☐ Write debian/control (dependencies, description)
  ☐ Write debian/hermes-sensor-dashboard.service
  ☐ Write debian/postinst (mkdir, chown, systemd daemon-reload)
  ☐ Test: debuild -us -uc → generates .deb
  ☐ Test: sudo apt install ./hermes-sensor-dashboard_1.0_armhf.deb
  ☐ Test: sudo systemctl start hermes-sensor-dashboard
  ☐ Test: curl http://localhost:8080/

Phase 2: Data Migration
  ☐ Copy existing /media/embedsquare/PI_DATABASE/ → /var/lib/hermes/
  ☐ Verify SQLite integrity: sqlite3 ...db ".integrity_check"
  ☐ Test event queries still work
  ☐ Backup old database: cp mqtt_database.db mqtt_database.db.backup

Phase 3: Config Migration
  ☐ Migrate /home/embed/hammer/.env → /etc/hermes/secrets.env (mode 0600)
  ☐ Migrate /home/embed/hammer/emails.txt → /etc/hermes/allowed-emails.txt
  ☐ Test systemd EnvironmentFile loading: systemctl show -p EnvironmentFiles

Phase 4: Cutover
  ☐ Stop old system: ./stop.sh
  ☐ Verify old PID is gone: lsof -ti:8080
  ☐ Start new system: sudo systemctl start hermes-sensor-dashboard
  ☐ Verify service is running: sudo systemctl status hermes-sensor-dashboard
  ☐ Verify web UI: curl http://localhost:8080/
  ☐ Verify MQTT is flowing: mosquitto_sub -t stm32/adc
  ☐ Verify events are detected: tail -f /var/log/hermes/sensor-dashboard.log
  ☐ Test graceful shutdown: sudo systemctl stop hermes-sensor-dashboard

Phase 5: Cleanup
  ☐ Remove old repo directory (backup first)
  ☐ Verify /etc/systemd/system/ contains only hermes unit
  ☐ Document in /opt/hermes/DEPLOYMENT.md
```

---

## Database & Persistence

### SQLite Location & Configuration

**Primary (SSD):** `/media/embedsquare/PI_DATABASE/mqtt_database/mqtt_database.db`

**Fallback:** `src/database/mqtt_database.db` (if SSD not mounted)

**Post-Rewrite:** `/var/lib/hermes/mqtt_database.db` (systemd-compliant)

### Database Schema Overview

| Table | Purpose | Size | Notes |
|-------|---------|------|-------|
| `devices` | Device registry (up to 20) | ~1 KB | Active, `is_active` flag |
| `events` | Event records (wide format) | Grows | One row per trigger window; includes all 12 sensors × 4 types |
| `event_config_type_a` | Global Type A settings | ~100 B | Always 1 row |
| `event_config_type_a_per_sensor` | Per-sensor Type A overrides | ~5 KB | 0–240 rows |
| `system_config` | Auto-restart schedule | ~50 B | Always 1 row |
| `users` | Login credentials | ~1 KB | Default: admin/admin |
| `app_config` | Key-value store | ~10 KB | 117 tunable constants |

### Critical PRAGMAs (Performance Tuning)

```sql
PRAGMA journal_mode = WAL;              -- Write-Ahead Logging (crash recovery + concurrency)
PRAGMA synchronous = OFF;                -- Max speed (safe with WAL)
PRAGMA cache_size = -64000;              -- 64 MB in-memory cache
PRAGMA temp_store = MEMORY;              -- Temp tables in RAM
PRAGMA foreign_keys = ON;                -- Referential integrity
PRAGMA busy_timeout = 60000;             -- Wait up to 60s on lock
PRAGMA wal_autocheckpoint = 500;         -- Checkpoint every ~2 MB WAL
PRAGMA journal_size_limit = 4194304;     -- Max 4 MB WAL file
```

### Backup & Recovery

**Current System (Manual):**

```bash
# Backup
$ cp /media/embedsquare/PI_DATABASE/mqtt_database/mqtt_database.db /path/to/backup/mqtt_database.db.`date +%Y%m%d`

# Recovery
$ sqlite3 /media/embedsquare/PI_DATABASE/mqtt_database/mqtt_database.db
sqlite> PRAGMA integrity_check;
sqlite> PRAGMA wal_checkpoint(RESTART);
```

**Post-Rewrite (Systemd):**

```bash
# Should add: daily backup cron job
0 2 * * * /usr/bin/sqlite3 /var/lib/hermes/mqtt_database.db \
    ".backup '/var/lib/hermes/backups/mqtt_database-$(date +\%Y\%m\%d).db'"
```

---

## Systemd Integration (None Currently)

### Current Gaps

| Requirement | Current | Status |
|-------------|---------|--------|
| **Service file** | `run.sh` + manual start | ❌ No systemd unit |
| **Auto-restart on crash** | — | ❌ No restart policy |
| **Graceful shutdown** | `stop.sh` with timeout | ⚠️ Manual script, not systemd |
| **Log aggregation** | Files in `logs/` | ⚠️ Not journalctl |
| **Dependency ordering** | None (manual) | ❌ No Before=/After= |
| **Resource limits** | None | ❌ No MemoryLimit, CPUQuota |
| **Health checks** | None | ❌ No ExecHealthCheck |
| **Socket activation** | None | ❌ Not implemented |
| **Environment isolation** | None | ❌ No ProtectSystem, PrivateTmp |

### Post-Rewrite Requirements

**Must Implement:**

```ini
[Service]
Type=notify                               # Use systemd notification protocol
Restart=on-failure                        # Auto-restart on crash
RestartSec=5                              # 5s delay before restart
StartLimitBurst=3                         # Max 3 restarts in 300s
StartLimitInterval=300

ExecStart=/usr/bin/gunicorn --worker-class=gthread --bind=0.0.0.0:8080 wsgi:app
ExecStop=/usr/bin/kill -TERM $MAINPID    # Or custom stop script
TimeoutStopSec=10                         # Force kill after 10s

StandardOutput=journal                    # Logs to systemd journal
StandardError=journal
SyslogIdentifier=hermes-sensor-dashboard
```

**Should Implement:**

```ini
ProtectSystem=strict                      # Read-only filesystem (except /var/lib/hermes)
ProtectHome=yes                           # No access to /home
PrivateTmp=yes                            # Isolated /tmp
NoNewPrivileges=true                      # No privilege escalation
User=hermes                               # Run as dedicated user
Group=hermes
```

**May Implement (Future):**

```ini
# Health check (systemd 250+)
ExecHealthCheck=/usr/local/bin/hermes-healthcheck.sh

# Socket activation (optional optimization)
[Socket]
ListenStream=8080
Accept=no

# Scheduled restart (daily maintenance window)
OnCalendar=*-*-* 02:00:00
Unit=hermes-sensor-dashboard.service
```

---

## Summary: Files to Preserve vs Replace

### Preserve (Port 1:1)

| File | Reason |
|------|--------|
| `wsgi.py` | Entry point; no framework dependencies |
| `requirements.txt` | Dependency list (update versions) |
| `pytest.ini` | Test configuration |
| `README.md` | User-facing documentation |
| Database schema (DDL) | Core data model |

### Adapt (Rewrite/Migrate)

| File | Target | Reason |
|------|--------|--------|
| `run.sh` | Systemd `ExecStart=` | Decouple from shell; let systemd manage lifecycle |
| `stop.sh` | Systemd `ExecStop=` | Decouple from shell |
| `install.sh` | `debian/postinst` | Package manager handles installation |
| `.env` | `/etc/hermes/secrets.env` + systemd EnvironmentFile | Secrets management best practice |
| `emails.txt` | Database table + API endpoint | Better scalability, dynamic updates |
| `Dockerfile` | Updated (gunicorn, health check) | Improve for production use |
| `.gitignore` | Update (add debian/, build artifacts) | Track new directories |

### Drop (Not in Rewrite Scope)

| File | Reason |
|------|--------|
| `configure_all_sensors.sh` | Demo/manual ops; replace with API |
| `trigger_s10_demo.sh` | Interactive debugging; replace with test suite |
| `test_all_sensors_mqtt.sh` | Legacy testing; integrate into pytest |
| `validate_realtime.sh` | Ad-hoc validation; replace with health checks |

---

## References & Appendices

### File Manifest (Complete)

**Operational Files (Non-Python):**

```
/home/embed/hammer/
├── .dockerignore
├── .env                           ⚠️ Contains exposed SMTP credentials
├── .gitignore
├── configure_all_sensors.sh
├── Dockerfile
├── emails.txt
├── install.sh
├── pytest.ini
├── requirements.txt
├── run.sh                         PRIMARY ENTRY POINT
├── stop.sh                        SHUTDOWN SCRIPT
├── test_all_sensors_mqtt.sh
├── trigger_s10_demo.sh
├── validate_realtime.sh
└── wsgi.py                        GUNICORN ENTRY POINT
```

**Key Python (Not Detailed Here, But Note):**

```
/home/embed/hammer/
├── web_server.py                  MAIN APPLICATION (3688 lines)
├── src/
│   ├── app/
│   │   ├── __init__.py           (factory pattern, alternative entry)
│   │   ├── services.py           (global shared state)
│   │   ├── live_data.py          (in-memory buffers for SSE)
│   │   └── routes/               (blueprints: auth, devices, events, etc.)
│   ├── database/
│   │   ├── mqtt_database.py      (SQLite schema + queries)
│   │   └── mqtt_config.py        (config DB)
│   ├── detection/
│   │   └── event_detector.py     (Types A/B/C/D logic)
│   ├── mqtt/
│   │   ├── client.py             (MQTT connection)
│   │   └── parser.py             (payload parsing)
│   └── utils/
│       ├── auto_restart.py       (daily restart monitor)
│       └── logging_config.py     (SENSOR env var parsing)
└── tests/                        (pytest suite)
```

**Documentation:**

```
/home/embed/hammer/docs/
├── PROJECT_OVERVIEW.md                (Complete architecture)
├── DATABASE_SCHEMA.md                 (SQLite tables, PRAGMAs)
├── API_REFERENCE.md                   (REST endpoints)
├── MQTT_LOCAL_BROKER_SETUP.md        (Broker config)
├── events/
│   ├── EVENT_A.md                     (Variance detection)
│   ├── EVENT_B.md                     (Post-window deviation)
│   ├── EVENT_C.md                     (Range check)
│   └── EVENT_D.md                     (Two-stage average)
├── architecture/
│   ├── SYSTEM_ARCHITECTURE.md         (8-layer model)
│   ├── CORE_ARCHITECTURE.md
│   └── BUSINESS_LOGIC_ARCHITECTURE.md
└── guides/
    ├── RASPBERRY_PI_OPTIMIZATION.md   (ARM tuning)
    ├── DEVICE_PARAMETERS_SPECIFICATION.md
    └── SENSOR_TEST_CONFIGS.md
```

### Environment Variable Glossary

```bash
# Build / Runtime Python
PYTHONPYCACHEPREFIX=               Bytecode cache location (set by run.sh)
PYTHONUNBUFFERED=                  For systemd: no buffering
PYTHONDONTWRITEBYTECODE=           For systemd: no .pyc files

# Application Configuration
DEV_HOT_RELOAD=1                   Flask auto-reload (dev only)
SENSOR_LOG_ENABLED=1               Extra sensor data logging
SENSOR="1A,3B"                     Filter by sensor/type
LATENCY_LOG_SECONDS=30             Enable latency profiling
MQTT_EVENT_DEBUG=1                 Verbose MQTT logs

# MQTT / Networking
MQTT_BROKER=localhost              Broker hostname/IP (hardcoded, not in .env)
MQTT_PORT=1883                     Broker port (hardcoded)
MQTT_BASE_TOPIC=canbus/sensors/data   Subscription topic (hardcoded)

# Email / Authentication
SMTP_HOST=smtp.gmail.com           Email server
SMTP_PORT=587                      SMTP port
SMTP_USER=...@gmail.com            Sender email
SMTP_PASS='...'                    ⚠️ APP PASSWORD (EXPOSED)
SMTP_FROM=...@gmail.com            From header
ALLOWED_EMAILS_PATH=emails.txt     Email allowlist file

# Database
MQTT_DATABASE_PATH=...             SQLite database (hardcoded)
LIVE_BUFFER_MAX_SAMPLES=2000       In-memory buffer size
LIVE_STREAM_INTERVAL_S=0.1         SSE push frequency
```

### Deployment Checklist (Old → New System)

```
1. Pre-Deployment
   [ ] Review all secrets in use (SMTP, MQTT, API keys)
   [ ] Prepare new .deb package
   [ ] Test .deb on staging Raspberry Pi
   [ ] Backup existing database
   [ ] Backup /home/embed/hammer directory

2. Deploy
   [ ] Stop old system: cd /home/embed/hammer && ./stop.sh
   [ ] Verify no process on port 8080: lsof -i:8080
   [ ] Install new .deb: apt install ./hermes-sensor-dashboard_1.0_armhf.deb
   [ ] Verify systemd unit: systemctl list-unit-files | grep hermes
   [ ] Start new service: systemctl start hermes-sensor-dashboard
   [ ] Verify running: systemctl status hermes-sensor-dashboard

3. Validation
   [ ] Web UI accessible: curl http://localhost:8080/
   [ ] Login works: Test admin/admin
   [ ] MQTT flowing: mosquitto_sub -t stm32/adc
   [ ] Events detected: grep -i "event" /var/log/hermes/sensor-dashboard.log
   [ ] Database accessible: sqlite3 /var/lib/hermes/mqtt_database.db "SELECT COUNT(*) FROM events;"

4. Post-Deployment
   [ ] Document any issues
   [ ] Update runbooks
   [ ] Remove old installation: rm -rf /home/embed/hammer (after long soak test)
   [ ] Monitor logs for 24h: journalctl -u hermes-sensor-dashboard -f
```

---

## Document Metadata

| Field | Value |
|-------|-------|
| **Version** | 1.0 (Phase 0.5) |
| **Last Updated** | 2026-04-23 |
| **Author** | HERMES Ops Team |
| **Audience** | DevOps, Systems Architecture, Rewrite Team |
| **Scope** | Operational files only (no Python app logic) |
| **Related Docs** | PROJECT_OVERVIEW.md, DATABASE_SCHEMA.md, DEPLOYMENT.md (new) |
| **Status** | COMPLETE |

---

**END OF REFERENCE DOCUMENT**

---

*This document serves as the canonical record of all operational and deployment semantics in the legacy HERMES sensor dashboard system. It captures the exact behavior of `run.sh`, `stop.sh`, `install.sh`, `.env`, `wsgi.py`, and related configuration files before the complete rewrite to .deb packaging with systemd integration. Use this as the contract for the rewrite's operational layer.*
