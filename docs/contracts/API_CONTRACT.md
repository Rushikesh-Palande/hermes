# API_CONTRACT.md

## 1. Overview

The HERMES dashboard has a dual-registration HTTP API surface. All routes are mounted on a single Flask app created in `/home/embed/hammer/web_server.py:92` (`app = Flask(__name__)`). Routes reach that app through two paths:

1. **Blueprint registration** — `from src.app.routes import register_blueprints; register_blueprints(app)` is called at `/home/embed/hammer/web_server.py:100-101`. This registers nine blueprints (`auth_bp`, `dashboard_bp`, `devices_bp`, `events_bp`, `sensors_bp`, `mqtt_bp`, `algorithms_bp`, `system_bp`, `offsets_bp`) defined in `/home/embed/hammer/src/app/routes/__init__.py:4-23`.

2. **Inline `@app.route` decorators** — Module-level route decorators in `web_server.py` between lines ~1066 and ~3946. These execute **after** `register_blueprints(app)` because decorator evaluation is deferred until the module finishes importing the blueprint modules at line 101.

### Registration order and collisions

Confirmed from `/home/embed/hammer/src/app/routes/__init__.py` and `/home/embed/hammer/web_server.py:100-101`:

- `register_blueprints(app)` executes at line 101 during import — blueprints register first.
- Inline `@app.route` decorators at lines 1066 onward execute during the same module import, immediately after line 101 — they register second.

Because blueprint view functions use namespaced endpoints (e.g. `devices.list_devices_api`) while inline view functions use bare endpoints (e.g. `list_devices_api`), Flask does NOT raise `AssertionError: View function mapping is overwriting an existing endpoint` on collisions. Both rules sit in `app.url_map.iter_rules()` side-by-side.

Flask's `MapAdapter.match()` walks rules in `url_map._rules` in insertion order, and the first match wins. **Therefore blueprints WIN on every path+method collision** — the inline `@app.route` handlers that duplicate blueprint paths are dead code and never execute. The inline routes remain reachable only under `url_for()` lookups by bare endpoint name (e.g. `url_for('list_devices_api')`), but those reverse-lookups are not used for HTTP dispatch.

The one exception: `url_for('login_page')` inside `web_server.py:365` (the inline `login_required` decorator) — this resolves to the inline `/login` because both blueprint and inline register the same endpoint name `login_page` and Flask's last-wins endpoint-mapping rule applies to `url_for`, not matching. The blueprint's `auth.login_page` is reachable via `url_for('auth.login_page')` used in `src/app/routes/auth.py:89`.

### Auth pattern

Two parallel `login_required` decorators exist:

- `/home/embed/hammer/src/app/routes/auth.py:85-91` — redirects to `url_for('auth.login_page')`.
- `/home/embed/hammer/web_server.py:361-367` — redirects to `url_for('login_page')`.

Both check `'logged_in' not in session` on the Flask session cookie. There is no role/admin distinction; any authenticated session passes.

### Default content type

Every non-page endpoint returns `application/json` via `flask.jsonify(...)`. Pages return `text/html`. The SSE endpoint at `/api/live_stream` returns `text/event-stream`.

---

## 2. Routes by domain

### 2.1 Pages (HTML responses, not JSON)

| Path | Method | Auth | Blueprint (winner) | Inline duplicate | Notes |
|---|---|---|---|---|---|
| `/` | GET | public | `dashboard.py:8` (`index`) renders `index.html` | `web_server.py:1190` | Landing page |
| `/dashboard` | GET | login | `dashboard.py:13` → `redirect(url_for('dashboard.device_config'))` | `web_server.py:1196` renders `device_detail.html` | Blueprint redirects; inline is dead |
| `/device-config` | GET | login | `dashboard.py:22` renders `device_config.html` | `web_server.py:1206` | Same template |
| `/device-config/<int:device_id>` | GET | login | `dashboard.py:28` renders `device_detail.html` with `device_id` | `web_server.py:1213` | Same |
| `/event-config` | GET | login | `dashboard.py:34` renders `event_config.html` | `web_server.py:1220` | Same |
| `/system-config` | GET | login | `system.py:19` renders `system_config.html` | `web_server.py:1227` | Same |
| `/app-config` | GET | login | `system.py:26` renders `app_config.html` | (no inline) | |
| `/ttl-config` | GET | login | `dashboard.py:40` renders `ttl_config.html` | `web_server.py:1234` redirects to `event_config` (dead — blueprint wins) | Blueprint serves a real page; inline would redirect |
| `/avg-type-b` | GET | login | `dashboard.py:47` redirects to `event_config_page` | `web_server.py:1241` same | |
| `/avg-type-c` | GET | login | `dashboard.py:53` redirects to `event_config_page` | `web_server.py:1248` | |
| `/avg-type-d` | GET | login | `dashboard.py:59` redirects to `event_config_page` | `web_server.py:1255` | |
| `/offset-config` | GET | login | `dashboard.py:65` renders `sensor_offsets.html` | (no inline) | |
| `/login` | GET | public | `auth.py:94` renders `login.html` | `web_server.py:1066` same | Identical behavior |

No `/dashboard3`, `/sensor-offsets`, or `/mqtt_realtime_chart` route is defined in code — if the UI hard-codes those paths, they 404. `grep` across `templates/` and `static/` shows no references to `/dashboard3` or `/mqtt_realtime_chart`.

---

### 2.2 Authentication

All auth routes exist in both blueprint and inline form and are functionally identical on request/response schemas — blueprints win on dispatch.

#### POST `/api/auth/login`

- **Winner:** `src/app/routes/auth.py:99-115`. Dead duplicate: `web_server.py:1072-1093`.
- **Auth:** public.
- **Request body (JSON):** `{ "username": str, "password": str }` — both required, stripped.
- **Success (200):** `{ "success": true, "message": "Login successful" }`. Sets `session['logged_in']=True`, `session['username']=username`.
- **Errors:** `400 {success:false, error:"Username and password are required"}`, `401 {success:false, error:"Invalid username or password"}`, `500 {success:false, error:"Server error"}`.
- **Auth backend:** `services.db.authenticate_user(username, password)` (blueprint) or `db.authenticate_user(...)` (inline). Returns bool.

#### POST `/api/auth/logout`

- **Winner:** `src/app/routes/auth.py:197-206`. Dead duplicate: `web_server.py:1175-1185`.
- **Auth:** no decorator — callable even if not logged in (no-op if session empty).
- **Request body:** none required.
- **Response:** `{ "success": true, "message": "Logged out successfully" }`. Calls `session.clear()`.

#### POST `/api/auth/otp/request`

- **Winner:** `src/app/routes/auth.py:118-155`. Dead duplicate: `web_server.py:1096-1133`.
- **Request (JSON):** `{ "email": str }` (required).
- **Validation:** email must be in `emails.txt` (path: `ALLOWED_EMAILS_PATH` env var, default `emails.txt`). File is loaded by `_load_allowed_emails()` (`auth.py:22-34`), lowercased, ignores `#` comments.
- **Rate limits:**
  - Per-email resend cooldown: `OTP_RESEND_SECONDS` (default 60s) via `_rate_limit_ok`.
  - Per-email hourly cap: `OTP_MAX_PER_HOUR` (default 5 per `otp_rate_limit_window_s`, default 3600s).
- **OTP generation:** `f"{secrets.randbelow(1000000):06d}"`, SHA256-hashed with a 16-hex salt, stored in `services.otp_store[email]` in-memory dict (see `auth.py:133`, `services.py` defines `otp_lock` and `otp_store`). NOT persisted to DB.
- **Expiry:** `OTP_EXPIRY_SECONDS` (default 300s).
- **Email:** SMTP via `_send_otp_email` (`auth.py:53-82`). Requires `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` env vars. Uses STARTTLS if supported.
- **Success:** `{ "success": true, "message": "OTP sent" }`.
- **Errors:** 400 missing/no-allowlist, 403 not authorized, 429 rate-limited, 500 send failure.

