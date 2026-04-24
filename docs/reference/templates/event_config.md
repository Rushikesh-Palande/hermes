# Event Configuration Dashboard — Comprehensive Reference

**Source:** `/home/embed/hammer/templates/event_config.html` (lines 1–4663)

**Phase:** 0.5 — Behavior Contract Capture for HERMES Dashboard Rewrite

This document captures every UI behavior, input field, endpoint, validation rule, and event listener in the detection-config UI without reopening the original HTML.

---

## 1. Page Layout & Navigation

**Overall Structure:**
- Fixed left sidebar (70px wide, dark background #1a202c) with 4 main navigation items
- Main content area (max-width: none, 48px–72px margins depending on screen size)
- Page title: "EMBED SQUARE" + "Event Configuration" subtitle
- Alert banner area (top of main content) for success/error messages

**Sidebar Items (line 1167–1195):**
1. **DEVICE** → `/device-config`
2. **EVENTS** → `/event-config` (currently active, blue highlight #4299e1)
3. **OFFSET** → `/offset-config`
4. **EXIT** → `logout(event)` function call

**Main Content Sections (in order):**
1. Daily Auto-Restart Configuration (lines 1215–1278)
2. Sensor Mode Switching Configuration (lines 1280–1371) — **always enabled**
3. Algorithm Tabs Container (lines 1374–1580) with 4 tabs:
   - Algorithm A (Type A) — Variance-Based
   - Algorithm B (Type B) — Post-Window Deviation
   - Algorithm C (Type C) — Average Range Validation
   - Algorithm D (Type D) — Two-Stage Smoothed

---

## 2. Daily Auto-Restart Configuration

**Toggle Element:**
- ID: `autoRestartEnabled` (line 1229)
- Type: checkbox
- Event: `onchange="toggleAutoRestart()"`

**Config Section (Hidden by default, ID: `autoRestartConfig`):**
- Restarts only when toggle is ON
- **Restart Time Inputs:**
  - `restartHour` (id, line 1241): type="number", min="0", max="23", value="3"
  - `restartMinute` (id, line 1244): type="number", min="0", max="59", value="0"
  - Format: IST (Asia/Kolkata), 24-hour display
  
- **Status Display (read-only):**
  - `nextRestartDisplay` (id): shows next scheduled restart time or "N/A"
  - `lastRestartDisplay` (id): shows last restart in en-IN locale or "Never"

**Save Button:**
- onclick: `saveAutoRestartConfig()` (line 1268)
- Endpoint: POST `/api/system/auto-restart/config`
- Payload: `{ enabled, restart_hour, restart_minute }`
- Validation: hour [0–23], minute [0–59]
- Response: `{ success, error?, config?, status? }`

**Load Behavior:**
- Automatically called on DOMContentLoaded (line 4617) via `loadAutoRestartConfig()`
- Endpoint: GET `/api/system/auto-restart/config`
- Populates form fields and status display
- Shows/hides config section based on `enabled` flag

---

## 3. Sensor Mode Switching Configuration

**Scope:** Applies to **ALL sensors simultaneously**, **ALWAYS ENABLED** (badge at line 1292: "ALWAYS ACTIVE")

**Three-Mode System:** POWER_ON → STARTUP → BREAK
- POWER_ON (Gray): No events triggered, initial server state
- STARTUP (Green): Active detection mode, events triggered
- BREAK (Orange): Standby, events NOT triggered but monitored

**Input Fields:**

| Field ID | Label | Type | Min | Max | Step | Default | Purpose |
|----------|-------|------|-----|-----|------|---------|---------|
| `modeStartupThreshold` | Startup Threshold (Raw Value) | number | 0 | 10000 | 0.1 | 100.0 | Sensor value must exceed this to transition: POWER_ON → STARTUP or BREAK → STARTUP |
| `modeBreakThreshold` | Break Threshold (Raw Value) | number | 0 | 10000 | 0.1 | 50.0 | Sensor value must drop below this to transition: STARTUP → BREAK |
| `modeStartupDuration` | Startup Duration | number | 1 (ms) or 0.001 (s) | 60000 (ms) or 60 (s) | 1 or 0.001 | 100 | Time value must remain above startup threshold before entering STARTUP |
| `modeStartupDurationUnit` | Unit selector | select (ms/s) | — | — | — | ms | Switches display between milliseconds and seconds |
| `modeBreakDuration` | Break Duration | number | 1 (ms) or 0.001 (s) | 60000 (ms) or 60 (s) | 1 or 0.001 | 2000 | Time value must remain below break threshold before entering BREAK |
| `modeBreakDurationUnit` | Unit selector | select (ms/s) | — | — | — | ms | Switches display between milliseconds and seconds |

**Duration Unit Conversion (lines 4490–4526):**
- Displayed unit controlled by `<select>` elements
- Internally always stored/sent to backend in **seconds**
- Conversion happens via `convertDurationUnit()` (line 4490) when user changes dropdown:
  - ms → s: divide by 1000, allow 3 decimals, min=0.001, max=60
  - s → ms: multiply by 1000, round to integer, min=1, max=60000
- Helper: `getDurationSeconds(inputId, selectId)` returns seconds regardless of unit
- Helper: `setDurationFromSeconds()` converts seconds back to display unit

**Save Button:**
- onclick: `saveModeSwitchingConfig()` (line 1356)
- Endpoint: POST `/api/mode_switching/config`
- Payload:
  ```json
  {
    "enabled": true,
    "startup_threshold": number,
    "break_threshold": number,
    "startup_duration_seconds": number,
    "break_duration_seconds": number
  }
  ```
- Validation (lines 4560–4584):
  - startupThreshold: positive number
  - breakThreshold: positive number, must be < startupThreshold
  - startupDuration: 1 ms to 60 s
  - breakDuration: 1 ms to 60 s
- Response: `{ success, error? }`
- Side effect: "Mode switching configuration saved! All active detectors updated."

**Load Behavior:**
- Called on DOMContentLoaded (line 4618) via `loadModeSwitchingConfig()`
- Endpoint: GET `/api/mode_switching/config`
- Populates all 6 input fields
- Sets duration unit display from stored seconds

**Live Apply Semantics:** Changes take effect **immediately on all active detectors** (line 1368 info box)

---

## 4. Algorithm Tabs & Panels

**Tab Container (lines 1374–1392):**
- 4 buttons in `.tabs-container`
- onclick: `switchTab('typeX')` function (line 1586)
- Active tab highlighted with blue underline (#4299e1, ::after pseudo-element)
- Each tab button has status badge (badge-active / badge-inactive)

**Tab Switching Logic (lines 1586–1605):**
- Hides all `.tab-content` divs
- Removes `.active` class from all buttons
- Shows selected tab by ID
- Adds `.active` class to clicked button

---

## 5. Type A: Variance-Based Detection (CV%)

**Tab ID:** `typeA` (line 1395)

**Event Type Code:** Not explicitly numbered, but implied Type A

**Description (line 1397–1399):**
> Monitors sample variance over configurable timeframes. Triggers events when the variance percent (standard deviation ÷ mean × 100) exceeds the configured threshold.

**Enable/Disable Toggle (lines 1402–1408):**
- ID: `typeAEnabled`
- Checkbox with label "Enable Detection"
- onchange: `toggleTypeA()` (line 1405)
- Confirmation modal required before state change

**Behavior on Toggle (lines 1621–1644):**
1. toggleTypeA() fires, captures checked state
2. showEnableConfirmModal() shows "Are you sure you want to [enable/disable] Type A Detection?"
3. If confirmed: calls saveTypeAEnableState(enabled), then updateTypeAUI(enabled)
4. If cancelled: reverts checkbox
5. UI updates (opacity, pointer-events) and badge changes

**Save Endpoint (line 1687):**
- POST `/api/event/config/type_a`
- Payload structure (lines 1677–1682):
  ```json
  {
    "T1": timeframe_seconds,
    "tolerance_pct": tolerance_pct,
    "ttl_seconds": ttl_seconds,
    "debounce_seconds": debounce_value,
    "enabled": enabled
  }
  ```
- Preserves existing config values when only toggling enabled state
- Response: `{ success, error? }`

**Per-Sensor Configuration Grid (lines 1410–1440):**

**Grid Building (buildSensorGrid, lines 2216–2393):**
- Fetches devices from `/api/devices`
- For each device, creates a transposed table:
  - Rows = configuration type (T1, Variance, TTL, Debounce)
  - Columns = sensors 1–12
  - One table per device, displayed vertically

**Input Fields per Sensor:**

| Data-type | Label | Class | Min | Max | Step | Default | Range |
|-----------|-------|-------|-----|-----|------|---------|-------|
| `t1` | T1 Window (Sec) | `.sensor-config` | 1 | 60 | 1 | 3 | Per-sensor, overridable |
| `tolerance` | Variance (%) | `.sensor-config` | 0 | unbounded | 0.01 | 10 | Per-sensor, overridable |
| `ttl` | TTL (Sec) | `.sensor-config` | 0.1 | 60 | 0.1 | 5 | Per-sensor, overridable |
| `debounce` | Debounce (Sec) | `.type-a-debounce` | 0 | 300 | 0.1 | 0 | **Global** (one per device, applies to all sensors) |

**Dataset Attributes:**
- Each input has `data-device-id`, `data-sensor-id`, `data-key`, `data-type`
- Enables selector queries like `.sensor-config[data-device-id="1"][data-sensor-id="5"]`

**Grid Focus & Cell Selection (lines 2357–2392):**
- Global variable: `lastFocusedCell` (line 2210)
- On focus: saves device ID, sensor ID, type, current value
- On input (typing): updates `lastFocusedCell.value` in real-time
- On change: highlights cell with blue border (#667eea), light blue bg (#f0f4ff)
- On blur: removes box-shadow highlight

**"Apply to All 12 Sensors" Button (line 2346):**
- onclick: `applyToAll12Sensors(deviceId)`
- Requires: lastFocusedCell to be set (same device)
- Copies focused cell's value to all 12 sensors in that device and field type
- Highlights applied cells same color
- Shows success alert with count

**Save All Changes Button (line 1432):**
- onclick: `saveAllIndividualConfigs()`
- Collects all `.sensor-config` inputs into nested object:
  ```javascript
  configs = {
    deviceId: {
      sensorId: { T1, tolerance_pct, ttl_seconds },
      ...
    },
    ...
  }
  ```
- Shows confirmation modal with device/sensor count
- If confirmed: calls performSaveAllIndividualConfigs()
- Endpoint: POST `/api/event/config/type_a/per_sensor/bulk`
- Payload: configs object (line 1846)
- Side effects:
  1. Calls saveTypeADebounceToGlobal() (line 3811) to save debounce to global config
  2. Calls applyTypeAGlobalConfig() with selected cell config (line 3873) if a cell was focused
  3. Calls broadcastEventConfigUpdate() to reload all tabs (line 3998)
  4. Resets cell highlighting

**Reset Button (line 1435):**
- onclick: `resetIndividualConfigs()` (line 3942)
- Resets all T1, tolerance, debounce to:
  - T1: '20'
  - Tolerance: '10'
  - Debounce: '0'
- Does NOT save to backend, just UI reset

**Load Per-Sensor Configs (lines 2143–2207):**
- Called in window.onload (line 1613) AFTER grid is built
- Endpoint: GET `/api/event/config/type_a/per_sensor`
- Response structure:
  ```json
  {
    "success": true,
    "configs_by_device": {
      "1": {
        "1": { "timeframe_seconds": ..., "tolerance_pct": ..., "ttl_seconds": ... },
        ...
      },
      ...
    }
  }
  ```
- Populates grid inputs with saved values

**Load Global Type A Config (lines 3961–3983):**
- Called in window.onload (line 1612)
- Endpoint: GET `/api/event/config/type_a`
- Updates only the `typeAEnabled` checkbox
- Loads debounce_seconds to all `.type-a-debounce` inputs

**Debounce Field Behavior (CRITICAL - line 1611 comment):**
- Comment at line 1611: "Load AFTER grid build (grid rebuilds reset all inputs to defaults)"
- Debounce is NOT per-sensor; it applies to the entire Type A event type globally
- Stored in one input per device but synced globally
- Reloaded AFTER per-sensor configs are loaded, to override any defaults

**TTL Behavior:**
- Loaded from `/api/event/config/type_a/per_sensor` response
- If no per-sensor value exists, defaults to 5
- Per-sensor input included in grid but loaded separately

---

## 6. Type B: Post-Window Deviation Detection

**Tab ID:** `typeB` (line 1444)

**Event Type Code:** `-2` (line 1447)

**Description:**
> Detects sudden deviations immediately after a stable time window

**Tolerance Formula (line 1450 description text — NOTE: this text is misleading):**
> UI description says: "Bounds = avg × (1 ± tolerance%/100)"

**Actual implemented formula (tooltip at line 2473 + backend `src/events/constants.py` REF_VALUE=100):**
> `band = avg_T2 ± (REF_VALUE × tolerance% / 100)` where `REF_VALUE = 100`
> Which simplifies to `band = avg_T2 ± tolerance%` in raw units.

**Consequence:** a sensor resting at 327 with tolerance=5 produces band [322, 332], NOT [310.65, 343.35]. The UI copy at line 1450 is wrong and was the source of user confusion. The rewrite should either (a) use the REF_VALUE=100 formula with clearer UI copy, or (b) change to true "% of running average" — but NOT silently do one while the UI claims the other. This is [`BUG_DECISION_LOG.md`](../../contracts/BUG_DECISION_LOG.md) territory and already flagged.

**Enable/Disable Toggle (lines 1453–1459):**
- ID: `typeBEnabled`
- Checkbox with label "Enable Detection"
- onchange: `toggleTypeB()` (line 1456)
- Confirmation modal required

**Save Endpoint (toggleTypeB → saveTypeBEnableState):**
- If enabled: POST `/api/avg-type-b/start` (line 1759)
- If disabled: POST `/api/avg-type-b/stop` (line 1772)
- Response: `{ success, error? }`

**Per-Sensor Configuration Grid (lines 1461–1491):**

**Grid Building (buildAvgTypeBGrid, lines 2395–2565):**
- Similar to Type A: transposed table by device
- Each row = configuration type
- Each column = sensor 1–12

**Input Fields per Sensor:**

| Data-type | Label | Class | Min | Max | Step | Default | Purpose |
|-----------|-------|-------|-----|-----|------|---------|---------|
| `t2` | T2 Window (Sec) | `.avg-b-config` | 0.1 | 60 | 0.1 | 2 | Post-window stability period |
| `tolerance` | Tolerance (%) | `.avg-b-config` | 0 | unbounded | 0.1 | 5.0 | Band = avg_T2 ± (100 × tol%) |
| `ttl` | TTL (Sec) | `.avg-b-config` | 0.1 | 300 | 0.1 | 10 | Time-to-live before event save |
| `debounce` | Debounce (Sec) | `.avg-b-debounce` | 0 | 300 | 0.1 | 0 | **Global** (one per device) |

**Cell Selection & Apply (lines 2534–2562):**
- Global variable: `lastFocusedAvgTypeBCell`
- Same mechanics as Type A (focus, input, change, blur)

**Apply to All 12 Sensors (line 2524):**
- onclick: `applyAvgTypeBToAllSensors(deviceId)`
- Same pattern as Type A

**Save All Changes (line 1481):**
- onclick: `saveAvgTypeBConfigs()` (line 3083)
- Collects configs via `collectAvgTypeBConfigs()` (line 2945)
- Validation (lines 3090–3097):
  - T2 > 0
  - lower_tolerance_pct >= 0
- Confirmation modal with device/sensor count
- Endpoint: POST `/api/avg-type-b/config/bulk` (line 3119)
- Payload:
  ```json
  {
    "configs": configs_object,
    "selected_config": per_sensor_config_or_null
  }
  ```
- Side effect: Also POSTs to `/api/avg-type-b/config` with TTL and debounce (lines 3135–3151)
- Response: `{ success, error? }`
- Broadcasts update (line 3156)

**Reset Button (line 1483):**
- onclick: `resetAvgTypeBConfigs()` (line 3168)
- Resets all to defaults:
  - T2: '2.0'
  - Tolerance: '5.0'
  - TTL: '10.0'
  - Debounce: '0'
- UI-only reset, no backend call

**Load Per-Sensor Configs (lines 3010–3081):**
- Called in buildAvgTypeBGrid (line 2564)
- Endpoint: GET `/api/avg-type-b/config/per_sensor`
- Falls back to GET `/api/avg-type-b/config` if no per-sensor configs
- Applies values via `applyAvgTypeBConfigs()` (line 2983)
- Also fetches enabled state and debounce from global config

**TTL Syncing (lines 4625–4628):**
- DOMContentLoaded listener (line 4622)
- When user edits any `.avg-b-config[data-type="ttl"]` input:
  - All other TTL inputs in Type B are synced to same value
  - Ensures TTL is global across all sensors

---

## 7. Type C: Average Range Validation

**Tab ID:** `typeC` (line 1496)

**Event Type Code:** `-3` (line 1500)

**Description:**
> Detects sustained offset during the window using absolute thresholds

**Enable/Disable Toggle (lines 1504–1510):**
- ID: `typeCEnabled`
- onchange: `toggleTypeC()` (line 1507)
- Confirmation modal required

**Save Endpoint (toggleTypeC → saveTypeCEnableState):**
- If enabled: POST `/api/avg-type-c/start` (line 1834)
- If disabled: POST `/api/avg-type-c/stop` (line 1845)

**Per-Sensor Configuration Grid (lines 1512–1534):**

**Grid Building (buildAvgTypeCGrid, lines 2567–2753):**

**Input Fields per Sensor:**

| Data-type | Label | Class | Min | Max | Step | Default | Purpose |
|-----------|-------|-------|-----|-----|------|---------|---------|
| `t3` | T3 Window (Sec) | `.avg-c-config` | 0.1 | 60 | 0.1 | 4.0 | Averaging window duration |
| `lower` | Lower Tolerance | `.avg-c-config` | unbounded | unbounded | 0.1 | 51.9 | Lower threshold (absolute, not %) |
| `upper` | Upper Tolerance | `.avg-c-config` | unbounded | unbounded | 0.1 | 52.0 | Upper threshold (absolute, not %) |
| `ttl` | TTL (Sec) | `.avg-c-config` | 0.1 | 300 | 0.1 | 3 | Time-to-live before event save |
| `debounce` | Debounce (Sec) | `.avg-c-debounce` | 0 | 300 | 0.1 | 0 | **Global** |

**Cell Selection & Apply (lines 2722–2750):**
- Global variable: `lastFocusedAvgTypeCCell`
- onclick: `applyAvgTypeCToAllSensors(deviceId)` (line 2712)

**Save All Changes (line 1527):**
- onclick: `saveAvgTypeCConfigs()` (line 3319)
- Collects via `collectAvgTypeCConfigs()` (line 3209)
- Validation (lines 3326–3332):
  - lower_threshold <= upper_threshold
  - T3 > 0
- Endpoint: POST `/api/avg-type-c/config/bulk` (line 3355)
- Also POSTs global config with TTL/debounce (lines 3371–3386)

**Reset Button (line 1528):**
- onclick: `resetAvgTypeCConfigs()` (line 3403)
- Defaults:
  - T3: '4.0'
  - Lower: '51.9'
  - Upper: '52.0'
  - TTL: '3.0'
  - Debounce: '0'

**Load Per-Sensor Configs (lines 3280–3317):**
- Endpoint: GET `/api/avg-type-c/config/per_sensor`
- Falls back to global config if per-sensor not found
- Applies via `applyAvgTypeCConfigs()` (line 3248)

**Threshold Visualization (lines 4325–4357):**
- Function: `updateAvgTypeCThresholdVisualization()`
- Displays lower/upper thresholds on screen
- Updates on config change
- IDs: `c-lower-label`, `c-upper-label`

**TTL Syncing (lines 4629–4632):**
- Same DOMContentLoaded listener pattern as Type B
- Syncs all `.avg-c-config[data-type="ttl"]` cells

---

## 8. Type D: Two-Stage Smoothed Validation

**Tab ID:** `typeD` (line 1539)

**Event Type Code:** `-4` (line 1543)

**Description:**
> Detects sustained offset using absolute thresholds

**Enable/Disable Toggle (lines 1546–1552):**
- ID: `typeDEnabled`
- onchange: `toggleTypeD()` (line 1549)

**Save Endpoint (toggleTypeD → saveTypeDEnableState):**
- If enabled: POST `/api/avg-type-d/start` (line 1906)
- If disabled: POST `/api/avg-type-d/stop` (line 1917)

**Per-Sensor Configuration Grid (lines 1554–1576):**

**Grid Building (buildAvgTypeDGrid, lines 2755–2943):**

**Input Fields per Sensor:**

| Data-type | Label | Class | Min | Max | Step | Default | Purpose |
|-----------|-------|-------|-----|-----|------|---------|---------|
| `t4` | T4 Window (Sec) | `.avg-d-config` | 1 | 120 | 1 | 5.0 | First-stage window (shorter-term) |
| `t5` | T5 Window (Sec) | `.avg-d-config` | 1 | 1800 | 1 | 30 | Second-stage baseline window (longer-term) |
| `tolerance` | Tolerance (%) | `.avg-d-config` | 0 | unbounded | 0.1 | 5.0 | Band = avg_T5 ± (100 × tol%) |
| `ttl` | TTL (Sec) | `.avg-d-config` | 0.1 | 300 | 0.1 | 8 | Time-to-live before event save |
| `debounce` | Debounce (Sec) | `.avg-d-debounce` | 0 | 300 | 0.1 | 0 | **Global** |

**Cell Selection & Apply (lines 2912–2940):**
- Global variable: `lastFocusedAvgTypeDCell`
- onclick: `applyAvgTypeDToAllSensors(deviceId)` (line 2902)

**Save All Changes (line 1569):**
- onclick: `saveAvgTypeDConfigs()` (line 3567)
- Collects via `collectAvgTypeDConfigs()` (line 3446)
- Validation (lines 3574–3583):
  - lower_threshold >= 0
  - T4 > 0
  - T5 > 0
- Endpoint: POST `/api/avg-type-d/config/bulk` (line 3606)
- Also POSTs global config (lines 3622–3641)

**Reset Button (line 1570):**
- onclick: `resetAvgTypeDConfigs()` (line 3658)
- Defaults:
  - T4: '5.0'
  - T5: '30.0'
  - Tolerance: '5.0'
  - TTL: '8.0'
  - Debounce: '0'

**Load Per-Sensor Configs (lines 3520–3565):**
- Endpoint: GET `/api/avg-type-d/config/per_sensor`
- Applies via `applyAvgTypeDConfigs()` (line 3487)
- Also loads enabled state, T5, tolerance, and debounce from global config

**TTL Syncing (lines 4633–4637):**
- Same DOMContentLoaded listener pattern
- Syncs all `.avg-d-config[data-type="ttl"]` cells

---

## 9. Endpoint Inventory

**Complete API Surface:**

| Method | URL | Request Body | Response | Side Effect |
|--------|-----|--------------|----------|------------|
| GET | `/api/devices` | — | `{ success, devices: [{device_id, ...}, ...] }` | Populates device list for grid building |
| GET | `/api/event/config/type_a` | — | `{ success, config: {T1, tolerance_pct, ttl_seconds, debounce_seconds, enabled} }` | Loads global Type A config |
| POST | `/api/event/config/type_a` | `{ T1, tolerance_pct, ttl_seconds, debounce_seconds, enabled }` | `{ success, error? }` | Updates global Type A config |
| GET | `/api/event/config/type_a/per_sensor` | — | `{ success, configs_by_device: {deviceId: {sensorId: {timeframe_seconds, tolerance_pct, ttl_seconds}, ...}, ...} }` | Populates Type A grid |
| POST | `/api/event/config/type_a/per_sensor/bulk` | `{ deviceId: {sensorId: {T1, tolerance_pct, ttl_seconds}, ...}, ... }` | `{ success, error? }` | Saves all Type A per-sensor configs |
| POST | `/api/avg-type-b/start` | — | `{ success, error? }` | Enables Type B detection |
| POST | `/api/avg-type-b/stop` | — | `{ success, error? }` | Disables Type B detection |
| GET | `/api/avg-type-b/config` | — | `{ success, config: {T2, lower_tolerance_pct, upper_tolerance_pct, ttl_seconds, debounce_seconds, enabled} }` | Loads global Type B config |
| POST | `/api/avg-type-b/config` | `{ T2, lower_tolerance_pct, upper_tolerance_pct, enabled, ttl_seconds, debounce_seconds }` | `{ success, error? }` | Updates global Type B config |
| GET | `/api/avg-type-b/config/per_sensor` | — | `{ success, configs_by_device: {deviceId: {sensorId: {T2, lower_tolerance_pct, upper_tolerance_pct, ttl_seconds}, ...}, ...} }` | Populates Type B grid |
| POST | `/api/avg-type-b/config/bulk` | `{ configs: {...}, selected_config: {...} }` | `{ success, error? }` | Saves all Type B per-sensor configs |
| GET | `/api/avg-type-b/selection` | — | `{ success, selection: {device_id, sensor_id} }` | Loads currently active Type B sensor (unused in current UI) |
| POST | `/api/avg-type-c/start` | — | `{ success, error? }` | Enables Type C detection |
| POST | `/api/avg-type-c/stop` | — | `{ success, error? }` | Disables Type C detection |
| GET | `/api/avg-type-c/config` | — | `{ success, config: {T3, lower_threshold, upper_threshold, ttl_seconds, debounce_seconds, enabled} }` | Loads global Type C config |
| POST | `/api/avg-type-c/config` | `{ T3, lower_threshold, upper_threshold, enabled, ttl_seconds, debounce_seconds }` | `{ success, error? }` | Updates global Type C config |
| GET | `/api/avg-type-c/config/per_sensor` | — | `{ success, configs_by_device: {deviceId: {sensorId: {T3, lower_threshold, upper_threshold, ttl_seconds}, ...}, ...} }` | Populates Type C grid |
| POST | `/api/avg-type-c/config/bulk` | `{ configs: {...}, selected_config: {...} }` | `{ success, error? }` | Saves all Type C per-sensor configs |
| POST | `/api/avg-type-d/start` | — | `{ success, error? }` | Enables Type D detection |
| POST | `/api/avg-type-d/stop` | — | `{ success, error? }` | Disables Type D detection |
| GET | `/api/avg-type-d/config` | — | `{ success, config: {T4, T5, tolerance, lower_threshold, upper_threshold, ttl_seconds, debounce_seconds, enabled} }` | Loads global Type D config |
| POST | `/api/avg-type-d/config` | `{ T4, T5, tolerance, enabled, ttl_seconds, debounce_seconds }` | `{ success, error? }` | Updates global Type D config |
| GET | `/api/avg-type-d/config/per_sensor` | — | `{ success, configs_by_device: {deviceId: {sensorId: {T4, T5, lower_threshold, upper_threshold, ttl_seconds}, ...}, ...} }` | Populates Type D grid |
| POST | `/api/avg-type-d/config/bulk` | `{ configs: {...}, selected_config: {...} }` | `{ success, error? }` | Saves all Type D per-sensor configs |
| GET | `/api/system/auto-restart/config` | — | `{ success, config: {enabled, restart_hour, restart_minute, last_restart}, status: {next_restart} }` | Populates auto-restart form |
| POST | `/api/system/auto-restart/config` | `{ enabled, restart_hour, restart_minute }` | `{ success, error? }` | Saves auto-restart schedule |
| GET | `/api/mode_switching/config` | — | `{ success, config: {startup_threshold, break_threshold, startup_duration_seconds, break_duration_seconds} }` | Populates mode-switching form |
| POST | `/api/mode_switching/config` | `{ enabled, startup_threshold, break_threshold, startup_duration_seconds, break_duration_seconds }` | `{ success, error? }` | Saves mode-switching config, applies to all detectors |
| POST | `/api/auth/logout` | — | — | Redirects to /login |

---

## 10. JavaScript Function Index

**Core UI Functions:**

| Function | Line | Inputs | Outputs | Side Effects |
|----------|------|--------|---------|--------------|
| `switchTab(tabName)` | 1586 | tabName: string | — | Hides all tab-content, shows selected tab, updates active button |
| `toggleTypeA()` | 1621 | — | — | Shows confirmation modal, calls saveTypeAEnableState() or reverts checkbox |
| `updateTypeAUI(enabled)` | 1646 | enabled: bool | — | Updates opacity, pointer-events, badge text/color |
| `saveTypeAEnableState(enabled)` | 1664 | enabled: bool | — | POSTs to /api/event/config/type_a, shows alert, broadcasts update |
| `toggleTypeB()` | 1710 | — | — | Shows confirmation modal, calls saveTypeBEnableState() |
| `updateTypeBUI(enabled)` | 1735 | enabled: bool | — | Updates badge |
| `saveTypeBEnableState(enabled)` | 1749 | enabled: bool | — | POSTs /api/avg-type-b/start or /stop, shows alert |
| `toggleTypeC()` | 1791 | — | — | Shows confirmation modal, calls saveTypeCEnableState() |
| `updateTypeCUI(enabled)` | 1812 | enabled: bool | — | Updates card opacity and badge |
| `saveTypeCEnableState(enabled)` | 1831 | enabled: bool | — | POSTs /api/avg-type-c/start or /stop |
| `toggleTypeD()` | 1863 | — | — | Shows confirmation modal, calls saveTypeDEnableState() |
| `updateTypeDUI(enabled)` | 1884 | enabled: bool | — | Updates card opacity and badge |
| `saveTypeDEnableState(enabled)` | 1903 | enabled: bool | — | POSTs /api/avg-type-d/start or /stop |
| `showEnableConfirmModal(message, callback)` | 1934 | message: string, callback: function | — | Appends modal DOM, sets callback |
| `applyToAll12Sensors(deviceId)` | 1964 | deviceId: int | — | Validates lastFocusedCell, copies value to 12 sensors, shows alert |
| `applyAvgTypeBToAllSensors(deviceId)` | 2002 | deviceId: int | — | Same pattern for Type B |
| `applyAvgTypeCToAllSensors(deviceId)` | 2034 | deviceId: int | — | Same pattern for Type C |
| `applyAvgTypeDToAllSensors(deviceId)` | 2066 | deviceId: int | — | Same pattern for Type D |
| `loadDevicesAndBuildGrid()` | 2099 | — | — | GETs /api/devices, calls buildSensorGrid/buildAvgTypeB/C/DGrid |
| `buildSensorGrid(devices)` | 2216 | devices: array | — | Builds Type A grid HTML, attaches focus/input/change/blur listeners |
| `buildAvgTypeBGrid(devices)` | 2395 | devices: array | — | Builds Type B grid, calls loadAvgTypeBConfigs() |
| `buildAvgTypeCGrid(devices)` | 2567 | devices: array | — | Builds Type C grid, calls loadAvgTypeCConfigs() |
| `buildAvgTypeDGrid(devices)` | 2755 | devices: array | — | Builds Type D grid, calls loadAvgTypeDConfigs() |
| `loadSavedPerSensorConfigs()` | 2143 | — | — | GETs /api/event/config/type_a/per_sensor, populates grid inputs |
| `collectAvgTypeBConfigs()` | 2945 | — | object, int, int | Returns {configs, deviceCount, sensorCount} |
| `saveAvgTypeBConfigs()` | 3083 | — | — | Validates, shows confirmation, calls performSaveAvgTypeBConfigs() |
| `performSaveAvgTypeBConfigs(configs, deviceCount, sensorCount)` | 3113 | configs, counts | — | POSTs /api/avg-type-b/config/bulk + global config, broadcasts update |
| `resetAvgTypeBConfigs()` | 3168 | — | — | Resets all Type B inputs to defaults, shows alert |
| `collectAvgTypeCConfigs()` | 3209 | — | object, int, int | Returns {configs, deviceCount, sensorCount} |
| `saveAvgTypeCConfigs()` | 3319 | — | — | Validates lower <= upper, shows confirmation, calls performSaveAvgTypeCConfigs() |
| `performSaveAvgTypeCConfigs(configs, deviceCount, sensorCount)` | 3349 | configs, counts | — | POSTs /api/avg-type-c/config/bulk + global config, broadcasts update |
| `resetAvgTypeCConfigs()` | 3403 | — | — | Resets all Type C inputs, shows alert |
| `collectAvgTypeDConfigs()` | 3446 | — | object, int, int | Returns {configs, deviceCount, sensorCount} |
| `saveAvgTypeDConfigs()` | 3567 | — | — | Validates T4/T5 > 0, shows confirmation, calls performSaveAvgTypeDConfigs() |
| `performSaveAvgTypeDConfigs(configs, deviceCount, sensorCount)` | 3600 | configs, counts | — | POSTs /api/avg-type-d/config/bulk + global config, broadcasts update |
| `resetAvgTypeDConfigs()` | 3658 | — | — | Resets all Type D inputs, shows alert |
| `saveAllIndividualConfigs()` | 3712 | — | — | Collects Type A configs, validates, shows confirmation |
| `performSaveAllIndividualConfigs(configs, deviceCount, sensorCount)` | 3836 | configs, counts | — | POSTs /api/event/config/type_a/per_sensor/bulk, saves debounce, broadcasts |
| `loadTypeAConfig()` | 3961 | — | — | GETs /api/event/config/type_a, updates enabled checkbox and debounce |
| `broadcastEventConfigUpdate()` | 3998 | — | — | Reloads all Type A/B/C/D configs from backend |
| `showAlert(type, message)` | 4079 | type: 'success'/'error', message: string | — | Shows alert, hides after 5 seconds |
| `logout(e)` | 4091 | e: event | — | POSTs /api/auth/logout, redirects to /login |
| `convertDurationUnit(inputId, selectId)` | 4490 | input/select IDs | — | Converts ms ↔ s, updates input constraints |
| `getDurationSeconds(inputId, selectId)` | 4511 | input/select IDs | seconds: number | Returns duration in seconds |
| `setDurationFromSeconds(inputId, selectId, seconds)` | 4518 | input/select IDs, seconds | — | Sets input value in currently selected unit |
| `loadModeSwitchingConfig()` | 4528 | — | — | GETs /api/mode_switching/config, populates form |
| `saveModeSwitchingConfig()` | 4554 | — | — | Validates thresholds/durations, POSTs /api/mode_switching/config |
| `toggleAutoRestart()` | 4398 | — | — | Shows/hides autoRestartConfig section, calls loadAutoRestartConfig() |
| `loadAutoRestartConfig()` | 4410 | — | — | GETs /api/system/auto-restart/config, populates form |
| `saveAutoRestartConfig()` | 4445 | — | — | Validates hour/minute, POSTs /api/system/auto-restart/config |
| `updateAvgTypeCThresholdVisualization()` | 4325 | — | — | Updates c-lower-label and c-upper-label with threshold values |

---

## 11. Event Listeners

**Window & Document Events:**

| Element | Event | Handler | Line | Effect |
|---------|-------|---------|------|--------|
| window | onload | `loadDevicesAndBuildGrid()`, `loadTypeAConfig()`, `loadSavedPerSensorConfigs()`, `loadAvgTypeBSelection()`, `updateAvgTypeBStatistics()`, `updateAvgTypeCThresholdVisualization()` | 1608 | Page initialization sequence |
| document | DOMContentLoaded | Load auto-restart, mode-switching; attach TTL sync listener | 4616 | Post-DOM setup |
| document (global) | input | TTL sync for Type B/C/D grids | 4622 | When user edits TTL field, sync all TTL cells |

**Input Focus/Change Events (attached dynamically):**

| Element Class | Event | Handler | Effect |
|---------------|-------|---------|--------|
| `.sensor-config` | focus | Updates lastFocusedCell | Records focused cell for bulk apply |
| `.sensor-config` | input | Updates lastFocusedCell.value | Tracks real-time changes |
| `.sensor-config` | change | Adds border/bg highlight | Visual indication of edit |
| `.sensor-config` | blur | Removes box-shadow | Clears focus state |
| `.avg-b-config` | focus | Updates lastFocusedAvgTypeBCell | Same pattern |
| `.avg-b-config` | input | Updates lastFocusedAvgTypeBCell.value | — |
| `.avg-b-config` | change | Adds highlight | — |
| `.avg-b-config` | blur | Removes shadow | — |
| `.avg-c-config` | focus | Updates lastFocusedAvgTypeCCell | — |
| `.avg-c-config` | input | Updates lastFocusedAvgTypeCCell.value | — |
| `.avg-c-config` | change | Adds highlight | — |
| `.avg-c-config` | blur | Removes shadow | — |
| `.avg-d-config` | focus | Updates lastFocusedAvgTypeDCell | — |
| `.avg-d-config` | input | Updates lastFocusedAvgTypeDCell.value | — |
| `.avg-d-config` | change | Adds highlight | — |
| `.avg-d-config` | blur | Removes shadow | — |

**Inline onclick/onchange Handlers:**

| Element | Handler | Line |
|---------|---------|------|
| `.tab-button` | onclick="switchTab('typeX')" | 1376–1391 |
| `typeAEnabled` checkbox | onchange="toggleTypeA()" | 1405 |
| `typeBEnabled` checkbox | onchange="toggleTypeB()" | 1456 |
| `typeCEnabled` checkbox | onchange="toggleTypeC()" | 1507 |
| `typeDEnabled` checkbox | onchange="toggleTypeD()" | 1549 |
| autoRestartEnabled checkbox | onchange="toggleAutoRestart()" | 1229 |
| modeStartupDurationUnit select | onchange="convertDurationUnit(...)" | 1325 |
| modeBreakDurationUnit select | onchange="convertDurationUnit(...)" | 1342 |
| Various buttons | onclick="function()" | Multiple (see function index) |

---

## 12. Save Flow & Validation

**Type A Save Flow:**

1. User clicks "Save All Changes"
2. `saveAllIndividualConfigs()` collects all `.sensor-config` inputs
3. Builds config object nested by device → sensor
4. Validates: (implicitly, no explicit rules in JS)
5. Shows confirmation modal with device/sensor count
6. If confirmed:
   - Captures debounce value synchronously (line 3838)
   - POSTs `/api/event/config/type_a/per_sensor/bulk` with configs
   - Waits for response
   - If success:
     - Calls `saveTypeADebounceToGlobal(capturedDebounce)` → POSTs /api/event/config/type_a with debounce + existing T1/tolerance
     - Calls `applyTypeAGlobalConfig()` with selected cell config → POSTs /api/event/config/type_a if cell was focused
     - Calls `broadcastEventConfigUpdate()` to reload all tabs
     - Resets cell highlighting
   - If error: shows error alert
7. Success alert: "Successfully saved configurations for X device(s) and Y sensor(s)"

**Type B Save Flow:**

1. User clicks "Save All Changes"
2. `saveAvgTypeBConfigs()` collects all `.avg-b-config` inputs
3. Builds config object
4. **Validates:**
   - T2 > 0 (line 3090)
   - lower_tolerance_pct >= 0 (line 3093)
5. Shows confirmation modal
6. If confirmed: `performSaveAvgTypeBConfigs()`:
   - Captures TTL and debounce from inputs
   - POSTs `/api/avg-type-b/config/bulk` with configs + selected_config
   - Waits for response
   - If success:
     - Fetches current global config
     - POSTs `/api/avg-type-b/config` with TTL, debounce, other fields
     - Broadcasts update
   - Resets highlighting
7. Success alert: "Saved Avg Type B configs for X device(s) and Y sensor(s)"

**Type C Save Flow:**

1. User clicks "Save All Changes"
2. `saveAvgTypeCConfigs()` collects all `.avg-c-config` inputs
3. **Validates:**
   - lower_threshold <= upper_threshold (line 3326)
   - T3 > 0 (line 3329)
4. Shows confirmation modal
5. If confirmed: `performSaveAvgTypeCConfigs()`:
   - POSTs `/api/avg-type-c/config/bulk`
   - POSTs `/api/avg-type-c/config` with TTL, debounce
   - Broadcasts update
6. Success alert

**Type D Save Flow:**

1. User clicks "Save All Changes"
2. `saveAvgTypeDConfigs()` collects all `.avg-d-config` inputs
3. **Validates:**
   - lower_threshold >= 0 (line 3574)
   - T4 > 0 (line 3577)
   - T5 > 0 (line 3580)
4. Shows confirmation modal
5. If confirmed: `performSaveAvgTypeDConfigs()`:
   - POSTs `/api/avg-type-d/config/bulk`
   - POSTs `/api/avg-type-d/config` with TTL, debounce, T5, tolerance
   - Broadcasts update
6. Success alert

**broadcastEventConfigUpdate() (lines 3998–4077):**

Orchestrates reload of ALL event config after any save:
1. Reloads Type A enabled state and per-sensor configs
2. Reloads Type B enabled state and per-sensor configs
3. Reloads Type C enabled state and per-sensor configs
4. Reloads Type D enabled state and per-sensor configs

Catches failures and warns to console but doesn't block.

---

## 13. Live Apply & Restart Semantics

**Global Config Changes (apply immediately without restart):**
- Toggle Type A/B/C/D enabled state → immediately affects running detector
- Change Mode Switching thresholds/durations → immediately applies to all detectors (line 1368: "Changes take effect immediately on all active detectors")
- Change Type A/B/C/D debounce → affects detector after debounce resets

**Per-Sensor Config Changes:**
- Type A per-sensor: affects detector on next evaluation cycle
- Type B per-sensor: affects detector on next evaluation cycle
- Type C per-sensor: affects detector on next evaluation cycle
- Type D per-sensor: affects detector on next evaluation cycle

**Auto-Restart:**
- Takes effect after next save
- Server performs graceful shutdown/restart at scheduled time
- ~5–10 seconds downtime (line 1275)

**No Explicit Restart Required Anywhere** — all changes are live-apply.

---

## 14. TTL Configuration

**TTL Sources:**

1. **Type A:** Loaded from per-sensor config endpoint, default 5 seconds (line 2309 default)
   - Stored in per-sensor config object as `ttl_seconds`
   - Synced globally when per-sensor configs saved

2. **Type B:** Loaded from global config endpoint, default 10 seconds (line 2490 default)
   - Global TTL applies to all sensors (line 4625–4628 sync)
   - Updated in `/api/avg-type-b/config` POST

3. **Type C:** Loaded from global config endpoint, default 3 seconds (line 2678 default)
   - Global TTL (line 4629–4632 sync)
   - Updated in `/api/avg-type-c/config` POST

4. **Type D:** Loaded from global config endpoint, default 8 seconds (line 2869 default)
   - Global TTL (line 4633–4637 sync)
   - Updated in `/api/avg-type-d/config` POST

**TTL Sync Behavior (lines 4622–4639):**
- DOMContentLoaded listener
- When user edits any TTL cell in Type B/C/D, **all TTL cells of that type are synced to same value**
- Ensures TTL is always global across sensors
- Type A TTL: per-sensor in grid but synced on save

**Round-Trip Behavior:**
- Load: fetch global config, set all TTL inputs to same value
- Edit: user changes one TTL cell, JS syncs all others immediately
- Save: captures first TTL input found, sends to backend
- Load: fetch again, all inputs updated to fetched value

---

## 15. Tolerance & Threshold Formula UI Hints

**Type A: Variance (%) (line 1397–1399)**
> Variance percent (standard deviation ÷ mean × 100)
- Formula: CV% = (σ / μ) × 100
- UI Label: "Variance (%)" per sensor
- Default: 10%
- Range: 0 to unbounded

**Type B: Post-Window Deviation (line 1450)**
> Tolerance uses percentage of running average. Bounds = avg × (1 ± tolerance%/100).
- Formula: band_lower = avg_T2 × (1 - tol% / 100), band_upper = avg_T2 × (1 + tol% / 100)
- Example in code (line 2473 title): "50% → ±50 units, 200% → ±200 units" (assuming Ref_Value=100)
- UI Label: "Tolerance (%)"
- Default: 5.0%

**Type C: Average Range Validation (lines 1500–1501)**
> Detects sustained offset during the window using absolute thresholds
- NO percentage formula
- Fields: "Lower Tolerance" (lower_threshold), "Upper Tolerance" (upper_threshold) — **absolute values, not percentages**
- Default: 51.9 (lower), 52.0 (upper)
- Validation: lower <= upper (line 3326)

**Type D: Two-Stage Smoothed (line 1543)**
> Detects sustained offset using absolute thresholds
- Fields: Tolerance (%) but actually stored as lower_threshold/upper_threshold
- Formula (line 2851 title): "band = avg_T5 ± (Ref_Value × tolerance%) where Ref_Value=100. e.g. 50% → ±50 units, 200% → ±200 units"
- Default: 5.0%
- T5 is baseline window for calculating avg_T5

**REF_VALUE Constant:**
- Hardcoded as 100 in all tolerance calculations (implicit in code comments)
- Not exposed in UI, implicit in formula

---

## 16. Quirks & Pitfalls

**Critical Ordering Issues:**

1. **Type A Grid Load Order (line 1611 comment):**
   ```
   "Load AFTER grid build (grid rebuilds reset all inputs to defaults)"
   ```
   - Grid building (buildSensorGrid) sets all inputs to hardcoded defaults
   - Per-sensor config load (loadSavedPerSensorConfigs) must run AFTER to populate with saved values
   - If order reversed: saved configs overwritten by defaults

2. **Debounce Capture (lines 3837–3856):**
   - When calling `saveTypeADebounceToGlobal()`, must capture debounce value **synchronously BEFORE async calls**
   - Backend grid rebuild might reset DOM, losing reference to input element
   - Solution: capture `capturedDebounce` before any fetch() calls

**TTL Sync Side Effect:**

3. **TTL Input Sync (lines 4625–4637):**
   - DOMContentLoaded listener on document level
   - When user edits `.avg-b-config[data-type="ttl"]`, triggers input event
   - Listener syncs all OTHER TTL inputs in same type to same value
   - Means TTL is **always global per type**, even though displayed per-sensor row
   - User cannot set different TTL per sensor within same event type

**Cell Focus Tracking Race Condition:**

4. **lastFocusedCell Nullability:**
   - `lastFocusedCell` is global, never cleared
   - If user clicks one cell, then immediately clicks "Save All Changes", applies to previous focused cell
   - "Apply to All 12 Sensors" requires `lastFocusedCell` to be set; shows warning if null
   - No timeout or blur-based cleanup

**Tolerance Field Ambiguity (Type C vs Type D):**

5. **"Lower Tolerance" / "Upper Tolerance" Labels:**
   - Type C: fields labeled "Lower Tolerance" and "Upper Tolerance" but contain **absolute thresholds** (not percentages)
   - Type D: field labeled "Tolerance (%)" but used as threshold
   - Naming inconsistency; backend may call them `lower_threshold`, `upper_threshold`

**Validation Gaps:**

6. **Type A Validation:**
   - No explicit client-side validation in `saveAllIndividualConfigs()`
   - Server may enforce T1 > 0, but UI allows any value
   - No range hints to user

7. **Type B/C/D "Apply Selected Sensor" Buttons:**
   - UI says "Select a cell above, then use 'Apply Selected Sensor' to update the live detector"
   - These buttons exist in old code comments but **no actual buttons in current HTML**
   - Function `updateAvgTypeBConfig()` (line 4118) exists but never called from UI
   - Dead code or incomplete UI

**Hardcoded Magic Numbers:**

8. **T5 Fallback (line 4548):**
   - `const t5Val = t5Input ? parseFloat(t5Input.value) : (current.t5_seconds ?? 30.0);`
   - If input not found, defaults to 30.0 seconds (hardcoded fallback)

9. **Startup/Break Duration Defaults (line 4539–4540):**
   - startupSec = 0.1 (100 ms) if undefined
   - breakSec = 2.0 (2000 ms) if undefined

10. **Tolerance Fallback (Type D, line 3629):**
    - `const tolVal = tolInput ? parseFloat(tolInput.value) : (current.lower_threshold ?? 5.0);`
    - Defaults to 5.0 if input or config missing

**Missing Error Handling:**

11. **Network Failures in broadcastEventConfigUpdate():**
    - Catches errors but only logs to console (`.catch(err => console.warn(...))`)
    - Does not retry or notify user of partial reload failures
    - User sees success alert even if reload partially failed

12. **Modal Callback Cleanup:**
    - `showEnableConfirmModal()` creates modal DOM dynamically (line 1935)
    - Modal appended to document.body (line 1960)
    - Modal self-removes on close (line 1957)
    - No explicit cleanup if callback never called (e.g., page unload mid-modal)

**Device/Sensor Grid Assumptions:**

13. **Hardcoded 12 Sensors per Device:**
    - All grids assume exactly 12 sensors per device (lines 2248, 2436, etc.)
    - No dynamic sensor count from API
    - If device has != 12 sensors, grid will be wrong

14. **Device Sort Order:**
    - Devices sorted by device_id ascending (line 2109: `sort((a, b) => a.device_id - b.device_id)`)
    - Assumes device_id is numeric

**Dead Code / Unused Vars:**

15. **avgTypeBRunning, avgTypeDInterval, etc. (lines 4112–4116):**
    - Declared but never used in current HTML
    - Functions like `startAvgTypeBDetection()` (line 4169) exist but no UI buttons
    - Suggests incomplete UI or removed features

16. **updateAvgTypeBStatistics(), updateAvgTypeBEventLog() (lines 4212–4286):**
    - Functions exist, poll `/api/avg-type-b/stats` and `/api/avg-type-b/events`
    - No UI elements (#b-total-samples, #b-event-log) in current HTML
    - These are remnants from a real-time stats dashboard (not active)

---

## 17. BREAK and Mode-Switching Specifics

**BREAK Priority:**

- BREAK is **NOT** a separate event type like A/B/C/D
- Instead, BREAK is a **sensor mode** in the three-mode system (POWER_ON → STARTUP → BREAK)
- When any sensor enters BREAK mode:
  - Type A/B/C/D events are **NOT triggered** for that sensor
  - But sensor is still monitored (threshold checks continue)
  - Once sensor value rises above startup_threshold again, exits BREAK → STARTUP
- BREAK applies **independently per sensor** (line 1288: "per-sensor")

**Mode-Switching Configuration (Always Enabled):**

- Global config section (lines 1280–1371)
- Controls ALL sensors simultaneously
- Four thresholds/durations, no per-sensor override
- Endpoint: `/api/mode_switching/config`
- Payload: `{ enabled: true, startup_threshold, break_threshold, startup_duration_seconds, break_duration_seconds }`

**Mode State Transitions (lines 1362–1369):**

```
POWER_ON (initial)
  ↓
  [sensor_value > startup_threshold for startup_duration_seconds]
  ↓
STARTUP (events triggered for A/B/C/D)
  ↓
  [sensor_value < break_threshold for break_duration_seconds]
  ↓
BREAK (events NOT triggered, sensor monitored)
  ↓
  [sensor_value > startup_threshold for startup_duration_seconds]
  ↓
STARTUP
```

**Interaction with Event Types:**

- Mode state is **independent** of A/B/C/D enabled/disabled toggles
- If Type A enabled but sensor in BREAK mode: Type A events not triggered
- If Type A disabled but sensor in STARTUP: Type A events still not triggered (disabled)
- If Type A enabled AND sensor in STARTUP: Type A events triggered (both conditions met)

---

## 18. Re-Implementation Checklist for HERMES Rewrite

**Must Preserve:**

- [ ] Tab-based UI with 4 event types (A/B/C/D) + 2 config sections (auto-restart, mode-switching)
- [ ] Per-sensor grid layout (transposed: rows=config fields, cols=sensors 1–12)
- [ ] Cell focus tracking for "Apply to All 12 Sensors" bulk apply
- [ ] Confirmation modals before enable/disable toggles and bulk saves
- [ ] TTL syncing within same event type (all sensors same TTL)
- [ ] Global vs per-sensor config hierarchy
  - Type A: per-sensor T1/tolerance/TTL, global debounce
  - Type B: per-sensor T2/tolerance/TTL, global debounce
  - Type C: per-sensor T3/lower/upper/TTL, global debounce
  - Type D: per-sensor T4/T5/tolerance/TTL, global debounce
- [ ] Debounce capture before async calls (Type A specific)
- [ ] broadcastEventConfigUpdate() reload orchestration
- [ ] Mode-switching threshold/duration converters (ms ↔ s)
- [ ] Auto-restart hour/minute validation [0–23] / [0–59]
- [ ] Validation rules:
  - Type B: T2 > 0, tolerance >= 0
  - Type C: lower <= upper, T3 > 0
  - Type D: T4 > 0, T5 > 0, tolerance >= 0
  - Mode-switching: startup_threshold > break_threshold
- [ ] Load/Save sequencing (grid build THEN load saved configs)
- [ ] Alert messages with 5-second auto-hide

**Can Modernize:**

- [ ] Replace inline `onchange="func()"` with event listeners
- [ ] Use fetch() → async/await consistently
- [ ] Replace global `lastFocusedCell` variables with event.target tracking
- [ ] Centralize modal creation (not dynamic string interpolation)
- [ ] TypeScript for Type A/B/C/D field definitions
- [ ] Reactive UI framework (Vue/React) for grid management
- [ ] Real-time validation feedback (not just on save)

**Testing Requirements:**

- [ ] Save Type A with 2 devices × 12 sensors = 24 configs + debounce
- [ ] Apply cell value to all 12 sensors, verify bulk update
- [ ] Toggle Type B on/off, verify POST /api/avg-type-b/start and /stop
- [ ] Edit TTL in Type C, verify all Type C TTL cells sync
- [ ] Save Type D, verify T4, T5, tolerance all sent correctly
- [ ] Change mode-switching thresholds, verify immediate effect
- [ ] Set auto-restart to future time, verify next_restart displayed
- [ ] Disable Type A, save, verify enabled: false sent
- [ ] Load page with no devices, verify "No devices configured" message
- [ ] Test all validation error paths (hour > 23, lower > upper, T5 = 0, etc.)

---

## Document Metadata

- **Generated:** Phase 0.5 (Behavior Contract Capture)
- **Source File:** `/home/embed/hammer/templates/event_config.html` (4663 lines)
- **Last Complete Read:** Lines 1–4663
- **Total Functions:** 60+
- **Total Endpoints:** 25
- **Validation Rules:** 20+
- **Input Fields:** 100+

**Key Dependencies:**
- Sidebar routing (/device-config, /offset-config)
- Auth endpoint (/api/auth/logout)
- Devices & sensor schema (12 sensors per device assumed)
- 4 event type detector APIs (A/B/C/D)
- System auto-restart and mode-switching subsystems

