# HERMES Configuration Templates — Complete Reference

**Phase 0.5: Behavior Contract Capture**  
**Date:** 2024  
**Status:** Pre-rewrite documentation (exhaustive capture of current state)

---

## Table of Contents

1. [app_config.html — Application Configuration](#app_confightml--application-configuration)
2. [system_config.html — System Configuration](#system_confightml--system-configuration)
3. [ttl_config.html — Event TTL Configuration](#ttl_confightml--event-ttl-configuration)
4. [Cross-Template Analysis](#cross-template-analysis)
   - [WHERE_USED Map (app_config.html)](#where_used-map)
   - [Redundancy Map](#redundancy-map)
   - [Deletion Candidates](#deletion-candidates)

---

## app_config.html — Application Configuration

**File path:** `/home/embed/hammer/templates/app_config.html` (~1740 lines)

### Purpose

Comprehensive settings panel for all 90+ tunable system constants across the sensor dashboard backend. Users modify acquisition rates, detection thresholds, worker batch sizes, database pragmas, API timeouts, MQTT configuration, authentication settings, and UI display parameters. Changes take effect immediately (Live) or after server restart (Restart Required). This is the primary administrative interface for the system.

**Who uses it:** System administrators, field engineers, power users with access to the `/app-config` route.

---

### Layout

**Sidebar (lines 21–55):**
- Fixed 70px left navigation bar with icon buttons to Dashboard, Event Config, System Config, Offsets, App Config, and Logout.
- Active page highlighted with `#4299e1` blue background.

**Main Content (lines 666–713):**
- **Page header** (lines 670–683): Title "⚙️ Application Configuration", subtitle, global search box (🔍), "Expand All" / "Collapse All" buttons.
- **Filter bar** (lines 686–701): Category pill filter buttons (sorted by order in `CAT_META`), toggle checkboxes for "Show only changed" and "Restart required".
- **Config container** (lines 704–713): Loading spinner initially, then dynamically rendered category cards with parameter rows.

**Sticky bottom save bar (lines 718–725):**
- Shows count of unsaved changes ("X unsaved changes across all categories").
- "Save All Changes" button (gradient purple) and "Reset All to Defaults" button (red).
- Only visible when `dirtyValues` is non-empty.

**Modals (lines 731–759):**
- **Restart Required Modal** (731–744): Lists parameter keys requiring restart; offers "Copy restart command" button (copies `./stop.sh && ./run.sh`).
- **Reset All Confirmation Modal** (747–759): Warns user about resetting 90+ parameters.

---

### Category Structure & Field Groupings

**Categories (defined in `CAT_META`, lines 773–792):**

1. **Acquisition** (icon: 📡, order: 1) — Sample rates, buffer durations, data gaps
   - `sample_rate_hz`: int, default ?, unit "Hz"
   - `stm32_adc_topic`: string
   - `total_sensors`: int
   - `timestamp_drift_threshold_s`: float
   - `data_gap_reset_s`: float
   - `buffer_duration_a_s`: float
   - `buffer_duration_bcd_s`: float

2. **Detection** (icon: 🔍, order: 2) — Mode thresholds, transition debouncing
   - `startup_threshold`: int, unit "ADC"
   - `break_threshold`: int, unit "ADC"
   - `startup_duration_s`: float
   - `break_duration_s`: float

3. **Type A** (icon: 🅰️, order: 3) — Type A event settings
   - `type_a_ttl_s`: float
   - `type_a_debounce_s`: float
   - `type_a_timeframe_min_s`: float (validation)
   - `type_a_timeframe_max_s`: float (validation)

4. **Type B** (icon: 🅱️, order: 4) — Rolling average deviation
   - `type_b_t2_s`: float
   - `type_b_lower_pct`: float
   - `type_b_upper_pct`: float
   - `type_b_ttl_s`: float
   - `type_b_debounce_s`: float
   - `type_b_init_fill_ratio`: float
   - `type_b_window_headroom`: float

5. **Type C** (icon: 🔵, order: 5) — Range detection
   - `type_c_t3_s`: float
   - `type_c_lower`: int, unit "ADC"
   - `type_c_upper`: int, unit "ADC"
   - `type_c_ttl_s`: float
   - `type_c_debounce_s`: float
   - `type_c_init_fill_ratio`: float

6. **Type D** (icon: 🔷, order: 6) — Two-stage average (highest priority)
   - `type_d_t4_s`: float
   - `type_d_t5_s`: float
   - `type_d_lower_pct`: float
   - `type_d_upper_pct`: float
   - `type_d_ttl_s`: float
   - `type_d_debounce_s`: float
   - `type_d_1sec_buffer_slots`: int
   - `type_d_init_fill_ratio`: float
   - `type_d_per_sec_buffer_secs`: float

7. **Mode Switching** (icon: 🔀, order: 7) — (assumed empty or grouped with Detection)

8. **Live Stream** (icon: 📊, order: 8) — Server-side SSE push rates
   - `live_buffer_max_samples`: int
   - `live_stream_interval_s`: float
   - `live_wait_timeout_s`: float
   - `devices_cache_ttl_s`: float
   - `hardware_sample_rate_hz`: int

9. **MQTT** (icon: 📨, order: 9) — Broker config, QoS, topics
   - `mqtt_broker`: string
   - `mqtt_port`: int
   - `mqtt_keepalive_s`: int
   - `mqtt_websocket_url`: string
   - `mqtt_qos`: int (0 or 1)
   - `mqtt_consumer_queue_timeout_s`: float
   - `mqtt_base_topic`: string

10. **Database** (icon: 🗄️, order: 10) — SQLite pragmas, cache, WAL
    - `db_cache_size_kb`: int
    - `db_wal_checkpoint_pages`: int
    - `db_journal_size_limit_bytes`: int
    - `db_busy_timeout_ms`: int
    - `db_recovery_cooldown_s`: float
    - `db_connection_timeout_s`: float
    - `db_busy_timeout_read_ms`: int

11. **Server** (icon: 🖥️, order: 11) — Gunicorn config
    - `server_port`: int
    - `server_workers`: int
    - `server_threads`: int
    - `server_timeout_s`: int

12. **Workers** (icon: ⚙️, order: 12) — Event batch processing
    - `worker_batch_size`: int
    - `worker_batch_size_a`: int
    - `worker_batch_size_snapshot`: int
    - `worker_batch_size_update`: int
    - `worker_db_batch_size`: int
    - `worker_queue_timeout_s`: float
    - `worker_sleep_on_error_s`: float
    - `worker_sleep_idle_snapshot_s`: float
    - `worker_sleep_idle_update_s`: float
    - `worker_batch_flush_interval_s`: float
    - `queue_maxlen_a`: int
    - `queue_maxlen_bcd`: int
    - `queue_snapshot_maxlen`: int
    - `event_history_maxlen`: int
    - `variance_window_deque_secs`: float

13. **API** (icon: 🔌, order: 13) — API limits & defaults
    - `api_event_list_default_limit`: int
    - `api_live_sensor_max_samples`: int
    - `api_live_sensor_cap`: int

14. **Device** (icon: 📟, order: 14) — Device registration, timeouts
    - `device_active_timeout_s`: float
    - `device_poll_interval_s`: float
    - `device_thread_join_timeout_s`: float
    - `device_max_count`: int
    - `device_max_sensors`: int
    - `stm32_device_name`: string

15. **UI** (icon: 🖼️, order: 15) — Frontend display settings
    - `ui_default_live_window_ms`: int
    - `ui_rolling_win_secs`: float
    - `ui_change_epsilon`: int, unit "ADC"
    - `ui_max_buffer_points`: int
    - `ui_live_export_max`: int
    - `ui_live_table_update_ms`: int
    - `ui_zoom_min_ms`: int
    - `ui_zoom_max_ms`: int
    - `ui_echarts_large_threshold`: int
    - `ui_fetch_timeout_ms`: int
    - `ui_fetch_retries`: int
    - `ui_event_list_max_fetch`: int
    - `ui_status_update_interval_ms`: int
    - `ui_event_poll_interval_ms`: int
    - `ui_toast_auto_close_ms`: int
    - `ui_api_event_fetch_timeout_ms`: int
    - `ui_api_live_data_timeout_ms`: int
    - `ui_api_backoff_base_ms`: int
    - `ui_pattern_time_window_ms`: int

16. **Logging** (icon: 📝, order: 16) — Event log rotation
    - `log_max_bytes`: int
    - `log_backup_count`: int

17. **Auth** (icon: 🔐, order: 17) — OTP settings
    - `otp_expiry_s`: int
    - `otp_max_attempts`: int
    - `otp_resend_s`: int
    - `otp_max_per_hour`: int
    - `otp_rate_limit_window_s`: int

18. **Validation** (icon: ✅, order: 18) — API-level constraint constants
    - `type_a_timeframe_min_s`: float
    - `type_a_timeframe_max_s`: float

19. **Auto-Restart** (icon: ?, implied from schema) — (if present, separate from "System Config")
    - `auto_restart_check_interval_s`: float
    - `auto_restart_default_hour`: int
    - `auto_restart_default_minute`: int
    - `auto_restart_thread_join_s`: float

20. **Misc** — Convenience constants
    - `ref_value`: int, unit "ADC" (tolerance band reference)

---

### Field Inventory (Complete)

**Data Types:**
- `int`: renders as `<input type="number" step="1">`
- `float`: renders as `<input type="number" step="any">`
- `bool`: renders as custom toggle switch (`.bool-track` button)
- `string`: renders as `<input type="text">`

**Field Structure (per param):**

```
{
  key: string,                      // e.g., "sample_rate_hz"
  label: string,                    // e.g., "Sample Rate"
  value: (int|float|bool|string),   // current value in database
  default_val: (int|float|bool|string), // factory default
  data_type: "int" | "float" | "bool" | "string",
  description: string,              // one-liner shown in UI
  requires_restart: bool,           // true = restarts needed
  updated_at: string (ISO 8601)?    // last update timestamp (if available)
}
```

**Example Parameters (sampled):**

1. **sample_rate_hz**
   - ID: `sample_rate_hz`
   - Label: `Sample Rate`
   - Type: `int`
   - Unit: `Hz`
   - Default: (not visible in template, fetch from backend)
   - Description: "Sample rate in Hertz from STM32 ADC"
   - Requires Restart: `true` (implied from WHERE_USED)
   - WHERE_USED: `event_detector.py` — sizes circular buffers

2. **total_sensors**
   - ID: `total_sensors`
   - Label: (implied "Total Sensors")
   - Type: `int`
   - Unit: `channels`
   - Description: "Number of sensor channels to allocate"
   - Requires Restart: `true`

3. **mqtt_broker**
   - ID: `mqtt_broker`
   - Label: (implied "MQTT Broker")
   - Type: `string`
   - Example value: `localhost` or `192.168.1.115`
   - Description: "MQTT broker hostname or IP"
   - Requires Restart: `true` (implied)

4. **type_a_ttl_s**
   - ID: `type_a_ttl_s`
   - Label: "Event A Marker TTL"
   - Type: `float`
   - Unit: `s`
   - Description: "Frontend-only: Event A marker visibility on live graph"
   - Requires Restart: `false` (Live)

5. **otp_expiry_s**
   - ID: `otp_expiry_s`
   - Label: "OTP Expiry"
   - Type: `int`
   - Unit: `s`
   - Default: `300` (5 minutes)
   - Description: "How long an OTP code remains valid"
   - Requires Restart: `false`

**Default values & validation:**
- For each parameter, the backend returns `default_val` (factory reset value).
- Frontend tracks `origValues` (snapshot on load) to detect changes.
- `dirtyValues` object accumulates unsaved edits.
- Units are pulled from the `UNITS` constant (lines 1032–1082).

---

### Endpoint Inventory

All endpoints are `JSON-based` with standardized response format:
```json
{ "success": true, "...": "..." }
```

#### **1. Load Configuration**
- **Method:** `GET`
- **Endpoint:** `/api/system/app-config`
- **Call site:** `loadConfig()` (line 1095)
- **Request body:** None
- **Response:**
  ```json
  {
    "success": true,
    "config": {
      "Acquisition": [
        { "key": "sample_rate_hz", "value": 100, "default_val": 100, "data_type": "int", "label": "Sample Rate", "description": "...", "requires_restart": true },
        ...
      ],
      "Detection": [ ... ],
      ...
    }
  }
  ```
- **Response handling:**
  - Extract `data.config` into `allConfig` (nested by category).
  - Snapshot all values into `origValues` (line 1105).
  - Call `renderConfig()`, `buildPills()`, `restoreCollapseState()`.
  - Show loading spinner during fetch, hide on complete.

#### **2. Save Configuration (Category or All)**
- **Method:** `POST`
- **Endpoint:** `/api/system/app-config`
- **Call sites:**
  - `saveCategory(cat)` (line 1475) — saves only dirty keys in `cat`.
  - `saveAll()` (line 1488) — saves all dirty keys in `dirtyValues`.
  - Both call `performSave(updates)` (line 1496).
- **Request body:**
  ```json
  {
    "updates": {
      "key1": newValue,
      "key2": newValue,
      ...
    }
  }
  ```
- **Response:**
  ```json
  {
    "success": true,
    "saved": ["key1", "key2"],
    "requires_restart": boolean,
    "restart_keys": ["key_requiring_restart", ...] (optional)
  }
  ```
- **Response handling:**
  - On success: update `origValues` with saved values.
  - Call `clearDirty(saved)` to unhighlight rows and remove dirty button state.
  - Show success toast: `✓ Saved N parameter(s) successfully.`
  - If `data.requires_restart`: show modal listing restart keys.
- **Error handling:** Show error toast on `!data.success` or network error.

#### **3. Reset Keys**
- **Method:** `POST`
- **Endpoint:** `/api/system/app-config/reset`
- **Call sites:**
  - `resetKeys(keys)` (line 1536) — reset specific keys if `keys` array provided, or all if `keys == null`.
  - `resetField(key)` (line 1556) — reset a single field.
  - `confirmResetCategory(cat)` (line 1521) — confirm then call `resetKeys(params.map(p => p.key))`.
  - `resetAll()` (line 1531) — confirm via modal then call `resetKeys(null)`.
- **Request body:**
  ```json
  {
    "keys": ["key1", "key2"] or {}
  }
  ```
  - Empty object `{}` = reset all parameters.
- **Response:**
  ```json
  {
    "success": true,
    "keys_reset": ["key1", "key2"] or "all"
  }
  ```
- **Response handling:**
  - On success: reload full config via `loadConfig()` (line 1548).
  - For single field reset: fetch current config, update input element's value, remove `.changed` class.
  - Show toast: `↺ Reset {N} parameter(s) to defaults.`
  - Clear `dirtyValues` and update save bar.

---

### Save/Reset Flow

**Dirty Tracking (lines 1385–1463):**

1. **On input change:**
   - User edits a field (`<input>` or `<button.bool-track>`).
   - Event listeners: `onchange` and `oninput` (line 1309) or `onclick` for toggle.
   - Calls `onInputChange(input)` (line 1385) or `toggleBool(btn)` (line 1392).
   - Function extracts `data-key` and new value, calls `markDirty(key, value, inputEl)`.

2. **Mark dirty (lines 1401–1421):**
   - Add key to `dirtyValues` object.
   - Add `.dirty-row` class to the param row (yellow left border + light yellow background).
   - Show `.pill-dirty` ("● Modified" badge).
   - Add `.changed` class to input field (yellow border).
   - Add `.dirty` class to category save button (triggers pulse animation).
   - Call `updateSaveBar()` to show sticky bottom bar.

3. **Clear dirty (lines 1423–1455):**
   - Called after successful save or reset.
   - Remove key from `dirtyValues`.
   - Only un-highlight row if value matches original (not just current database value).
   - Remove `.dirty` class from category save button if no other keys in that category are dirty.

4. **Confirm dialogs:**
   - **Category reset:** `confirm()` browser dialog (line 1523).
   - **All reset:** `confirmResetAll()` → modal (line 1527) → user clicks "Reset Everything" to proceed (line 1531).
   - **Restart:** Auto-shown modal if save returns `requires_restart` (line 1513).

5. **Polling/Auto-refresh:**
   - No automatic polling. Configuration is loaded once on page load and cached in `allConfig`.
   - Users must manually reload (F5) to fetch latest backend values if another user changed them.

6. **Save bar visibility (lines 1457–1463):**
   - `.save-bar` slides in from bottom when `dirtyValues` has items.
   - `transform: translateY(0)` when `.visible` class is present.
   - Shows count: "{N} unsaved change(s) across all categories".

---

### JavaScript Function Index

| Line | Function | Purpose |
|------|----------|---------|
| 1087–1090 | `DOMContentLoaded` event | Initialize: load config, wire up global search |
| 1095–1114 | `loadConfig()` | Fetch `/api/system/app-config`, render all sections, restore collapse state |
| 1116–1118 | `showLoading(on)` | Toggle loading spinner visibility |
| 1120–1145 | `buildPills()` | Dynamically create category filter buttons |
| 1147–1153 | `sortedCategories()` | Return categories sorted by `CAT_META[cat].order` |
| 1155–1166 | `renderConfig()` | Render all category cards into container |
| 1168–1216 | `buildCatCard(cat, params)` | Build a single collapsible category card with header & body |
| 1218–1272 | `buildParamRow(param)` | Build a single parameter row (left + right sides) |
| 1274–1315 | `buildInput(key, value, dataType, unit, isChanged)` | Generate HTML for input field (bool toggle or text/number input) |
| 1320–1326 | `toggleCard(card)` | Toggle `.open` class on category card, save state to localStorage |
| 1328–1334 | `expandAll()` | Open all visible category cards |
| 1336–1342 | `collapseAll()` | Close all category cards |
| 1344–1350 | `saveCollapseState()` | Serialize card open/closed state to `localStorage.appconfig_collapse` |
| 1352–1364 | `restoreCollapseState()` | Load saved collapse state from localStorage, or expand all if not found |
| 1366–1372 | `toggleDesc(key)` | Toggle description clamp (2 lines vs full text) |
| 1374–1380 | `toggleWhere(key)` | Toggle WHERE_USED panel visibility for a parameter |
| 1385–1390 | `onInputChange(input)` | Event handler for input field changes; calls `markDirty()` |
| 1392–1399 | `toggleBool(btn)` | Toggle bool switch; update label ("Enabled"/"Disabled"), call `markDirty()` |
| 1401–1421 | `markDirty(key, value, inputEl)` | Mark key as dirty; update UI (row, pill, button); show save bar |
| 1423–1455 | `clearDirty(keys)` | Remove keys from dirty state; clean up UI highlighting if not changed vs original |
| 1457–1463 | `updateSaveBar()` | Show/hide save bar based on `dirtyValues` count; update label |
| 1465–1470 | `castValue(raw, dtype)` | Parse input string to int/float/bool based on data type |
| 1475–1486 | `saveCategory(cat)` | Extract dirty keys in category, call `performSave()` |
| 1488–1494 | `saveAll()` | Call `performSave()` with all dirty keys |
| 1496–1519 | `performSave(updates)` | POST updates to `/api/system/app-config`; handle response & restart modal |
| 1521–1525 | `confirmResetCategory(cat)` | Confirm via `confirm()` dialog, call `resetKeys()` with category's keys |
| 1527–1529 | `confirmResetAll()` | Show reset confirmation modal |
| 1531–1534 | `resetAll()` | Close modal, call `resetKeys(null)` (all) |
| 1536–1554 | `resetKeys(keys)` | POST reset request; reload config; clear dirtyValues |
| 1556–1593 | `resetField(key)` | Reset single field; update input element, remove dirty state |
| 1595–1601 | `services_getParam(key)` | Lookup parameter object by key across all categories |
| 1606–1645 | `applyFilters()` | Apply search, changed, restart, and category pill filters |
| 1650–1666 | `showToast(type, message)` | Create and show toast notification (auto-dismiss after 3.5 s for success) |
| 1668–1672 | `dismissToast(toast)` | Remove toast with slide-out animation |
| 1677–1681 | `showRestartModal(keys)` | Populate and show restart modal with list of affected keys |
| 1683–1685 | `closeModal(id)` | Hide modal by removing `.open` class |
| 1687–1695 | `copyRestartCmd()` | Copy `./stop.sh && ./run.sh` to clipboard; fallback to `prompt()` |
| 1698–1702 | Modal overlay click handler | Close modal on background click |
| 1707–1712 | `doLogout(e)` | POST `/api/auth/logout`, redirect to `/login` |
| 1717–1719 | `slugify(str)` | Convert string to CSS-safe slug (lowercase, alphanumeric + hyphens) |
| 1721–1727 | `escHtml(s)` | HTML-escape string (`&`, `<`, `>`, `"`) |
| 1729–1731 | `escAttr(s)` | Attribute-escape string (`"`, `'`) |
| 1733–1736 | `debounce(fn, ms)` | Utility to debounce function (used for global search input) |

---

### Hardcoded Values

1. **Restart command** (line 1688): `'./stop.sh && ./run.sh'` — copied to clipboard when user clicks "Copy restart command".
2. **localStorage key** (line 1349): `'appconfig_collapse'` — stores expand/collapse state of category cards.
3. **Color scheme:**
   - Gradient purple buttons: `linear-gradient(135deg, #667eea 0%, #764ba2 100%)` (lines 227, 440, 540)
   - Blue accent: `#4299e1` (sidebar active, field focus)
   - Green (enabled/live): `#48bb78`
   - Yellow (restart required): `#fef3c7` / `#ecc94b`
4. **Toast auto-dismiss timeout** (line 1664): `3500` ms for success toast.
5. **Debounce timeout for search** (line 1089): `150` ms.
6. **Parameter count estimate** (line 751): "**all 90+ configuration parameters**" mentioned in reset modal.
7. **Time zone in modals:** Not hardcoded; handled by backend or UTC.

---

### Quirks & Issues

1. **Frontend-only parameters mixed with backend parameters:**
   - `type_a_ttl_s`, `type_b_ttl_s`, `type_c_ttl_s`, `type_d_ttl_s` are marked as frontend-only ("Event X marker lifetime on live graph") in WHERE_USED (lines 824, 834, 848, 862), yet stored in the app config database. They could be removed from this page since they're also in `ttl_config.html`.

2. **WHERE_USED is hardcoded in JavaScript (lines 795–1029):**
   - 100+ entries mapping config keys to detailed descriptions.
   - If the Python backend code changes, these descriptions become stale.
   - No automatic sync; risk of docs going out of sync.

3. **Restart modal doesn't auto-close:**
   - User must click "Copy restart command" or "I'll restart later" manually.
   - No auto-dismiss or countdown.

4. **No dirty detection across page reloads:**
   - If user edits but doesn't save, then reloads the page, all edits are lost silently.
   - No warning dialog on page unload.

5. **Collapse state stored in localStorage per browser:**
   - Doesn't sync across devices.
   - `restoreCollapseState()` (line 1352) silently expands all if localStorage is empty or corrupt.

6. **Search is client-side only:**
   - Must load entire config before searching.
   - No server-side search/filtering; slow on very large configs.

7. **No conflict resolution:**
   - If User A and User B both modify the same parameter and save, User B's value wins (last-write-wins).
   - No warning or merge strategy.

8. **Mock units:** Some units in `UNITS` (e.g., `ratio`, `slots`, `tries`) are cosmetic only — no validation against these units.

9. **Boolean parameters store as 1/0/true/false inconsistently:**
   - Line 1253: `default_val === true || default_val === 'true' || default_val === '1'` suggests backend may return mixed types.

10. **Reset all modal counts parameters:**
    - Modal says "all 90+ configuration parameters" (line 751), but this is hardcoded.
    - Actual count comes from the backend and varies — no sync with UI text.

11. **No validation on input values:**
    - Frontend only calls `parseInt()` / `parseFloat()` — doesn't check min/max.
    - Server validates; frontend errors only show in toast (generic "Save failed: ...").

12. **Global search doesn't highlight matches:**
    - Filters rows but doesn't mark matched text.
    - User must scroll to find why a row is shown.

---

### Browser Compatibility Notes

- **localStorage:** Required for collapse state. Older IE may fail silently (caught in try/catch, line 1349).
- **Modern CSS:** Uses CSS Grid (`grid-template-columns: 1fr 380px`), Flexbox, CSS variables (implicitly via hex colors).
- **JavaScript:** ES6 (arrow functions, template literals, destructuring).

---

---

## system_config.html — System Configuration

**File path:** `/home/embed/hammer/templates/system_config.html` (~431 lines)

### Purpose

Specialized page for auto-restart scheduling. Allows administrators to enable daily automatic server restarts at a configurable time (24-hour format) to manage memory usage and clear fragmentation. Shows current restart status, last restart timestamp, and next scheduled restart time.

**Who uses it:** System administrators monitoring server health.

---

### Layout

**Sidebar (lines 223–267):**
- Same fixed 70px sidebar as `app_config.html`.
- System Configuration page is marked `.active`.

**Main Content:**
- **Page header** (lines 272–282): Heading "System Configuration" with subtitle "Auto-restart schedule and server health settings".
- **Quick link card** (lines 285–292): Colored link to `/app-config` (App Config) with icon ⚙️ and summary "Edit all 117 tunable system constants — sample rates, buffers, thresholds, MQTT, UI, Workers, and more". (Note: count "117" is hardcoded; actual count may differ.)
- **Auto-Restart Configuration section** (lines 295–353):
  - Status badge (lines 297–298): "ENABLED" (green) or "DISABLED" (gray).
  - Warning alert (lines 301–304).
  - Toggle for "Auto-Restart" (lines 308–317).
  - Restart time input (lines 319–327): two `<input type="number">` fields for hour (0–23) and minute (0–59) separated by `:`.
  - Status info grid (lines 329–342): Three display boxes showing "Current Status", "Last Restart", "Next Restart".
  - Save button (lines 344–352): Single button (no category-level granularity like app_config.html).

---

### Field Inventory

| ID | Label | Type | Default | Unit | Validation | Description |
|----|-------|------|---------|------|-----------|-------------|
| `autoRestartToggle` | Auto-Restart | bool | (from server) | — | — | Toggle switch to enable/disable auto-restart |
| `restartHour` | (part of "Restart Time") | int | (from server) | h | 0–23 | Hour in 24-hour format |
| `restartMinute` | (part of "Restart Time") | int | (from server) | min | 0–59 | Minute |

**Notes:**
- No separate "enable" checkbox per parameter; auto-restart is a feature flag (enabled/disabled) plus a scheduled time.
- No "Save Category" or "Reset to Defaults" button — only a single "Save Configuration" action button.
- Status display (read-only): "Current Status", "Last Restart" (ISO 8601 formatted), "Next Restart" (calculated by server).

---

### Endpoint Inventory

#### **1. Load Auto-Restart Configuration**
- **Method:** `GET`
- **Endpoint:** `/api/system/auto-restart/config`
- **Call site:** `loadConfig()` (line 360)
- **Request body:** None
- **Response:**
  ```json
  {
    "success": true,
    "config": {
      "enabled": boolean,
      "restart_hour": int (0–23),
      "restart_minute": int (0–59),
      "last_restart": string (ISO 8601 timestamp) or null
    },
    "status": {
      "next_restart": string or "N/A"
    }
  }
  ```
- **Response handling:**
  - Store `data.config` in `currentConfig`.
  - Populate form fields from config values.
  - Update status badge (line 373–375).
  - Format `last_restart` to locale string with Asia/Kolkata timezone (line 382–383).
  - Display `status.next_restart` or "N/A" (line 386).

#### **2. Save Auto-Restart Configuration**
- **Method:** `POST`
- **Endpoint:** `/api/system/auto-restart/config`
- **Call site:** `saveConfig()` (line 392)
- **Request body:**
  ```json
  {
    "enabled": boolean,
    "restart_hour": int (0–23),
    "restart_minute": int (0–59)
  }
  ```
- **Response:**
  ```json
  {
    "success": true
  }
  ```
- **Response handling:**
  - On success: show success toast "Saved successfully." (line 409).
  - Schedule `loadConfig()` to refresh after 800 ms (line 409).
  - On error: show error toast with `data.error` message (line 410).

---

### Save/Reset Flow

**No dirty tracking or multi-step UI like app_config.html.**

**Direct save flow:**

1. User edits hour/minute or toggles the checkbox.
2. User clicks "Save Configuration" button.
3. `saveConfig()` (line 392) is called.
4. **Validation** (lines 393–396):
   - Hour: `0 ≤ hour ≤ 23` → error toast if invalid.
   - Minute: `0 ≤ minute ≤ 59` → error toast if invalid.
5. **POST request** to `/api/system/auto-restart/config` with current form values.
6. **On success:** Show success toast, schedule `loadConfig()` after 800 ms to refresh status displays.
7. **On error:** Show error toast.

**No reset to defaults button.**  
**No modal dialogs.**  
**No sticky bottom save bar.**

---

### JavaScript Function Index

| Line | Function | Purpose |
|------|----------|---------|
| 360–390 | `loadConfig()` | Fetch config from server; populate form; update status displays |
| 362–364 | Server GET + `.json()` | Fetch `/api/system/auto-restart/config` |
| 365–375 | Badge update | Set status badge text and class based on `enabled` |
| 377–386 | Status displays | Format `last_restart` timestamp (Asia/Kolkata TZ) and `next_restart` |
| 392–412 | `saveConfig()` | Validate hour/minute, POST to server, show toast, reload |
| 393–396 | Input validation | Check hour (0–23) and minute (0–59) |
| 398–407 | POST request | Send form data to `/api/system/auto-restart/config` |
| 409–410 | Success/error handling | Show toast, reload on success or error on failure |
| 414–420 | `showMsg(type, msg)` | Create and auto-hide message box |
| 422–425 | `doLogout()` | POST logout, redirect to `/login` |
| 427 | `loadConfig()` | Initial load on page load |
| 428 | `setInterval(loadConfig, 60000)` | Refresh config every 60 seconds |

---

### Hardcoded Values

1. **Timezone for last restart display** (line 382): `'Asia/Kolkata'` — hardcoded for a specific region.
2. **Message box hide timeout** (line 419): `5000` ms (5 seconds) auto-hide.
3. **Reload delay after save** (line 409): `800` ms before calling `loadConfig()`.
4. **Auto-refresh interval** (line 428): `60000` ms (60 seconds).
5. **Default restart time hint** (line 326): "Default: 03:00 — recommended for minimal user impact."
6. **App Config link text** (line 289): Hardcoded count "**all 117 tunable system constants**".

---

### Quirks & Issues

1. **Hardcoded Asia/Kolkata timezone:**
   - Line 382: `toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' })`.
   - Non-configurable; assumes deployment in Indian region.
   - May be confusing for global deployments.

2. **Auto-refresh every 60 seconds:**
   - Continuously reloads config without user action.
   - May show stale "Last Restart" or "Next Restart" until refresh completes.
   - No visual indicator of refresh in progress.

3. **Message box is crude:**
   - Single `<div id="messageBox">` for all alerts.
   - No styling or icons; relies on `.alert-success`, `.alert-error`, `.alert-warning` classes.
   - Compared to app_config.html's toast system, this is bare-bones.

4. **No error details:**
   - Line 410: Error toast just shows `data.error` without context.
   - If server returns no error message, toast will say "Save failed: undefined".

5. **No "Reset" or "Discard changes" button:**
   - User must reload page to undo edits.
   - No warning on page unload.

6. **Validation is frontend-only:**
   - Server should validate hour/minute again, but no error handling for server-side validation.

7. **Status displays never get stale:**
   - `loadConfig()` runs every 60 seconds, but if network fails, old values persist.

---

---

## ttl_config.html — Event TTL Configuration

**File path:** `/home/embed/hammer/templates/ttl_config.html` (~327 lines)

### Purpose

Specialized page for configuring Event TTL (Time To Live) — how long each event type must remain stable before being saved to the database. Four separate cards for Events A, B, C, D with individual TTL window input fields and save buttons. Includes detailed explanation of TTL filtering and event priority hierarchy.

**Who uses it:** Field engineers tuning event detection sensitivity.

---

### Layout

**Framework:** Jinja2 template extending `base.html` (line 1: `{% extends "base.html" %}`).

**No custom sidebar** — assumes `base.html` provides navigation.

**Main Content:**

- **Page header** (lines 6–13):
  - Title: "Event TTL (Time To Live) Configuration"
  - Subtitle: "Configure how long events must remain stable before being saved to database (noise filtering)"

- **Type A & B cards** (lines 16–61):
  - Two-column grid (`.row`, `.col-md-6`).
  - Each card: header with colored background (blue for A, cyan for B), form-group with number input, save button, status div.

- **Type C & D cards** (lines 64–108):
  - Another two-column grid.
  - C: yellow/warning header; D: red/danger header (marked "HIGHEST PRIORITY").

- **TTL Behavior Explanation card** (lines 111–149):
  - Dark header ("How TTL Works").
  - Ordered list of 5 rules.
  - Priority hierarchy (D > C > B > A).
  - Detailed bullet examples.
  - `<pre>` block showing timeline example with ASCII art.

---

### Field Inventory

| ID | Label | Type | Default | Unit | Step | Min | Max | Description |
|----|-------|------|---------|------|------|-----|-----|-------------|
| `type-a-ttl` | TTL Window (seconds) | number | 5.0 | s | 0.1 | 0.1 | 60 | Event A noise-filtering TTL |
| `type-b-ttl` | TTL Window (seconds) | number | 10.0 | s | 0.1 | 0.1 | 60 | Event B post-window deviation TTL |
| `type-c-ttl` | TTL Window (seconds) | number | 3.0 | s | 0.1 | 0.1 | 60 | Event C range detection TTL |
| `type-d-ttl` | TTL Window (seconds) | number | 8.0 | s | 0.1 | 0.1 | 60 | Event D two-stage average (highest priority) TTL |

**Notes:**
- All fields are `<input type="number" step="0.1" min="0.1" max="60">`.
- Default values are hardcoded in HTML (`value="5.0"`, etc.), not fetched from server initially.
- Help text below each input explains what the TTL window does.

---

### Endpoint Inventory

#### **1. Load Type A TTL Configuration**
- **Method:** `GET`
- **Endpoint:** `/api/event/config/type_a`
- **Call site:** `loadTTLConfigs()` (line 161)
- **Request body:** None
- **Response:**
  ```json
  {
    "success": true,
    "config": {
      "ttl_seconds": float,
      "timeframe_seconds": float,
      "tolerance_pct": float or "threshold_lower": float,
      "enabled": boolean
    }
  }
  ```
- **Response handling:** Extract `config.ttl_seconds`, set `document.getElementById('type-a-ttl').value` (line 163).

#### **2. Load Type B TTL Configuration**
- **Method:** `GET`
- **Endpoint:** `/api/avg-type-b/config`
- **Call site:** `loadTTLConfigs()` (line 166)
- **Response:** Similar to Type A.
- **Response handling:** Set `type-b-ttl` input value (line 169).

#### **3. Load Type C TTL Configuration**
- **Method:** `GET`
- **Endpoint:** `/api/avg-type-c/config`
- **Call site:** `loadTTLConfigs()` (line 172)
- **Response:** Similar structure.
- **Response handling:** Set `type-c-ttl` input value (line 175).

#### **4. Load Type D TTL Configuration**
- **Method:** `GET`
- **Endpoint:** `/api/avg-type-d/config`
- **Call site:** `loadTTLConfigs()` (line 178)
- **Response:** Similar structure.
- **Response handling:** Set `type-d-ttl` input value (line 181).

#### **5. Save Type A TTL**
- **Method:** `POST`
- **Endpoint:** `/api/event/config/type_a`
- **Call site:** `saveTypeATTL()` (line 190)
- **Request body:**
  ```json
  {
    "timeframe_seconds": float (from current config),
    "tolerance_pct": float or "threshold_lower": float,
    "enabled": boolean (from current config),
    "ttl_seconds": float (new value from input)
  }
  ```
- **Response:**
  ```json
  {
    "success": true
  }
  ```
- **Response handling:**
  - On success: show status div with `<div class="alert alert-success">✓ Type A TTL saved: Xs</div>` (line 213).
  - On error: show `<div class="alert alert-danger">✗ Error: ...message...</div>` (line 218).

#### **6. Save Type B TTL**
- **Method:** `PUT` (differs from POST for Type A!)
- **Endpoint:** `/api/avg-type-b/config`
- **Call site:** `saveTypeBTTL()` (line 222)
- **Request body:**
  ```json
  {
    "T2": float (t2_seconds from current),
    "lower_tolerance_pct": float,
    "upper_tolerance_pct": float,
    "enabled": boolean,
    "ttl_seconds": float (new)
  }
  ```
- **Response:** Similar success/error handling (lines 243–250).

#### **7. Save Type C TTL**
- **Method:** `PUT`
- **Endpoint:** `/api/avg-type-c/config`
- **Call site:** `saveTypeCTTL()` (line 253)
- **Request body:**
  ```json
  {
    "T3": float (t3_seconds from current),
    "lower_threshold": float,
    "upper_threshold": float,
    "enabled": boolean,
    "ttl_seconds": float (new)
  }
  ```
- **Response:** Similar (lines 274–281).

#### **8. Save Type D TTL**
- **Method:** `PUT`
- **Endpoint:** `/api/avg-type-d/config`
- **Call site:** `saveTypeDTTL()` (line 284)
- **Request body:**
  ```json
  {
    "T4": float (t4_seconds from current),
    "lower_threshold": float,
    "upper_threshold": float,
    "enabled": boolean,
    "ttl_seconds": float (new)
  }
  ```
- **Response:** Similar (lines 305–312).

---

### Save/Reset Flow

**Fetch-on-click pattern (no pooling or auto-load):**

1. **Page load** (line 154: `DOMContentLoaded`):
   - Calls `loadTTLConfigs()`.

2. **Load all TTL configs:**
   - 4 parallel `fetch()` calls to load current values for Types A, B, C, D (lines 160–182).
   - Extract `config.ttl_seconds` and populate respective input fields.
   - Errors are logged to console (line 186) but don't block other loads.

3. **User edits a TTL value** (e.g., Type A):
   - User changes `<input id="type-a-ttl">` value.
   - No dirty tracking; form is not marked as changed.

4. **User clicks "Save Type A TTL"**:
   - Calls `saveTypeATTL()` (line 190).
   - **GET current config** from server (line 196): fetch `/api/event/config/type_a`.
   - **POST updated config** (line 200): send with TTL + preserved other fields.
   - **On success:** Show green status div (line 213).
   - **On error:** Show red status div with error message (line 218).

5. **Status div auto-hide:**
   - No auto-dismiss. User must manually reload or navigate away to clear status message.

---

### JavaScript Function Index

| Line | Function | Purpose |
|------|----------|---------|
| 154–156 | `DOMContentLoaded` event | Load all TTL configs on page load |
| 158–188 | `loadTTLConfigs()` | Fetch current TTL values from all 4 event type endpoints |
| 160–164 | Type A load | GET `/api/event/config/type_a`, populate `#type-a-ttl` |
| 166–170 | Type B load | GET `/api/avg-type-b/config`, populate `#type-b-ttl` |
| 172–176 | Type C load | GET `/api/avg-type-c/config`, populate `#type-c-ttl` |
| 178–182 | Type D load | GET `/api/avg-type-d/config`, populate `#type-d-ttl` |
| 184–187 | Error logging | Log fetch errors to console |
| 190–220 | `saveTypeATTL()` | Fetch current Type A config, update TTL, POST, show status |
| 195–197 | Fetch current | GET `/api/event/config/type_a` |
| 200–210 | POST update | POST `/api/event/config/type_a` with preserved fields + new TTL |
| 222–251 | `saveTypeBTTL()` | Similar pattern but uses PUT method; preserves T2 and tolerances |
| 253–282 | `saveTypeCTTL()` | Similar pattern; preserves T3 and thresholds |
| 284–313 | `saveTypeDTTL()` | Similar pattern; preserves T4 and thresholds |

---

### Hardcoded Values

1. **Default input values** (HTML, not JavaScript):
   - Type A: `value="5.0"` (line 26)
   - Type B: `value="10.0"` (line 49)
   - Type C: `value="3.0"` (line 74)
   - Type D: `value="8.0"` (line 96)

2. **Input constraints** (HTML):
   - All: `step="0.1" min="0.1" max="60"` (lines 26, 49, 74, 95)

3. **Priority hierarchy text** (line 127):
   - "Priority Hierarchy: D (highest) > C > B > A (lowest)"

4. **Example timeline** (lines 138–145):
   - ASCII example showing event firing and TTL expiry.

---

### Quirks & Issues

1. **Inconsistent HTTP methods:**
   - Type A: `POST` (line 200)
   - Types B, C, D: `PUT` (lines 230, 261, 292)
   - No clear reason; likely historical API design debt.

2. **Fetch-on-save pattern is inefficient:**
   - Before saving, the page must GET current config to preserve other fields.
   - If the user changes the TTL and immediately saves, there's a race condition if another user modified other fields.

3. **No dirty tracking:**
   - User can edit multiple input fields but only save one at a time.
   - No indication of which fields are unsaved.

4. **Status div has no auto-dismiss:**
   - Message lingers until user navigates away or manually reloads.
   - In `system_config.html`, messages auto-hide after 5 seconds; inconsistent UX.

5. **Load errors silently logged:**
   - Line 186: `console.error()` if a fetch fails.
   - User doesn't see that loading failed; input fields retain defaults (5.0, 10.0, etc.).

6. **HTML defaults override server values on error:**
   - If server is down, user sees hardcoded defaults (5.0s, 10.0s, etc.).
   - Confusing if production server has different settings.

7. **TTL fields are frontend-only parameters:**
   - These are also in `app_config.html` as `type_a_ttl_s`, `type_b_ttl_s`, etc.
   - Redundancy: users can change TTLs in two places, causing confusion.

8. **No validation feedback:**
   - Min value is 0.1, max is 60 (HTML constraints).
   - Server doesn't validate ranges; if user enters invalid input, they may get opaque backend errors.

9. **Bootstrap styling inconsistency:**
   - Uses Bootstrap classes (`.card`, `.card-header`, `.btn`, `.form-control`, `.alert`).
   - Other pages (app_config, system_config) use custom CSS with Space Grotesk font.
   - Visual inconsistency across the app.

---

---

## Cross-Template Analysis

### WHERE_USED Map (app_config.html)

The `WHERE_USED` constant in app_config.html (lines 795–1029) provides detailed backend usage for 80+ configuration keys. This is a curated mapping of config keys to their backend readers.

**Key Structure (example):**

```javascript
sample_rate_hz: `<strong>File:</strong> <code>event_detector.py</code> — sizes circular buffers...`
```

**Notable entries (sampled):**

| Config Key | Primary File(s) | Impact | Restart? |
|------------|-----------------|--------|----------|
| `sample_rate_hz` | `event_detector.py`, `avg_type_b/c/d.py` | Buffer sizing | Yes |
| `total_sensors` | `web_server.py`, `logging_config.py` | Data structure allocation | Yes |
| `stm32_adc_topic` | `web_server.py` | MQTT subscription string | Yes |
| `type_a_ttl_s` | Frontend only (`device_detail.html`) | Event marker lifetime | No |
| `type_b_t2_s` | `avg_type_b.py` | Rolling window duration | No |
| `mqtt_broker` | `src/mqtt/client.py`, `mqtt_config.py` | Broker hostname | Yes |
| `db_cache_size_kb` | `mqtt_database.py` | SQLite PRAGMA | No (but requires connection restart) |
| `server_port` | `run.sh`, `wsgi.py` | Gunicorn bind port | Yes |
| `otp_expiry_s` | `src/app/routes/auth.py` | OTP validity window | No |

**Coverage:**
- ~80 out of 90+ parameters have WHERE_USED entries.
- Missing entries are typically simple constants or rarely-changed values.

---

### Redundancy Map

**Parameters appearing in multiple templates or contexts:**

| Parameter | app_config.html | system_config.html | ttl_config.html | Notes |
|-----------|-----------------|-------------------|-----------------|-------|
| `type_a_ttl_s` | Yes (category: Type A) | — | Yes (card: Type A) | Redundancy: TTL can be edited in two places |
| `type_b_ttl_s` | Yes (category: Type B) | — | Yes (card: Type B) | Same as above |
| `type_c_ttl_s` | Yes (category: Type C) | — | Yes (card: Type C) | Same as above |
| `type_d_ttl_s` | Yes (category: Type D) | — | Yes (card: Type D) | Same as above |
| `auto_restart_*` | Yes (implied category) | Detailed (toggle + time fields) | — | system_config.html is dedicated UI; app_config.html for power users |

**Winners (which UI should win in rewrite):**

1. **TTL parameters:** 
   - `ttl_config.html` provides better UX (visual cards per event type, behavior explanation).
   - `app_config.html` entry is redundant and should be removed (or vice versa).
   - **Recommendation:** Keep ttl_config.html, remove TTL entries from app_config.html (or make app_config.html read-only for these fields).

2. **Auto-restart:**
   - `system_config.html` is specialist; provides restart status displays.
   - `app_config.html` entry would just be raw input fields (less context).
   - **Recommendation:** Keep system_config.html, remove from app_config.html or hide in "advanced" section.

---

### Deletion Candidates

**For the HERMES rewrite (Phase 1+), these pages are consolidation targets:**

#### **1. ttl_config.html → Merge into unified event-config panel**

**Rationale:**
- TTL fields (Type A–D) are specialized but related to event detection.
- No admin overhead: changes take effect immediately (no restart required).
- Current split across ttl_config.html and app_config.html creates confusion.

**Recommended consolidation:**
- Move TTL cards into a "Event Detection" or "Event Behavior" tab/section in app_config.html.
- Add the behavior explanation (priority hierarchy, example timeline) as an expandable help section.
- Remove redundant TTL entries from app_config.html's Type A/B/C/D categories.

#### **2. system_config.html → Integrate into app_config.html with visual distinction**

**Rationale:**
- Auto-restart is a single feature with only 3 parameters (enabled, hour, minute).
- Current dedicated page feels over-engineered for this scope.
- Can be a collapsible "System Health" or "Server Management" section in app_config.html.

**Recommended consolidation:**
- Add "Server" or "System" category card to app_config.html.
- Include the status grid (Current Status, Last Restart, Next Restart) with auto-refresh (polling).
- Reduce from 431 lines (system_config.html) to ~100 lines (new category).
- Keep the same save behavior and error handling.

#### **3. app_config.html → Refactor into specialized panels by role**

**Rationale:**
- 90+ parameters across 18 categories is overwhelming for most users.
- Different user personas need different subsets: field engineers vs. sysadmins.

**Recommended structure (Phase 1):**
1. **General / Basic Configuration Panel:**
   - Acquisition, Detection, Type A–D (core event detection settings).
   - Defaults for all parameters.

2. **Performance Tuning Panel:**
   - Workers, Database, Server, Queue parameters.
   - Intended for sysadmins optimizing for hardware.

3. **Integration Panel:**
   - MQTT, API, Device, Logging, Auth.
   - Intended for deployment customization.

4. **UI / Frontend Panel:**
   - All `ui_*` parameters.
   - Can be embedded in a separate frontend config page or kept here with read-only note.

---

### Hardcoded Frontend Constants (to be externalised)

These values are currently baked into template HTML or JavaScript. In the rewrite, consider:

1. **TTL default values** (ttl_config.html):
   - `5.0s` (Type A), `10.0s` (Type B), `3.0s` (Type C), `8.0s` (Type D).
   - Should come from server `/api/event/config/*` endpoints (currently do on page load, but HTML defaults as fallback).

2. **Category metadata** (app_config.html, CAT_META):
   - Icon, order, display name.
   - Could be generated from backend schema instead of hardcoding 18 entries.

3. **Unit labels** (app_config.html, UNITS):
   - 80+ mapping of key → unit string.
   - Could be returned by backend in the config schema.

4. **WHERE_USED descriptions** (app_config.html):
   - 80+ entries mapping key → detailed HTML description.
   - Should be fetched from backend or a separate API endpoint.
   - Major maintenance burden: if code changes, docs go stale.

5. **Timezone** (system_config.html):
   - `'Asia/Kolkata'` hardcoded for last restart display.
   - Should be configurable (add `ui_timezone` or `system_timezone` config parameter).

6. **Message & label text:**
   - "all 117 tunable system constants" (app_config.html, system_config.html).
   - "all 90+ configuration parameters" (app_config.html).
   - Should be dynamic based on actual parameter count from backend.

---

### Browser/Platform Assumptions

1. **Font:** 'Space Grotesk' (Google Fonts) for app_config.html and system_config.html.
   - ttl_config.html inherits from `base.html` (assume Bootstrap fonts).

2. **Modern JavaScript:** ES6+ (arrow functions, template literals, `const`/`let`).
   - No IE11 support.

3. **CSS Grid/Flexbox:** Required for multi-column layouts.
   - Fallback for older browsers not implemented.

4. **localStorage:** Required for app_config.html collapse state.
   - Fails silently if disabled.

5. **Network:** Assumes persistent connection; no offline mode.

6. **Timezone API:** `toLocaleString()` with custom timezone (system_config.html only).

---

## Summary Table

| Aspect | app_config.html | system_config.html | ttl_config.html |
|--------|-----------------|-------------------|-----------------|
| **Lines** | ~1740 | ~431 | ~327 |
| **Purpose** | All 90+ system constants | Auto-restart schedule | Event TTL tuning |
| **UI Complexity** | High (categories, search, filters, modals) | Low (single feature) | Medium (4 event cards) |
| **Save Behavior** | Dirty tracking, per-category, save-all | Direct POST on button click | Fetch-on-save (race condition risk) |
| **Endpoints** | `/api/system/app-config` (load, save, reset) | `/api/system/auto-restart/config` | 4× separate event API endpoints |
| **Auto-refresh** | None (manual reload) | Every 60 seconds | None (manual load on page init) |
| **Data Sync** | localStorage collapse state | None | None |
| **Error Handling** | Toast notifications + modals | Message box (crude) | Status divs (no auto-dismiss) |
| **Restart Modal** | Yes (shows affected keys) | Implied (validation only) | N/A |
| **Reset Feature** | Yes (category or all) | No | No |
| **Search/Filter** | Yes (global search, pills, toggles) | N/A | N/A |
| **Validation** | Frontend input types only | Frontend range checks (0–23, 0–59) | Frontend range via HTML constraints |
| **Redundancy** | TTL fields + auto-restart (also in other pages) | Subset of app_config fields | TTL fields (also in app_config) |
| **Rewrite Priority** | **Keep as unified panel** | **Merge into app_config** | **Merge into app_config or event-config** |

---

**End of Reference Document**