#### POST `/api/auth/otp/verify`

- **Winner:** `src/app/routes/auth.py:158-194`. Dead duplicate: `web_server.py:1136-1172`.
- **Request (JSON):** `{ "email": str, "otp": str }`.
- **Checks:** entry exists, not expired, `attempts < OTP_MAX_ATTEMPTS` (default 5), salted hash matches.
- **Success:** `{ "success": true, "message": "Login successful" }`. Sets `session['logged_in']=True`, `session['email']`, `session['username']`, `session['auth_method']='otp'`. Clears `otp_store[email]`.
- **Errors:** 400 missing fields / not-found / expired, 401 invalid OTP (increments attempts), 429 too many attempts, 500 exception.

#### No `/api/auth/status` endpoint exists. The UI infers logged-in state from the 302 to `/login` on any protected call.

---

### 2.3 Device CRUD

All eight `/api/devices/**` paths collide between blueprint and inline. Blueprints win on dispatch; inline handlers are dead code and reference undefined globals (e.g. `device_instances`, `_stop_device_poll_thread`, `STM32_DEVICE_ID`) that would raise `NameError` if ever reached — but they aren't reached. **The active contract is the blueprint.**

#### GET `/api/devices`

- **Winner:** `src/app/routes/devices.py:27-44`.
- **Auth:** no decorator on blueprint (inline also no decorator).
- **Response:** `{ success: true, devices: [...], count: int }`. Each device dict is the result of `services.device_manager.get_all_devices()` with `is_active` overlaid: `(now - services.device_last_data_ts[str(device_id)]) < 0.5`.
- **Blueprint response omits `max_devices`; inline `web_server.py:1507-1512` would include `max_devices: 20` — not reached.**
- **Caching:** 5 s via `services._devices_cache` (see `devices.py:10-24`).

#### POST `/api/devices`

- **Winner:** `src/app/routes/devices.py:47-71`.
- **Request:** `{ "device_name": str (required, stripped), "topic": str | null (optional) }`.
- **Note (contract divergence):** blueprint only reads `device_name` and `topic`; inline `web_server.py:1522-1611` reads `device_type` (default `'modbus_tcp'`), `model`, `hardware_version`, `firmware_version`, `ip_address`, `port`, `slave_id`, `register_start`, `register_count`, `modbus_scaling`, `sample_rate_hz`. These inline fields are **silently ignored** since the blueprint wins.
- **Success:** `{ success: true, device: {...}, message: "Device 'X' created successfully" }`. Invalidates device cache.
- **Errors:** 400 no name / create failed, 500 exception.

#### GET `/api/devices/<int:device_id>`

- **Winner:** `src/app/routes/devices.py:74-83`.
- **Response:** `{ success: true, device: {...} }` or `404 { success: false, error: "Device X not found" }`.

#### PUT `/api/devices/<int:device_id>`

- **Winner:** `src/app/routes/devices.py:86-102`.
- **Request:** any JSON object; passed verbatim as kwargs to `services.device_manager.update_device(device_id, **data)`.
- **Success:** `{ success: true, device: {updated}, message: "Device X updated successfully" }`. Invalidates cache.
- **Error:** 404 if device missing or update failed.

#### DELETE `/api/devices/<int:device_id>`

- **Winner:** `src/app/routes/devices.py:105-118`.
- **Response:** `{ success: true, message: "Device X 'name' deleted successfully" }`. 404 not-found, 500 delete-failed. No `is-running` check in blueprint (inline has no check either).

#### POST `/api/devices/<int:device_id>/initialize`

- **Winner:** `src/app/routes/devices.py:121-130`.
- **Behavior:** Just validates device exists, then returns `{ success: true, message: "Device X uses MQTT ingestion; no connection required" }`. Does NOT branch on `device_type`.
- **Status:** the inline version at `web_server.py:1729-1829` DID branch on `device_type` with a `dev_data['device_type']` KeyError risk for MQTT-only rows that lack the column — but that inline route is dead. The blueprint simply no-ops.
- **No body fields are read.** (Inline would read `force_reinit` — ignored.)

#### POST `/api/devices/<int:device_id>/start`

- **Winner:** `src/app/routes/devices.py:133-159`.
- **Behavior:** Validates device exists. Keys `event_detectors` by **string device_id** (`str(device_id)`). If no detector yet, creates `EventDetector(database=services.worker_db, device_id=device_id_str, device_manager=services.device_manager, worker_manager=services.worker_manager)` and calls `detector.start_detection()`.
- **Request body:** none consumed.
- **Response:** `{ success: true, message: "Device X started successfully" }`. 404 not-found, 500 exception (with traceback).

#### POST `/api/devices/<int:device_id>/stop`

- **Winner:** `src/app/routes/devices.py:162-178`.
- **Behavior:** If `str(device_id)` key exists in `services.event_detectors`, calls `.stop_detection()` and deletes the key.
- **Response:** `{ success: true, message: "Device X stopped successfully" }`.

#### GET `/api/devices/<int:device_id>/status`

- **Winner:** `src/app/routes/devices.py:181-200`.
- **Response:** `{ success: true, device_id: int, running: bool, topic: str|null, is_active: bool }` where `running = (str(device_id) in services.event_detectors)` and `is_active = (time.time() - services.device_last_data_ts[str(device_id)]) < 0.5`.
- **Note:** inline version at `web_server.py:2056-2110` includes `initialized`, `stats`, `sample_rate_hz` fields — these are missing from blueprint response. UI must tolerate missing keys.

---

### 2.4 Sensor / live data

#### GET `/api/sensor_data`

- **Winner:** `src/app/routes/sensors.py:47-90` (inline duplicate at `web_server.py:2792-2869`).
- **Auth:** `@login_required`.
- **Query params:**
  - `device_id` (required, string — used as dict key; matched against `str(device_id)` keys in `services.event_detectors`).
  - `sensor_ids` — comma-separated ints like `"1,3,7"` or `"all"` (default `all` → None, all sensors).
  - `max_samples` — int (default = `db.get_app_config_value('api_live_sensor_max_samples', 1000)`).
- **Backing store:** `services.event_detectors[device_id].get_sensor_data(sensor_ids=..., max_samples=...)`.
- **Response:** `{ success: true, data: { timestamps: [float s], sensors: {1:[...], 2:[...], ...}, ... } }`.
- **Errors:** 400 missing device_id or bad sensor_ids format, 404 no EventDetector for device.

#### GET `/api/live_sensor_data`

- **Winner:** `src/app/routes/sensors.py:93-163` (inline duplicate at `web_server.py:2893-3007`).
- **Auth:** `@login_required`.
- **Query params:** same as `/api/sensor_data`.
- **Backing store:** primary `services.live_data.get_data(...)`; fallback to `services.event_detectors[device_id].get_sensor_data(...)` if live buffer empty.
- **Response:** `{ success: true, data: { timestamps: [...], sensors: {...}, stats: { <sensor_id>: {...} } } }`. `stats` added from `detector.get_current_stats()` when EventDetector exists.
- **Side effect:** if `services.SENSOR_LOG_ENABLED`, appends sensor-1 samples to `logs/sensor.log`.
- **Errors:** 400 missing device_id / bad sensor_ids, 404 no live data and no detector.

#### GET `/api/live_stream` (SSE) — see §6 for detail.

- **Winner:** `src/app/routes/sensors.py:191-277` (inline duplicate at `web_server.py:3010-3108`).
- **Auth:** `@login_required`.
- **Query params:** `device_id` (required), `sensor_ids` (default `all`), `interval` (float s, default `services.LIVE_STREAM_INTERVAL_S`), `max_samples` (int, default `db.get_app_config_value('api_live_sensor_cap', 1500)`), `last_seq` (int, default 0).
- **Response:** `text/event-stream`, first frame is literal `retry: 2000\n\n`, then repeating `data: <json>\n\n`.
- **Headers:** `Cache-Control: no-cache`, `X-Accel-Buffering: no`.

#### GET `/api/stats`

- **Only inline:** `web_server.py:2123-2162`. No blueprint — this endpoint is active via the inline route.
- **Auth:** no decorator.
- **Query params:** `device_id` optional int — scopes MQTT counts to that device.
- **Response:** `{ total_tx: int, total_rx: int, source: "mqtt"|"device", uptime_seconds: int, counter_mode: {enabled: false} }`.
- **Logic:** Uses `mqtt_stats_lock` and `services.event_stats_lock`. `total_tx` is event publishes; `total_rx` is max of MQTT messages and LiveDataHub seq.

#### GET `/api/debug/variance_state`

- **Winner:** `src/app/routes/sensors.py:166-188` (inline duplicate `web_server.py:2872-2890`).
- **Auth:** `@login_required` on blueprint.
- **Query:** `device_id` (default `'1'`), `sensor_id` (int, default 1).
- **Response:** `{ sensor_id, initialized, window_count, running_sum, running_sum_sq, last_timestamp, window_deque_len }` or `{ error, available }` (200) if no detector.

#### GET `/api/db/frames` — NOT IMPLEMENTED

Referenced by `templates/device_detail.html:5490,5656` (`/api/db/frames?start_time=...&end_time=...&limit=100`). Not present in any route file. Returns 404.

---

### 2.5 Events

All event routes exist in both blueprint and inline; blueprints win.

#### GET `/api/event/config/type_a`

- **Winner:** `src/app/routes/events.py:12-38`. Inline: `web_server.py:2167-2199`.
- **Auth:** `@login_required`.
- **Response (success):** `{ success: true, config: { timeframe_seconds: float, tolerance_pct: float, threshold_lower: float, threshold_upper: float, enabled: bool, ttl_seconds: float, debounce_seconds: float } }`. `tolerance_pct` is derived from stored `threshold_lower` for legacy compat.
- **Default** (no DB row): `timeframe_seconds=20, tolerance_pct=10, threshold_lower=10, threshold_upper=10, enabled=false, ttl_seconds=5, debounce_seconds=0`.

#### POST `/api/event/config/type_a`

- **Winner:** `src/app/routes/events.py:41-80`.
- **Request body (JSON):** Accepts either `T1` or `timeframe_seconds` (required, float). `tolerance_pct` (preferred) or legacy `threshold_lower`. Optional: `ttl_seconds` (default 5.0), `debounce_seconds` (default 0), `enabled` (default true).
- **Validation:** timeframe between `db.get_app_config_value('type_a_timeframe_min_s', 1)` and `type_a_timeframe_max_s` (default 60); tolerance >= 0.
- **Side effect:** Calls `detector.reload_config()` and `detector.reload_ttl_config()` on every active EventDetector.
- **Response:** `{ success: true, message: "Type A GLOBAL configuration saved and applied to all N device(s)" }`.
- **Inline divergence (dead):** inline also validates `threshold_lower <= threshold_upper` and has hardcoded 1..60 bounds instead of reading app_config.

#### GET `/api/event/list`

- **Winner:** `src/app/routes/events.py:83-122`.
- **Auth:** `@login_required`.
- **Query:** `device_id` (int), `sensor_id` (int), `start_time` (numeric epoch s or ms, or datetime string), `end_time` (same), `limit` (int, default from `db.get_app_config_value('api_event_list_default_limit', 100)` — blueprint). Inline default is hard-coded 100. Values above `1e12` treated as milliseconds and divided by 1000.
- **Response:** `{ success: true, events: [...], count: int }`, 200.
- **No hard-cap on limit** — whatever the client sends goes through to `db.get_events(..., limit=limit)`.

#### POST `/api/event/config/type_a/per_sensor/bulk`

- **Winner:** `src/app/routes/events.py:125-205`.
- **Auth:** `@login_required`.
- **Request body** (one of):
  - Plain dict `{ "<device_id>": { "<sensor_id>": {config}, ... }, ... }`, OR
  - Wrapped `{ "configs": {...}, "selected_config": {optional global override} }`.
  - Per-sensor config fields: `T1` or `timeframe_seconds` (default 20), `tolerance_pct` or legacy `threshold_lower` (default 10), `enabled` (default true).
- **Validation:** per-sensor, timeframe within app-config bounds; tolerance >= 0.
- **Side effects:** `db.set_device_per_sensor_mode(device_id, use_per_sensor=True)`; `detector.reload_config()` per affected device. If `selected_config` present, also saves as global via `db.save_type_a_config(...)`.
- **Response:** `{ success: true, saved_count: int, message: "Saved N per-sensor configurations", warnings?: [str] }`.
- **Inline divergence (dead):** inline reads `ttl_seconds` per-sensor and passes to `db.save_type_a_per_sensor_config(..., ttl_seconds=...)`; blueprint does NOT. This is a behavior drift that won't manifest because the blueprint wins.

#### GET `/api/event/config/type_a/per_sensor`

- **Winner:** `src/app/routes/events.py:208-229`.
- **Auth:** `@login_required`.
- **Query:** `device_id` (int, optional).
- **Response, with device_id:** `{ success: true, device_id: int, configs: { "<sensor_id>": {...}, ... } }` (empty `{}` if no per-sensor rows).
- **Response, without device_id:** `{ success: true, configs_by_device: { "<device_id>": { "<sensor_id>": {...}, ... }, ... } }`.
- **Key-shape quirk:** outer keys are integers in Python (from `device['device_id']`), but JSON serializes as strings.

#### GET `/api/event/<int:event_id>/data`

- **Winner:** `src/app/routes/events.py:232-248`. Inline: `web_server.py:2731-2763`.
- **Auth:** none (blueprint) or none (inline).
- **Response:** `{ success: true, data: <decoded JSON blob> }` or 404. If legacy event without `event_info` in blob, backfills from `db.get_event_metadata(event_id)`.

#### GET `/api/event/<int:event_id>/metadata` — **NOT IMPLEMENTED.** No route defined. `grep` confirms no match anywhere.

#### GET `/api/event/detector/status`

- **Winner:** `src/app/routes/events.py:251-261`.
- **Auth:** `@login_required`.
- **Response:** `{ success: true, detectors: { "<device_id>": <buffer_status>, ... }, total_devices: int }`. `buffer_status` = `detector.get_buffer_status()`.

#### GET `/api/mode_switching/config`

- **Winner:** `src/app/routes/events.py:268-288`. Inline: `web_server.py:2575-2596`.
- **Auth:** `@login_required`.
- **Response:** `{ success: true, config: { enabled: bool, startup_threshold: float, break_threshold: float, startup_duration_seconds: float, break_duration_seconds: float } }` with defaults `{false, 100.0, 50.0, 0.1, 2.0}`.

#### POST `/api/mode_switching/config`

- **Winner:** `src/app/routes/events.py:291-338`.
- **Auth:** `@login_required`.
- **Request (JSON):** `{ enabled: bool, startup_threshold: float, break_threshold: float, startup_duration_seconds: float, break_duration_seconds: float }`.
- **Validation:** `startup_threshold > break_threshold`; both durations > 0.
- **Side effect:** updates every running EventDetector's `mode_switching_enabled/startup_threshold/break_threshold/startup_duration_seconds/break_duration_seconds` and resets timing state (`sensor_above_start_time`, `sensor_below_start_time`).
- **Response:** `{ success: true }` or `{ success: false, error: "..." }` (400 on validation, 500 on DB).

#### GET `/api/mode_switching/per_sensor`

- **Winner:** `src/app/routes/events.py:341-351`. Inline: `web_server.py:2654-2664`.
- **Response:** `{ success: true, configs_by_device: { <device_id>: { <sensor_id>: {...}, ... }, ... } }`.

#### POST `/api/mode_switching/per_sensor`

- **Winner:** `src/app/routes/events.py:354-401`.
- **Auth:** `@login_required`.
- **Request:** `{ device_id: int(1-20), sensor_id: int(1-12), startup_threshold: float, break_threshold: float, startup_duration_seconds: float (default 2.0), break_duration_seconds: float (default 2.0) }`.
- **Validation:** ranges, `startup > break`, durations > 0.
- **Side effect:** `detector.apply_mode_switching_per_sensor_configs(device_configs)` for matching device.
- **Response:** `{ success: true }`.

#### DELETE `/api/mode_switching/per_sensor/<int:device_id>/<int:sensor_id>`

- **Winner:** `src/app/routes/events.py:404-423`.
- **Response:** `{ success: true }` or 500 on failure. Live-reapplies device per-sensor configs to detector.

---

### 2.6 Avg-type algorithms (B, C, D)

All of these live in the `algorithms` blueprint (`src/app/routes/algorithms.py`) and have inline duplicates in `web_server.py`. Blueprints win.

**None of the B/C/D routes use `@login_required`.** They are public in both blueprint and inline.

#### Avg Type B (`/home/embed/hammer/src/app/routes/algorithms.py`)

| Path | Method | File:Line (winner) | Inline line | Behavior / Response |
|---|---|---|---|---|
| `/api/avg-type-b/config` | GET | algorithms.py:102 | web_server.py:3141 | `{ success: true, config: { t2_seconds, lower_tolerance_pct, upper_tolerance_pct, pre_event_seconds, post_event_seconds, enabled, ttl_seconds, debounce_seconds } }` |
| `/api/avg-type-b/config` | POST | algorithms.py:102 | web_server.py:3141 | Body accepts `T2`, `lower_tolerance_pct`/`lower_threshold`, `upper_tolerance_pct`/`upper_threshold`, `ttl_seconds` (default 10), `debounce_seconds` (default 0), `enabled`, optional `device_id`/`sensor_id`. Saves global + optional per-sensor + selection. Response: `{ success: true }` |
| `/api/avg-type-b/config/bulk` | POST | algorithms.py:179 | web_server.py:3228 | Accepts `{configs: {<device>: {<sensor>: {...}}}, selected_config?}` OR raw configs dict. Response: `{ success: true, t2_seconds, lower_tolerance_pct, upper_tolerance_pct }` |
| `/api/avg-type-b/config/per_sensor` | GET | algorithms.py:245 | web_server.py:3302 | `{ success: true, configs_by_device: { <device_id>: { <sensor_id>: { T2, lower_tolerance_pct, upper_tolerance_pct }, ... }, ... } }` |
| `/api/avg-type-b/start` | POST | algorithms.py:263 | web_server.py:3321 | Optional body `{simulate: bool}` or query `?simulate=1` to spin up `simulate_signal_b` thread. `{ success: true }` |
| `/api/avg-type-b/stop` | POST | algorithms.py:282 | web_server.py:3344 | `{ success: true }` |
| `/api/avg-type-b/reset` | POST | algorithms.py:294 | web_server.py:3358 | `{ success: true }` |
| `/api/avg-type-b/stats` | GET | algorithms.py:306 | web_server.py:3375 | `{ total_samples, events_detected, detection_rate, current_avg, current_bounds, mode: 'real'|'simulation' }` |
| `/api/avg-type-b/events` | GET | algorithms.py:340 | web_server.py:3419 | Returns JSON **array** (not object): up to 20 events, newest-first |
| `/api/avg-type-b/selection` | GET | algorithms.py:355 | web_server.py:3439 | `{ success: true, selection: {device_id: int, sensor_id: int} \| null }` |

#### Avg Type C

| Path | Method | File:Line | Inline | Response |
|---|---|---|---|---|
| `/api/avg-type-c/config` | GET | algorithms.py:366 | web_server.py:3476 | `{ success:true, config: { t3_seconds, lower_threshold, upper_threshold, enabled, ttl_seconds, debounce_seconds } }` |
| `/api/avg-type-c/config` | POST | algorithms.py:366 | web_server.py:3476 | Body: `T3`, `lower_threshold`, `upper_threshold`, `ttl_seconds` (default 3), `debounce_seconds`, `enabled`. `{ success: true }` |
| `/api/avg-type-c/config/bulk` | POST | algorithms.py:405 | web_server.py:3522 | `{configs:{...}, selected_config?}`. `{ success: true, t3_seconds, lower_threshold, upper_threshold }` |
| `/api/avg-type-c/config/per_sensor` | GET | algorithms.py:451 | web_server.py:3576 | `{ success: true, configs_by_device: {...} }` (raw DB shape — keys `t3_seconds`, `threshold_lower`, `threshold_upper`) |
| `/api/avg-type-c/start` | POST | algorithms.py:460 | web_server.py:3586 | `{ success: true }`. ALWAYS spawns `simulate_signal_c` thread (no `simulate` gating — unlike B) |
| `/api/avg-type-c/stop` | POST | algorithms.py:475 | web_server.py:3604 | `{ success: true }` |
| `/api/avg-type-c/reset` | POST | algorithms.py:487 | web_server.py:3618 | `{ success: true }` |
| `/api/avg-type-c/stats` | GET | algorithms.py:498 | web_server.py:3631 | `{ total_samples, events_detected, detection_rate, current_avg, lower_threshold, upper_threshold, in_range, mode }` |
| `/api/avg-type-c/events` | GET | algorithms.py:533 | web_server.py:3676 | JSON array of up to 20 events |

#### Avg Type D

| Path | Method | File:Line | Inline | Response |
|---|---|---|---|---|
| `/api/avg-type-d/config` | GET | algorithms.py:550 | web_server.py:3718 | `{ success:true, config:{ t4_seconds, t5_seconds, lower_threshold, upper_threshold, enabled, ttl_seconds, debounce_seconds } }`. **Inline response omits `t5_seconds` — blueprint wins** |
| `/api/avg-type-d/config` | POST | algorithms.py:550 | web_server.py:3718 | Body: `T4`, `T5` or `t5_seconds` (default 30), `tolerance` (if set, overrides `lower_threshold`/`upper_threshold`), else `lower_threshold`/`upper_threshold`. `ttl_seconds` (default 8), `debounce_seconds`, `enabled`. `{ success: true }` |
| `/api/avg-type-d/config/bulk` | POST | algorithms.py:596 | web_server.py:3764 | `{ success: true, t4_seconds, lower_threshold, upper_threshold }` |
| `/api/avg-type-d/config/per_sensor` | GET | algorithms.py:649 | web_server.py:3818 | `{ success: true, configs_by_device: {...} }` |
| `/api/avg-type-d/start` | POST | algorithms.py:658 | web_server.py:3828 | Spawns `simulate_signal_d`. `{ success: true }` |
| `/api/avg-type-d/stop` | POST | algorithms.py:673 | web_server.py:3846 | `{ success: true }` |
| `/api/avg-type-d/reset` | POST | algorithms.py:685 | web_server.py:3860 | `{ success: true }` |
| `/api/avg-type-d/stats` | GET | algorithms.py:696 | web_server.py:3873 | `{ total_samples, events_detected, detection_rate, current_smoothed_avg, lower_threshold, upper_threshold, in_range, stage1_calculations, mode }` |
| `/api/avg-type-d/events` | GET | algorithms.py:734 | web_server.py:3921 | JSON array of up to 20 events |
| `/api/avg-type-d/stage1-averages` | GET | algorithms.py:749 | web_server.py:3939 | JSON array from `avg_detector_d.get_one_sec_averages(count=20)` |

**Response-key audit (critical for UI rewrite):**

- GET `/api/avg-type-b/config/per_sensor` returns `configs_by_device` (camel-adjacent plural form).
- GET `/api/avg-type-c/config/per_sensor` returns `configs_by_device`.
- GET `/api/avg-type-d/config/per_sensor` returns `configs_by_device`.
- GET `/api/event/config/type_a/per_sensor` returns EITHER `configs` (when `device_id` passed) OR `configs_by_device` (when omitted). This asymmetry is the source of the per-sensor key audit finding — it is deliberate but confusing.

There is **no `/api/avg-type-c/selection`** or `/api/avg-type-d/selection` endpoint. Selection exists only for Type B.

---

### 2.7 MQTT config

#### GET `/api/mqtt/config`

- **Winner:** `src/app/routes/mqtt_routes.py:16-35`. Inline: `web_server.py:1264-1283`.
- **Auth:** `@login_required` in both.
- **Response:** `{ success: true, mqtt_available: bool, mqtt_enabled: bool, broker: str, port: int, base_topic: str, subscribe_topic: "stm32/adc", websocket_url: str, username: str, enabled: bool }`.
- **Source of truth:** blueprint reads `services.mqtt_config_db.get_config()` and various `services.mqtt_*` globals. Inline reads `_mqtt_config_db` and `mqtt_*` web_server globals. These are kept loosely in sync via `services.mqtt_global_enabled`.

#### PUT `/api/mqtt/config`

- **Winner:** `src/app/routes/mqtt_routes.py:38-71`.
- **Auth:** `@login_required`.
- **Request:** partial — any of `broker, port, base_topic, websocket_url, username, password, enabled`. Empty strings for `username`/`password` → `None`. `port` cast to int.
- **Persistence:** `services.mqtt_config_db.save_config(dict)` in blueprint (**kwargs call in inline** — this is a real shape drift but inline is dead).
- **Response:** `{ success: true, message: "MQTT config updated" }`.
- **Note:** does NOT restart/reconnect the MQTT client — the new broker takes effect only on next server start.

#### GET `/api/mqtt/status`

- **Winner:** `src/app/routes/mqtt_routes.py:74-90`. Inline: `web_server.py:1325-1338`.
- **Auth:** `@login_required`.
- **Response:** `{ success: true, connected: bool, broker: "<host>:<port>", subscribe_topic: "stm32/adc", mqtt_available: bool }`. Polls `services.mqtt_client.is_connected()` if the client exists.

---

### 2.8 System / app config

All of these live in the `system` blueprint (`src/app/routes/system.py`) except `/api/system/auto-restart/config` and `/api/system/auto-restart/status` which also have inline dups.

#### GET `/api/system/app-config`

- **File:** `system.py:134-179`.
- **Auth:** `@login_required`.
- **Response:** `{ success: true, config: { "<Category>": [ { key, value, default_val, data_type: "int"|"float"|"bool"|"str", label, description, requires_restart: bool, updated_at: str }, ... ], ... } }`.
- Entries sorted alphabetically by `label` within each category.

#### POST `/api/system/app-config`

- **File:** `system.py:182-274`.
- **Auth:** `@login_required`.
- **Request:** `{ "updates": { "<key>": "<value>", ... } }` — must be non-empty dict.
- **Validation:** all keys must exist in current app_config; values type-checkable (int/float/bool/str).
- **Persistence:** `db.set_app_config_values(str_updates)` (all values stringified).
- **Live-apply:** calls `detector.apply_app_config_live(cast_updates)` on every running detector, `worker_manager.apply_app_config_live(...)`, and `services.apply_globals_live(...)`.
- **Response:** `{ success: true, saved: [keys], requires_restart: bool, restart_keys: [keys] }`.
- **Errors:** 400 missing updates / unknown key / type error (with `details` array), 500 DB write failed.

#### POST `/api/system/app-config/reset`

- **File:** `system.py:277-316`.
- **Auth:** `@login_required`.
- **Request:** `{ "keys": ["a","b",...] }` optional. Empty/absent → reset ALL.
- **Response:** `{ success: true, message: str, keys_reset: [str] | "all" }`.

#### GET `/api/system/app-config/<key>`

- **File:** `system.py:319-327`.
- **Auth:** `@login_required`.
- **Response:** `{ success: true, key: str, ...meta }` where meta has `value, default_val, data_type, label, description, requires_restart, updated_at, category`. 404 on unknown key.

#### GET `/api/system/auto-restart/config`

- **Winner:** `src/app/routes/system.py:33-66`. Inline duplicate: `web_server.py:1340-1376`.
- **Auth:** blueprint has NO `@login_required`; inline has `@login_required`. **Blueprint wins — auto-restart config is unauthenticated.** This is a likely security oversight.
- **Response:** `{ success: true, config: { enabled: bool, restart_hour: int, restart_minute: int, last_restart: str|null }, status: <monitor.get_status() shape or fallback dict> }`.

#### POST `/api/system/auto-restart/config`

- **Same file/method** — blueprint wins, NO auth.
- **Request:** `{ enabled?: bool, restart_hour?: int(0-23), restart_minute?: int(0-59) }`.
- **Response:** `{ success: true, message: "Configuration updated successfully", config: {...}, status: {...} }`.

#### GET `/api/system/auto-restart/status`

- **Winner:** `src/app/routes/system.py:330-344`. Inline: `web_server.py:1444-1461`.
- **Auth:** neither has `@login_required` (blueprint; inline has `@login_required`). Blueprint wins — no auth.
- **Response:** `{ success: true, status: <monitor.get_status()> }` or 503 if monitor not init.

#### Mode-switching

All `/api/mode_switching/*` endpoints are defined in `events.py` (see §2.5) — they live under the events blueprint, not system. Paths: `/api/mode_switching/config` (GET/POST), `/api/mode_switching/per_sensor` (GET/POST), `/api/mode_switching/per_sensor/<device_id>/<sensor_id>` (DELETE).

---

### 2.9 Sensor offsets

Only blueprint routes — no inline duplicates.

#### GET `/api/offsets/<int:device_id>` — `offsets.py:9-17`

- **Auth:** `@login_required`.
- **Response:** `{ success: true, device_id: int, offsets: {<sensor_id>: float, ...} }`. `services.db.get_all_sensor_offsets(device_id)` backs this.

#### POST `/api/offsets/<int:device_id>` — `offsets.py:20-40`

- **Auth:** `@login_required`.
- **Request:** `{ "offsets": { "<sensor_id>": float, ... } }` — keys cast `int`, values cast `float`.
- **Side effect:** `services.refresh_offsets(device_id)` if callable (refreshes live cache).
- **Response:** `{ success: true }` on success; 400 on no-offsets or type error; 500 on exception.

#### POST `/api/offsets/<int:device_id>/bulk` — `offsets.py:43-60`

- **Request:** `{ "value": float }` — applies same value to all 12 sensors.
- **Response:** `{ success: true }`.

#### POST `/api/offsets/<int:device_id>/reset` — `offsets.py:63-74`

- **Request:** none.
- **Behavior:** sets all 12 sensors to `0.0`.
- **Response:** `{ success: true }`.

No `/api/sensor-offsets` route exists; some older code may reference it but it returns 404.

---

### 2.10 Debug / simulators / internal

- `GET /api/debug/variance_state` — `sensors.py:166-188` (blueprint wins). See §2.4.
- No other `/api/debug/*` routes exist.
- **Simulator functions** (not routes, but spawned by start endpoints):
  - `simulate_signal_b` in `algorithms.py:13-28` — 123 Hz, baseline 78.92 ± spike. Spawned by `/api/avg-type-b/start` only if `simulate=1` passed.
  - `simulate_signal_c` in `algorithms.py:31-44` — 100 Hz. **Always** spawned by `/api/avg-type-c/start`.
  - `simulate_signal_d` in `algorithms.py:47-60` — 100 Hz. **Always** spawned by `/api/avg-type-d/start`.

---

### 2.11 Endpoints CALLED by UI but NOT IMPLEMENTED (dead)

Confirmed by grepping `templates/device_detail.html` and `static/app.js` against all route decorators:

| URL called in UI | Referrer | Backend? |
|---|---|---|
| `/api/device/open` | `templates/device_detail.html:3008`, `static/app.js:61` | NOT IMPLEMENTED |
| `/api/device/init` | `device_detail.html:3020`, `app.js:158` | NOT IMPLEMENTED |
| `/api/stream/start` | `device_detail.html:3044`, `app.js:223` | NOT IMPLEMENTED |
| `/api/device/errors` | `device_detail.html:3241` | NOT IMPLEMENTED |
| `/api/device/info` | `device_detail.html:2968` (commented out), `app.js:134` | NOT IMPLEMENTED |
| `/api/db/frames` | `device_detail.html:5490, 5656` | NOT IMPLEMENTED |
| `/api/frames/grids` | `device_detail.html:7499`, `app.js:374` | NOT IMPLEMENTED |
| `/api/frames/export` | `device_detail.html:7588` | NOT IMPLEMENTED |
| `/api/event/<id>/metadata` | referenced in audit — no UI code found here | NOT IMPLEMENTED |

All return 404.

---

## 3. Duplicated routes (same path in both web_server.py and blueprint)

Blueprints always win on dispatch because they register first at `web_server.py:101`, before the inline decorators are evaluated.

| Path | Methods | web_server.py line | Blueprint file:line | Winner | Behavioral drift |
|---|---|---|---|---|---|
| `/` | GET | 1190 | dashboard.py:8 | blueprint | identical |
| `/login` | GET | 1066 | auth.py:94 | blueprint | identical |
| `/dashboard` | GET | 1196 | dashboard.py:13 | blueprint | **blueprint redirects to `/device-config`; inline renders `device_detail.html`** |
| `/device-config` | GET | 1206 | dashboard.py:22 | blueprint | identical |
| `/device-config/<int:device_id>` | GET | 1213 | dashboard.py:28 | blueprint | identical |
| `/event-config` | GET | 1220 | dashboard.py:34 | blueprint | identical |
| `/system-config` | GET | 1227 | system.py:19 | blueprint | identical |
| `/ttl-config` | GET | 1234 | dashboard.py:40 | blueprint | **blueprint renders `ttl_config.html`; inline redirects** |
| `/avg-type-b` | GET | 1241 | dashboard.py:47 | blueprint | identical (both redirect) |
| `/avg-type-c` | GET | 1248 | dashboard.py:53 | blueprint | identical |
| `/avg-type-d` | GET | 1255 | dashboard.py:59 | blueprint | identical |
| `/api/auth/login` | POST | 1072 | auth.py:99 | blueprint | identical |
| `/api/auth/otp/request` | POST | 1096 | auth.py:118 | blueprint | identical |
| `/api/auth/otp/verify` | POST | 1136 | auth.py:158 | blueprint | identical |
| `/api/auth/logout` | POST | 1175 | auth.py:197 | blueprint | identical |
| `/api/mqtt/config` | GET | 1264 | mqtt_routes.py:16 | blueprint | near-identical |
| `/api/mqtt/config` | PUT | 1286 | mqtt_routes.py:38 | blueprint | Inline uses `save_config(**kwargs)`; blueprint uses `save_config({})` |
| `/api/mqtt/status` | GET | 1325 | mqtt_routes.py:74 | blueprint | identical |
| `/api/system/auto-restart/config` | GET,POST | 1340 | system.py:33 | blueprint | **Blueprint missing `@login_required`; inline has it. Blueprint wins → unauthenticated** |
| `/api/system/auto-restart/status` | GET | 1444 | system.py:330 | blueprint | **Same — blueprint unauthenticated** |
| `/api/devices` | GET | 1466 | devices.py:27 | blueprint | Inline adds `max_devices: 20` to response; blueprint omits |
| `/api/devices` | POST | 1522 | devices.py:47 | blueprint | Inline reads many fields (device_type, model, ip_address, slave_id, …); blueprint reads only `device_name` + `topic` |
| `/api/devices/<int:device_id>` | GET | 1614 | devices.py:74 | blueprint | identical |
| `/api/devices/<int:device_id>` | PUT | 1639 | devices.py:86 | blueprint | identical |
| `/api/devices/<int:device_id>` | DELETE | 1685 | devices.py:105 | blueprint | identical |
| `/api/devices/<int:device_id>/initialize` | POST | 1729 | devices.py:121 | blueprint | Inline branches on `device_type` with KeyError risk for MQTT rows; blueprint no-ops |
| `/api/devices/<int:device_id>/start` | POST | 1832 | devices.py:133 | blueprint | Inline spawns poll thread (Modbus); blueprint only creates EventDetector |
| `/api/devices/<int:device_id>/stop` | POST | 2009 | devices.py:162 | blueprint | Inline closes modbus device; blueprint only stops detector |
| `/api/devices/<int:device_id>/status` | GET | 2056 | devices.py:181 | blueprint | Inline adds `initialized`, `stats`, `sample_rate_hz`; blueprint adds `topic`, `is_active` |
| `/api/event/config/type_a` | GET,POST | 2167, 2202 | events.py:12, 41 | blueprint | Inline uses hardcoded 1-60 timeframe validation; blueprint reads `db.get_app_config_value('type_a_timeframe_min_s', ...)` |
| `/api/event/list` | GET | 2301 | events.py:83 | blueprint | Inline default limit 100 hardcoded; blueprint reads `api_event_list_default_limit` |
| `/api/event/config/type_a/per_sensor/bulk` | POST | 2376 | events.py:125 | blueprint | Inline reads `ttl_seconds` per-sensor; blueprint does NOT |
| `/api/event/config/type_a/per_sensor` | GET | 2479 | events.py:208 | blueprint | identical |
| `/api/mode_switching/config` | GET,POST | 2575, 2599 | events.py:268, 291 | blueprint | identical |
| `/api/mode_switching/per_sensor` | GET,POST | 2654, 2667 | events.py:341, 354 | blueprint | identical |
| `/api/mode_switching/per_sensor/<int:device_id>/<int:sensor_id>` | DELETE | 2712 | events.py:404 | blueprint | identical |
| `/api/event/<int:event_id>/data` | GET | 2731 | events.py:232 | blueprint | identical |
| `/api/event/detector/status` | GET | 2766 | events.py:251 | blueprint | identical |
| `/api/sensor_data` | GET | 2792 | sensors.py:47 | blueprint | Inline `max_samples` default 1000 hardcoded; blueprint reads `api_live_sensor_max_samples` |
| `/api/debug/variance_state` | GET | 2872 | sensors.py:166 | blueprint | identical |
| `/api/live_sensor_data` | GET | 2893 | sensors.py:93 | blueprint | identical; inline has stats-overlay debug prints |
| `/api/live_stream` | GET | 3010 | sensors.py:191 | blueprint | Inline `max_samples` default 200; blueprint default from `api_live_sensor_cap` (1500). Inline has 1ms post-yield sleep; blueprint has batching `time.sleep(interval)` BEFORE fetching |
| `/api/avg-type-b/config` | GET,POST | 3141 | algorithms.py:102 | blueprint | Blueprint also reloads TTL+debounce on detectors; inline doesn't |
| `/api/avg-type-b/config/bulk` | POST | 3228 | algorithms.py:179 | blueprint | identical |
| `/api/avg-type-b/config/per_sensor` | GET | 3302 | algorithms.py:245 | blueprint | identical |
| `/api/avg-type-b/start` | POST | 3321 | algorithms.py:263 | blueprint | identical |
| `/api/avg-type-b/stop` | POST | 3344 | algorithms.py:282 | blueprint | identical |
| `/api/avg-type-b/reset` | POST | 3358 | algorithms.py:294 | blueprint | identical |
| `/api/avg-type-b/stats` | GET | 3375 | algorithms.py:306 | blueprint | identical |
| `/api/avg-type-b/events` | GET | 3419 | algorithms.py:340 | blueprint | identical |
| `/api/avg-type-b/selection` | GET | 3439 | algorithms.py:355 | blueprint | identical |
| `/api/avg-type-c/config` | GET,POST | 3476 | algorithms.py:366 | blueprint | Blueprint reloads TTL+debounce |
| `/api/avg-type-c/config/bulk` | POST | 3522 | algorithms.py:405 | blueprint | identical |
| `/api/avg-type-c/config/per_sensor` | GET | 3576 | algorithms.py:451 | blueprint | identical |
| `/api/avg-type-c/start` | POST | 3586 | algorithms.py:460 | blueprint | identical |
| `/api/avg-type-c/stop` | POST | 3604 | algorithms.py:475 | blueprint | identical |
| `/api/avg-type-c/reset` | POST | 3618 | algorithms.py:487 | blueprint | identical |
| `/api/avg-type-c/stats` | GET | 3631 | algorithms.py:498 | blueprint | identical |
| `/api/avg-type-c/events` | GET | 3676 | algorithms.py:533 | blueprint | identical |
| `/api/avg-type-d/config` | GET,POST | 3718 | algorithms.py:550 | blueprint | Blueprint supports `T5`, `tolerance` shorthand; inline doesn't |
| `/api/avg-type-d/config/bulk` | POST | 3764 | algorithms.py:596 | blueprint | Blueprint supports `T5`, `tolerance` shorthand |
| `/api/avg-type-d/config/per_sensor` | GET | 3818 | algorithms.py:649 | blueprint | identical |
| `/api/avg-type-d/start` | POST | 3828 | algorithms.py:658 | blueprint | identical |
| `/api/avg-type-d/stop` | POST | 3846 | algorithms.py:673 | blueprint | identical |
| `/api/avg-type-d/reset` | POST | 3860 | algorithms.py:685 | blueprint | identical |
| `/api/avg-type-d/stats` | GET | 3873 | algorithms.py:696 | blueprint | identical |
| `/api/avg-type-d/events` | GET | 3921 | algorithms.py:734 | blueprint | identical |
| `/api/avg-type-d/stage1-averages` | GET | 3939 | algorithms.py:749 | blueprint | identical |

**Inline-only (no blueprint dup) — these routes are actually served by the inline handler:**

- `GET /api/stats` — `web_server.py:2123-2162`
- `POST /api/system/app-config` and friends — actually these ARE in the system blueprint, listed above.

---

## 4. Authentication flow

- **Session cookie:** default Flask session (`session` proxy). Cookie name defaults to `session`. No custom `SESSION_COOKIE_*` config set; runs with Flask defaults (HttpOnly, not Secure, SameSite=Lax).
- **Secret key:** `web_server.py:93` — `app.secret_key = 'usb-can-b-secret-key-change-in-production'`. Hardcoded; not rotated.
- **Expiry:** no `PERMANENT_SESSION_LIFETIME` set. Session cookie expires when browser closes.
- **Session keys set:** `logged_in` (True), `username` (str), `email` (if OTP), `auth_method` ('otp' if OTP path, otherwise absent).
- **CSRF:** NONE. No CSRF tokens generated or validated. Flask-WTF is not installed. Forms/JS calls rely on same-origin cookies + CORS (`CORS(app)` at line 94 — wide-open).
- **OTP state location:** `services.otp_store` — an in-memory Python dict, guarded by `services.otp_lock`. Not persisted. Restart clears all pending OTPs.
- **OTP rate limiting:** per-email only (by dict key). No per-IP limiting. Window is `otp_rate_limit_window_s` from app_config (default 3600 s); max requests `OTP_MAX_PER_HOUR` env var (default 5).
- **OTP allowlist:** `emails.txt` in project root. Lowercased, `#` comments, blank lines ignored. If the file is missing or empty, all OTP requests return 400 `"No allowed emails configured"`.

---

## 5. Error response format

Two patterns are used, depending on the age of the route:

1. **Predominant (blueprint + most inline):** `{ "success": false, "error": "<message>" }` with appropriate HTTP status.
2. **Older avg-type routes:** `{ "error": "<message>" }` without `success`, returned with status 200 from exception handlers (see `algorithms.py:337, 352, 361, 402, 448, 457, 472, 484, 495, 530, 545, 593, 646, 655, 670, 682, 693, 731, 746, 755`). This is a contract inconsistency — clients must check both shapes.

**HTTP status codes used:**

- `200` — all success responses (even 200+success:false for some older routes).
- `302` — redirects (dashboard pages, login required).
- `400` — missing/invalid request fields, validation failure.
- `401` — invalid credentials, invalid OTP.
- `403` — OTP email not in allowlist.
- `404` — device/event/detector/config key not found.
- `429` — OTP rate limit exceeded / too many attempts.
- `500` — server exception; body carries stringified exception.
- `503` — auto-restart monitor not initialized.

---

## 6. SSE `/api/live_stream` deep dive

### Event format

Output is `text/event-stream`. First message:

```
retry: 2000

```

(Tells browser to reconnect after 2000 ms on drop.) Subsequent messages:

```
data: {"timestamps": [...], "sensors": {...}, "stats": {...}, "sensor_modes": {...}, "seq_to": N, "ts_encoding": "delta_ms"}

```

### Query params (blueprint, `sensors.py:194-224`)

| Param | Type | Default | Notes |
|---|---|---|---|
| `device_id` | string | **required** | matched to `str(device_id)` keys |
| `sensor_ids` | CSV int or `"all"` | `"all"` | e.g. `"1,3,7"` |
| `interval` | float s | `services.LIVE_STREAM_INTERVAL_S` (env `LIVE_STREAM_INTERVAL_S`, default 0.1) | Sleep between batches to let samples accumulate |
| `max_samples` | int | `db.get_app_config_value('api_live_sensor_cap', 1500)` | Max samples per SSE frame |
| `last_seq` | int | 0 | Resume from this sequence number |

### Heartbeat

There is no explicit heartbeat/ping frame. If no data arrives, `services.live_data.wait_for_data(device_id, timeout=1.0)` returns empty-handed, `payload.get('timestamps')` is falsy, and NOTHING is yielded. The client depends on the TCP connection staying open. `X-Accel-Buffering: no` header prevents nginx buffering. No comment-only `:keepalive\n\n` is ever sent.

### Delta encoding of timestamps

After receiving the payload from `live_data.get_since(...)` (which returns absolute seconds-float timestamps), the handler (`sensors.py:256-261` / `web_server.py:3084-3089`):

1. Converts each timestamp to integer milliseconds: `ts_ms = [round(t * 1000) for t in payload['timestamps']]`.
2. Replaces the list with `[ts_ms[0], ts_ms[1]-ts_ms[0], ts_ms[2]-ts_ms[1], ...]` — first element is absolute ms, rest are deltas.
3. Adds `payload['ts_encoding'] = 'delta_ms'` as a marker.

### Response payload shape (fully expanded)

```json
{
  "timestamps": [1700000000000, 10, 10, 10, ...],   // first absolute ms, rest ms deltas
  "ts_encoding": "delta_ms",
  "sensors": { "1": [v1, v2, ...], "2": [...], ..., "12": [...] },  // sparse if sensor_ids filtered
  "sensor_modes": { "1": "POWER_ON"|"STARTUP"|"BREAK"|..., ... },   // only if EventDetector exists
  "stats": { "1": { "avg": float, "variance": float, ... }, ... }, // only if detector exists
  "seq_to": 12345   // monotonic; client sets ?last_seq on reconnect
}
```

### Client expectations

`templates/device_detail.html` consumes this via `EventSource`. The client:

- Reconstructs absolute timestamps by running prefix-sum on the `timestamps` array when `ts_encoding === 'delta_ms'`, then dividing by 1000 for seconds.
- Tracks `seq_to` to resume.
- Expects `sensors` as an object with stringified integer keys (`"1"` through `"12"`).

### Quirks

- No backpressure: a slow client blocks the generator on `yield`, which blocks the producer thread for this one subscription (Flask spawns one Python thread per request in dev mode).
- Exception handling in stream loop: broad `except Exception` → `time.sleep(0.1)` + continue. Errors never propagate to the client (they just appear as silence).
- Inline (dead) version has a `time.sleep(0.001)` post-yield "force flush" hack (`web_server.py:3095`); blueprint does not.

---

## 7. Payload conventions across the API

| Concern | Convention | Exceptions |
|---|---|---|
| Timestamps in responses | **Unix seconds float** (e.g. `1700000000.123`) | SSE stream: **delta-encoded milliseconds** (first absolute int ms, then int ms deltas). `start_time`/`end_time` query params accept EITHER numeric epoch s/ms OR datetime string. Values `> 1e12` are treated as ms and divided by 1000. |
| Event types | UPPERCASE letter: `'A'`, `'B'`, `'C'`, `'D'` in DB rows and response objects. Route segments use lowercase: `/api/event/config/type_a`, `/api/avg-type-b/...`. |
| Sensor IDs | **Integers 1..12.** JSON serialization: when in dict keys, they become strings (`"1"`-`"12"`); when in arrays they remain ints. Some responses mix both — `configs_by_device` has int-stringified keys at both levels. |
| Device IDs | **Integers 1..20 (max).** Route segments use `<int:device_id>`. EventDetector dict keys use **stringified** (`str(device_id)`) forms — check `services.event_detectors[str(device_id)]`. |
| Boolean fields | Native JSON `true`/`false`. Request bodies cast via `bool(data.get(...))` which treats any truthy Python value (including non-empty strings) as true. |
| Float fields | JSON number. Seconds fields in config bodies/responses: `T1`, `T2`, `T3`, `T4`, `T5` (uppercase short) and `*_seconds` (lowercase long) are interchangeable — most POST handlers accept both. |
| Response envelope | `{ "success": bool, "error"?: str, "message"?: str, ...payload }`. `success:true` is almost always present EXCEPT on: `/api/avg-type-b/stats|events`, `/api/avg-type-c/stats|events`, `/api/avg-type-d/stats|events|stage1-averages` (return raw dict/array), and `/api/stats` (returns raw counter dict). |

---

## 8. File:line references

All line numbers are absolute and current as of the file state read.

- `web_server.py:92` — Flask app instantiation.
- `web_server.py:93` — hardcoded secret key.
- `web_server.py:94` — `CORS(app)` (wide-open).
- `web_server.py:100-101` — `register_blueprints(app)` call site.
- `web_server.py:361-367` — inline `login_required` decorator (shadows blueprint version for inline routes; blueprints use their own from `auth.py:85-91`).
- `web_server.py:1066-1185` — inline auth routes (ALL dead).
- `web_server.py:1190-1259` — inline dashboard pages (ALL dead).
- `web_server.py:1264-1338` — inline MQTT routes (ALL dead).
- `web_server.py:1340-1461` — inline system auto-restart routes (ALL dead; blueprint unauthenticated version wins).
- `web_server.py:1466-2110` — inline device CRUD routes (ALL dead).
- `web_server.py:2123-2162` — `/api/stats` **(ACTIVE — inline-only)**.
- `web_server.py:2167-2529` — inline Type A + mode_switching (ALL dead).
- `web_server.py:2575-2728` — inline mode_switching (ALL dead).
- `web_server.py:2731-2869` — inline event data / detector-status / sensor_data (ALL dead).
- `web_server.py:2872-3108` — inline debug / live_sensor_data / live_stream (ALL dead).
- `web_server.py:3141-3946` — inline avg-type B/C/D (ALL dead).
- `src/app/routes/__init__.py:4-23` — blueprint registration order.
- `src/app/routes/auth.py:12` — `auth_bp` definition.
- `src/app/routes/auth.py:85-91` — blueprint `login_required`.
- `src/app/routes/auth.py:94-207` — active auth endpoints.
- `src/app/routes/dashboard.py:5` — `dashboard_bp`.
- `src/app/routes/dashboard.py:8-68` — active HTML page routes.
- `src/app/routes/devices.py:7` — `devices_bp`.
- `src/app/routes/devices.py:27-200` — active device CRUD.
- `src/app/routes/events.py:9` — `events_bp`.
- `src/app/routes/events.py:12-423` — active Type A + mode_switching routes.
- `src/app/routes/sensors.py:11` — `sensors_bp`.
- `src/app/routes/sensors.py:47-277` — active sensor/live/SSE routes.
- `src/app/routes/mqtt_routes.py:13` — `mqtt_bp`.
- `src/app/routes/mqtt_routes.py:16-90` — active MQTT routes.
- `src/app/routes/algorithms.py:8` — `algorithms_bp`.
- `src/app/routes/algorithms.py:102-755` — active avg-type B/C/D routes.
- `src/app/routes/system.py:16` — `system_bp`.
- `src/app/routes/system.py:19-344` — active system pages and app-config routes.
- `src/app/routes/offsets.py:6` — `offsets_bp`.
- `src/app/routes/offsets.py:9-74` — active offsets routes.

### Summary for rewrites

If rewriting the UI, target the blueprint contract. All inline `@app.route` handlers in `web_server.py` are dead — they execute no user requests and can be deleted during a cleanup without functional change. The only inline-only active endpoint is `GET /api/stats` (`web_server.py:2123-2162`); it must either be ported to a blueprint or explicitly retained.

If rewriting the server from scratch, preserve the following wire contracts exactly:

1. The `{success, error|message, ...}` envelope.
2. The `{success, config: ...}` GET-config shape for all `/api/event/config/type_a`, `/api/mode_switching/config`, `/api/avg-type-?/config`, `/api/mqtt/config`, `/api/system/app-config` endpoints.
3. The asymmetric `configs` vs `configs_by_device` key naming on `/api/event/config/type_a/per_sensor` (single-device query returns `configs`; multi-device query returns `configs_by_device`).
4. Uniform `configs_by_device` on `/api/avg-type-?/config/per_sensor` responses.
5. SSE payload shape including `ts_encoding: "delta_ms"`, `seq_to`, and `sensor_modes`/`stats` overlay fields.
6. Raw-array responses (not enveloped) for `/api/avg-type-?/events` and `/api/avg-type-d/stage1-averages`.
7. Raw-dict response (not enveloped) for `/api/stats` with keys `total_tx`, `total_rx`, `source`, `uptime_seconds`, `counter_mode`.
8. All `/api/offsets/*` routes keyed on `<int:device_id>` path segment, not query param.
9. EventDetector dict keying by **stringified** device_id (`str(device_id)`) everywhere — a common source of bugs when clients send `?device_id=1` (matches) vs bare `1` (misses if not stringified).
