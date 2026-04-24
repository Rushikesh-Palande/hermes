# device_detail.html Reference Document

**File Path:** `/home/embed/hammer/templates/device_detail.html`  
**Lines:** 7645 total  
**Purpose:** Main sensor dashboard for live real-time data streaming (~123 Hz) and historical event analysis with ECharts/uPlot visualization, sensor value tables, event windows, and CSV exports.

---

## 1. Top-Level Layout (DOM Order)

### Page Structure
- **Body:** `<div class="sidebar">` (fixed left sidebar, 70px wide)
- **Main container:** `<div class="container">` (max-width: none, margin: 0 48px)
  - **Hidden devices grid:** `<div id="devices-grid">` (display:none; device card management hidden on device detail page)
  - **Device panel:** `<div id="device-panel">` (dynamically shown/hidden)
    - **Panel header:** device name, status badge, control buttons
    - **Chart container:** `<div id="event-chart-echarts">` (ECharts, ~680px height at 2560px)
    - **Sensor modes grid:** `<div id="sensor-modes-grid">` (12 badges showing POWER_ON/STARTUP/BREAK)
    - **Stats grid:** `<div id="stats-grid">` (5 stat cards: RX Rate, Moving Avg, Min/Max, Expected, Threshold)
    - **Real-time chart:** `<div id="realtime-chart">` (Chart.js, TX/RX frame counts)
    - **Controls row:** Mode toggle (Live/Historical), time range buttons, filters
    - **Sensor data tables:** Live decimal & hex tables with pagination
    - **Event history:** Event list with pagination, CSV export
    - **Event window modal:** ECharts graph showing ±9s around event
  - **Modals:**
    - `#rename-modal` — Edit device name
    - `#confirm-modal` — Confirm rename
    - `#add-device-modal` — Create new device
    - `#delete-device-modal` — Delete device confirmation
    - `#event-window-graph-overlay` & `#event-window-graph-modal` — Event detail window

### Key IDs
- **Live mode:** `event-chart-echarts`, `sensor-modes-grid`, `sensor-dropdown-panel`, `live-data-tbody-decimal`
- **Historical mode:** `historical-sensor-dropdown-panel`, `historical-event-dropdown-panel`
- **Controls:** `live-pause-btn`, `mode-btn-live`, `mode-btn-historical`, `win-btn-1000`, `win-btn-6000`, `win-btn-12000`
- **Tables:** `event-list-tbody`, `live-data-tbody-decimal`

---

## 2. Endpoint Inventory

| Endpoint | Method | Params | Response | Polling | Notes |
|----------|--------|--------|----------|---------|-------|
| `/api/live_stream` | GET (SSE) | `device_id`, `sensor_ids=all`, `interval=0.05`, `max_samples=<calculated>` | JSON batch: `{timestamps, sensor_data, sensor_modes, ts_encoding}` | Continuous (server-driven) | Real-time SSE with auto-retry; server retries set to 2000ms |
| `/api/live_sensor_data` | GET | `device_id`, `sensor_ids=all`, `max_samples=<calculated>` | `{success, data: {timestamps, sensors, stats}}` | On-demand (table refresh) | Fast path fetches last N samples from ring buffer (~2000 cap) |
| `/api/event/list` | GET | `device_id`, `start_time`, `end_time`, `limit=5000` | `{success, events: [...]}` | On-demand | Wide format (sensor1_value, sensor1_average, sensor1_event_a, etc.) |
| `/api/event/{event_id}/data` | GET | `event_id` (path) | `{success, data: {event_info, triggered_sensors, sensor_N, bounds_B/bounds_C/bounds_D, event_center}}` | On-demand | Detailed event with ±9s data window BLOB. **Path param, not query.** `data.sensor_N` is an array of `{timestamp, value, avg, cv}`. |
| `/api/event/config/type_a/per_sensor` | GET | `device_id` | `{configs: {sensorId: {...}}}` | On-demand | Per-sensor Type A config (variance threshold) |
| `/api/event/config/type_a` | GET | None | `{config: {...}}` | On-demand | Global Type A config |
| `/api/avg-type-b/config/per_sensor` | GET | None | `{configs_by_device: {devId: {sensorId: {...}}}}` | On-demand | Type B per-sensor config |
| `/api/avg-type-b/config` | GET | None | `{config: {...}}` | On-demand | Global Type B config |
| `/api/avg-type-c/config/per_sensor` | GET | None | `{configs_by_device: {...}}` | On-demand | Type C per-sensor config |
| `/api/avg-type-c/config` | GET | None | `{config: {...}}` | On-demand | Global Type C config |
| `/api/avg-type-d/config/per_sensor` | GET | None | `{configs_by_device: {...}}` | On-demand | Type D per-sensor config |
| `/api/avg-type-d/config` | GET | None | `{config: {...}}` | On-demand | Global Type D config |
| `/api/db/frames` | GET | `start_time`, `end_time`, `limit=100` | `{frames: [{channel, data, decoded_data, data_length, timestamp, received_at}]}` | On-demand | Frame data for event popup detail view |
| `/api/stats` | GET | None | `{total_rx, total_tx}` | Every 1s | RX/TX frame counters for real-time & event graphs |
| `/api/frames/grids` | GET | `device_id` | `{tx_frames: [...], rx_frames: [...]}` | Every 1s (disabled) | TX/RX grid display |
| `/api/frames/export` | GET | `type=tx\|rx`, `limit=1000` | `{tx_frames: [...], rx_frames: [...]}` | On-demand | Export TX/RX frames to CSV |
| `/api/devices` | GET / POST | — / `{device_name, topic, ...}` | `{success, devices}` / `{success, error?}` | On-demand | List / create devices |
| `/api/devices/{id}` | GET / PATCH / DELETE | path `{id}` | `{success, device}` / `{success}` | On-demand | Per-device CRUD |
| `/api/devices/{id}/status` | GET | path `{id}` | `{running, ...}` | 1 s poll | Device status badge update |
| `/api/devices/{id}/initialize` | POST | `{...}` | `{success}` | On-demand | Initialize device connection |
| `/api/devices/{id}/start` | POST | — | `{success}` | On-demand | Start detection on device |
| `/api/devices/{id}/stop` | POST | — | `{success}` | On-demand | Stop detection; clears SSE |
| `/api/auth/logout` | POST | — | — | On-demand | Logout, redirects to /login |

---

### CRITICAL BEHAVIOR: shared event BLOB (lines 4061–4066)

When multiple event types (A/B/C/D) fire within 500 ms for the same sensor, the **legacy `batch_update_event_detection` merges them into one DB row with one shared BLOB**. The BLOB's stored `event_type` reflects whichever type wrote last. This is the root cause of [`BUG_DECISION_LOG.md` #5](../../contracts/BUG_DECISION_LOG.md) (500 ms dedup).

Frontend mitigation in `loadEventDataWindow` (line 4064):
```js
if (fallbackInfo && fallbackInfo.event_type) {
    eventInfo.event_type = fallbackInfo.event_type;   // trust what the user actually clicked
}
```

And bounds may live under `windowData.bounds_B` / `bounds_C` / `bounds_D` keys instead of `event_info.lower_bound/upper_bound` — the reconstruction logic at lines 4070–4108 handles this.

The rewrite removes the 500 ms dedup (per BUG_DECISION_LOG), so one event = one row = one type. The bounds_{B,C,D} fallback and `fallbackInfo.event_type` override become unnecessary in the rewrite.

---

## 3. SSE Streaming Details (`/api/live_stream`)

### Query Parameters
- **`device_id`** (required): Device to stream from (e.g., `"1"`)
- **`sensor_ids`** (required): `"all"` (client-side filtering applied later)
- **`interval`** (optional): `"0.05"` (fallback poll interval; server is event-driven, not polled)
- **`max_samples`** (required): Calculated as `Math.ceil((liveWindowMs / 1000) * 123) + 100` capped at 2000
  - For 1s window: ~150 samples
  - For 6s window: ~900 samples
  - For 12s window: ~1600 samples

### Batch Structure
```javascript
{
  "timestamps": [<ms|delta-encoded>],         // milliseconds or delta-encoded
  "ts_encoding": "delta_ms" | undefined,       // present if delta-encoded
  "sensor_data": { "1": [...], "2": [...], ... },  // values per sensor ID
  "sensor_modes": { "1": "POWER_ON", "2": "STARTUP", ... }  // optional
}
```

### Buffer Management (`echartsBuffer`)
- **Type:** `Map<sensorId: [timestamp_ms, value][]>`
- **Capacity:** 2000 points per sensor (96 KB memory per sensor at 8 bytes/point)
- **Trimming logic:**
  1. Detect STM32 restart gaps (>1s discontinuity between or within batch)
  2. Clear stale pre-restart data to avoid pollution
  3. Slice to timestamp range `[windowStart, latestTs]`
  4. Keep one anchor point just before `windowStart` for step-line continuity
  5. Enforce MAX_BUFFER_POINTS cap (2000) by removing oldest data

### Rolling Window States
- **`liveWindowMs`** (global): Current display window (1000, 6000, 12000 ms, or custom via wheel zoom)
- **`_sseLatestTs`** (global): Most recent timestamp received from SSE (ms)
- **`ROLLING_WIN_SECS = 4.0`** (constant): Server-side T1 window for CSV rolling stats (matches event config)
- **`pausedTimeRange`** (global): `{min, max}` when zooming paused; `null` when running

### Reconnection Logic
- **Auto-retry:** Server sets `retry: 2000` header → browser auto-reconnects after 2s
- **No explicit client close** on buffer overflow; ring buffer on server handles backpressure
- **On page refresh:** `last_seq=0` causes server to return pre+post restart data; client detects gap >1s inside batch and slices to keep only clean post-restart data

---

## 4. Charts Configuration

### ECharts Live Sensor Graph (`#event-chart-echarts`, ECharts v5.5.0)

**Library:** Apache ECharts  
**Container:** `<div id="event-chart-echarts">` (height: 680px @ 2560px resolution)  
**Data source:** `echartsBuffer` (client-side buffer)  
**Update rate:** requestAnimationFrame (~60 Hz max) via `_rafRender()`  

**Series Configuration:**
```javascript
{
  name: "Sensor N",
  type: 'line',
  step: true | false,           // 'end' for digital, false for analog
  smooth: false | true,          // Smooth interpolation for analog signals
  showSymbol: false,             // No markers on points (density > 100 pts/s)
  animation: false,              // Disable animation (critical for 123 Hz)
  sampling: 'none',              // Don't downsample (preserve 10ms resolution)
  lineStyle: { width: 2, color: SENSOR_COLORS[sensorId-1] },
  itemStyle: { color: SENSOR_COLORS[sensorId-1] }
}
```

**Axis Setup:**
- **X-axis:** `type: 'time'`, `boundaryGap: false`, auto-scale or paused range
- **Y-axis:** `type: 'value'`, auto-scaled per visible data
- **Tooltip:** Cross-type with custom formatter showing timestamp + all series at cursor
- **Toolbox:** Save as image, data zoom, restore (right-aligned)
- **Legend:** Hidden by default, shows on hover

**Zoom/Pan Behavior:**
- **Mouse wheel:** Clamped to [100ms, 30s] via ZOOM_FACTOR (1.2)
- **Paused zoom:** Centers on cursor; stores fixed range in `pausedTimeRange`
- **Running zoom:** Moves window right edge to latest timestamp
- **Anchor point:** One point just before `windowStart` maintained at all times to keep step lines flat until next change

**Critical ECharts perf trick** (line 6752–6758):
```js
echartsInstance.setOption({ xAxis: xAxisConfig, series: series }, {
    replaceMerge: ['series'],  // Only replace series array; keep axes/tooltip/toolbox
    lazyUpdate: false          // Update immediately (no batching)
});
```
Without `replaceMerge: ['series']`, ECharts accumulates memory on every update and produces 1–2 s delays after a few minutes of live streaming. This is non-obvious and must be preserved in the rewrite's chart wrapper.

**Auto analog/digital detection** (lines 6670–6728):
- Throttled: runs once per second per sensor (`AUTO_DETECT_THROTTLE_MS = 1000`).
- Looks at last 30 samples; classifies as analog iff range > 10 AND < 3 % of samples have big-jumps (> 30 % of range). Range ≤ 10 → analog (stable).
- Hysteresis: once analog, hold `step:false / smooth:true` for `SMOOTH_HOLD_MS = 5000`. Prevents flicker on the boundary.
- Every other frame renders with `step:'end' / smooth:false`.

**Color Mapping:**
```javascript
const SENSOR_COLORS = [
  '#FF0000', '#00FF00', '#0000FF', '#FFFF00', '#FF00FF', '#00FFFF',
  '#FF8000', '#8000FF', '#FF0080', '#00FF80', '#0080FF', '#FF8080'
];
```

### ECharts Event Window Graph (`ewgChart`, ECharts v5.5.0)

**Trigger:** Click scatter point in historical mode → opens `#event-window-graph-modal`  
**Data:** Event data window (±9s around trigger) with multiple series (Sensor Value, Average, CV%)  
**Series for Type A (Variance):**
- Sensor Value (line, step:'end')
- Rolling Average (area, step:'end')
- CV% (line, scaled on secondary Y-axis)

**Formatters:** Relative time format (`+X.XXXs from trigger`)

### Chart.js Real-Time Graph (`#realtime-chart`, Chart.js)

**Data:** TX/RX frame counts polled every 1 second from `/api/stats`  
**Datasets:**
1. TX Frames (purple, #667eea)
2. RX Frames (green, #10b981)

**Update:** `updateRealtimeChart()` every 1s, keeps last 60 points (1 minute)

---

## 5. Live Table (Sensor Value Decimal Table)

### Column Layout
| Column | Source | Format | Notes |
|--------|--------|--------|-------|
| Time | `timeStr` (from table data) | HH:MM:SS.mmm | Millisecond precision |
| Sensor Value | `decimalValue` | Float, 2 decimal places | Scaled by Modbus factor if applicable |
| Variance (CV%) | `_cv` or `variance` from stats | Float, 2 decimal places | Server-sent or recomputed per window |
| Rolling Average | `_avg` or `average` from stats | Float, 2 decimal places | Server-sent or recomputed per window |
| Sensor | `sensorId` | "Sensor N" | 1-12 |

### Row Count & Refresh
- **Cadence:** 500 ms (LIVE_TABLE_UPDATE_INTERVAL_MS)
- **Cap:** ~1500 rows (min displayed) per window size; clamped at 2000 to prevent memory bloat
- **Row order:** Newest first (descending timestamp)
- **Deduplication:** Remove exact consecutive duplicates caused by sub-ms display precision

### Rolling Stats Formula (4-second window)
**Constant:** `ROLLING_WIN_SECS = 4.0`

For each newly added sample at time `t`:
1. Gather all samples from same sensor in `[t - 4, t]`
2. Compute mean: `mean = Σ(values) / n`
3. Compute variance: `variance = Σ((value - mean)²) / n`
4. Compute CV%: `cv% = (√variance / mean) * 100` (if mean ≠ 0, else 0)
5. Store `_cv` and `_avg` on the row object

**Client-side recomputation:** Done in `updateLiveSensorDataTables()` for newly added rows via `liveExportStore` (Map of all accumulated samples in current window)

### Anchor-Point Logic
- **Purpose:** Keep step-line flat from previous value until next change
- **Implementation:** When trimming buffer to window, insert one point at `[windowStart, lastBefore[1]]` before in-window points
- **Effect:** If user zooms into middle of flat section, line stays flat back to window edge, not jumping to next change

---

## 6. Event History Panel

### Event List View
- **Container:** `#event-list` (in modal or card)
- **Fetch:** `loadHistoricalData(minutes)` or `loadHistoricalDataCustom(startTime, endTime)`
- **API:** `/api/event/list` with time range and device_id
- **Response format:** Wide (sensor1_value, sensor1_event_a, etc. per row per event timestamp)

### Event Data Window (±9s Modal)
- **Trigger:** Click scatter point in event graph OR click row in event list
- **Load:** `loadEventDataWindow(eventId, sensorId, fallbackInfo)`
  - Fetch from `/api/event/data-window?event_id=X&device_id=Y`
  - Response: `{rows: [{timestamp, sensorId, value, avg, cv}], event_info: {...}, window_data: {...}}`
- **Display:** ECharts graph with toggle switches (Value / Avg / CV%)
- **Export:** `exportEventWindowCSV()` includes config block + data table with trigger row marked

### CSV Export Flows
1. **Live mode export** (`exportEventDataToCSV('live')`):
   - Exports all accumulated data in `liveExportStore` (current window)
   - Recomputes rolling 4-second CV%/avg for each sample
   - Columns: Timestamp, Time, Sensor, Sensor Value, CV%, Average

2. **Historical event list export** (`exportEventDataToCSV('historical')`):
   - Exports visible event list rows
   - Columns: Time, Sensor, Event Type, Sensor Value

3. **Event window export** (`exportEventWindowCSV()`):
   - Most detailed: includes event config block + data
   - Columns: Timestamp, Time, Sensor, Event Type, Sensor Value, [CV% if Type A], Rolling Average, Trigger Row (YES/NO)
   - Fetches per-sensor and global configs for the event type

### Filter UI
- **Live mode:** Sensor checkboxes in `#sensor-dropdown-panel`
- **Historical mode:** Sensor & Event Type dropdowns in separate panels
- **Persistence:** `localStorage` key `sensor_filter_${CURRENT_DEVICE_ID}_${id}`
- **Apply:** Instant re-render (no page reload)

### Pagination
- **Event list:** Simple; uses `eventDataStore.currentPage` and `currentRowsPerPage`
- **Navigation:** `changeEventDataPage(tableType, direction)` updates page and re-renders

---

## 7. Window Selector (1s / 6s / 12s Buttons)

### Button IDs & State
- `#win-btn-1000`, `#win-btn-6000`, `#win-btn-12000`
- Global: `WIN_BTNS = [1000, 6000, 12000]` (ms)
- Active button has blue background (#eff6ff) and text color (#3b82f6)

### User Actions & Effects
1. **Click button:** `setLiveWindow(ms)`
   - Updates `liveWindowMs` global
   - Updates button active state
   - Does NOT clear buffer (avoids flicker)
   - If paused: forces re-render via `_rafRender()` to show new window
   - If running: next rAF will auto-trim buffer to new window

2. **Mouse wheel zoom:** `initMouseWheelZoom()`
   - Scroll up = smaller window (zoom in)
   - Scroll down = larger window (zoom out)
   - Clamped to [100ms, 30s]
   - If paused: zooms centered on mouse cursor (stores in `pausedTimeRange`)
   - If running: moves window right edge to latest timestamp

### Zoom Level Indicator
- **Element:** `#zoom-level-indicator`
- **Display:** Only when zoomed to non-button value (custom size)
- **Format:** "100ms" or "5.3s" or "2.1min"

---

## 8. Client-Side Rolling Stats

### Variance (CV%) Formula
```javascript
// For a window of samples [s1, s2, ..., sn] in time [t-T, t]
const T = 4.0;  // seconds
const n = samples.length;
const mean = samples.reduce((sum, s) => sum + s.value, 0) / n;
const variance = samples.reduce((sum, s) => sum + (s.value - mean)² / 0) / n;
const cv_percent = (Math.sqrt(variance) / Math.abs(mean)) * 100;
```

### Columns Computed vs Server-Sent
- **Server-sent:** Batch-level stats (one value per SSE batch, applies to all samples in batch)
- **Client-computed:** Per-sample rolling window via `updateLiveSensorDataTables()` → `liveExportStore`
- **Used in CSV export:** Always prefer `_cv` and `_avg` (client-computed) over batch-level stats

### Window Size Constant
- **Live table:** 4.0 seconds (ROLLING_WIN_SECS)
- **Server backend (Type A):** Configurable T1 timeframe (default 4s)

---

## 9. Modals & Dialogs

| Modal ID | Trigger | Content | Buttons |
|----------|---------|---------|---------|
| `#rename-modal` | Click edit icon on device card | Device name input (50 char max) | Cancel, Save Changes |
| `#confirm-modal` | Click "Save Changes" in rename | Confirmation text "Are you sure?" | No, Yes |
| `#add-device-modal` | Click "Add Device" card | Device type dropdown, name, Modbus fields (IP, port, slave ID, register, scaling, poll interval) | Cancel, Create Device |
| `#delete-device-modal` | Click delete icon on device card | Warning text + hidden device ID | Cancel, Delete Device |
| `#event-window-graph-modal` | Click scatter point in historical graph OR row in event list | ECharts graph of ±9s window with toggle checkboxes (Value/Avg/CV%) + zoom controls + CSV export button | Close (×) button |

### Modal Styling
- **Overlay:** `#modal-overlay` (fixed, rgba(0,0,0,0.5), z-index: 10000)
- **Content:** `#modal-content` (max-width: 800px, animation: modalSlideIn 0.3s)
- **Close:** Click ×, close button, or cancel button

---

## 10. Event Listeners

### Global Click Handlers
- **`document.addEventListener('click')`** (line 6357): Close dropdowns when clicking outside `.custom-dropdown`
- **`chartContainer.addEventListener('wheel')`** (line 7022, capture phase): Mouse wheel zoom with debounce (50ms)

### Button Click Handlers
| Button ID | Function | Effect |
|-----------|----------|--------|
| `#live-pause-btn` | `toggleLiveUpdates()` | Pause/resume SSE stream |
| `#mode-btn-live` | `switchToLiveMode()` | Hide historical controls, show live chart + tables |
| `#mode-btn-historical` | `switchToHistoricalMode()` | Hide live, show historical filters + event graph |
| `#win-btn-1000/6000/12000` | `setLiveWindow(ms)` | Set window size, no buffer clear |
| `#export-btn` | `exportEventDataToCSV('live')` or `exportEventDataToCSV('historical')` | Download CSV of visible data |
| `#event-window-export-btn` | `exportEventWindowCSV()` | Download CSV of ±9s event window with config |

### Checkbox Change Handlers
- **Sensor checkboxes** (live mode): `createDropdownCheckbox()` → `updateDropdownLabels()` + re-render via `_rafRender()`
- **Historical sensor/event checkboxes:** `updateHistoricalDropdownLabels()` + `loadHistoricalData()`

### Other Listeners
- **Window resize:** Debounced (250ms) → `echartsInstance.resize()`
- **Window unload:** Cleanup SSE, dispose charts, clear intervals
- **ECharts click:** Click scatter point → `loadEventDataWindow()`
- **Event timeframe select change (in popup):** `onchange` → re-fetch frames with new time range

---

## 11. Quirks & Pitfalls

### SSE Restart Hazards
1. **STM32 firmware restart:** Creates gap in timestamps (>1s jump)
   - **Detected in `_ingestSSEBatch()`:** Case A (between batches) clears buffer; Case B (inside batch) slices data
   - **Risk:** Pre-restart stale data pollutes graph if not detected
   - **Mitigation:** Always maintain `_sseLatestTs` and check discontinuity threshold

2. **Browser tab backgrounding:** EventSource may pause or reconnect
   - **Mitigation:** Server retry header handles reconnection automatically
   - **Client-side check:** Compare `_sseLatestTs` with expected latest to detect stale data

### 500-Sample Cap Rationale
- **MAX_BUFFER_POINTS = 2000** (not 500) to accommodate 12s window @ 123 Hz (~1476 points)
- **2000 cap:** Allows full 12s window + headroom; prevents memory bloat with large windows
- **Memory per sensor:** ~96 KB (2000 points × 8 bytes × 12 sensors = 1.2 MB total)

### Anchor-Point Logic Details
- **Window trim binary search:** O(log N) via manual lo/hi pointers (line 6619–6623)
- **Purpose:** Keep step-line flat for digital signals; prevents jumping to next value on zoom
- **Example:** If data is [1, 1, 1, 2, 2] and window starts after first three 1's, insert anchor point `[windowStart, 1]` so step line stays at 1

### Hardcoded Values & Magic Numbers
| Value | Location | Purpose |
|-------|----------|---------|
| `123` | Multiple | Default ~123 Hz sample rate (8ms poll interval) |
| `4.0` | ROLLING_WIN_SECS | Rolling average window for live tables & CSV export |
| `8` | Modbus poll interval (modal) | Default poll interval in ms (~125 Hz) |
| `100` | getCurrentModbusScaling() default | Modbus scaling factor default |
| `2000` | Multiple | Server ring buffer size (samples) |
| `2000` | MAX_BUFFER_POINTS | Max ECharts buffer per sensor |
| `1200` | Calculated in table fetch | Timestamps needed per selected sensor |
| `1500` | Table rows max | Absolute cap on table rows shown |
| `16` | liveUpdateIntervalMs | Target 60 FPS (not used; replaced by rAF) |

### Inline Styles & Z-Index Issues
- **Sidebar z-index:** 1000 (fixed left, always on top)
- **Modal overlay z-index:** 10000 (above everything)
- **Modal content z-index:** 10000 (same as overlay, stacks correctly)
- **Potential conflict:** Event popup dynamically creates overlay with z-index 9999 (event-popup) and 10000 (event-overlay)

### TODO/FIXME Comments
- **Line 6162-6166:** Zoom/pan infrastructure notes (removed — no longer used)
- **Line 6294-6296:** UI_LOGGING disable (verbose logs commented out for performance)
- **Line 6649-6654:** Latency debug logging disabled (CPU overhead)
- **Line 6759-6768:** Render timing logs disabled

### Common Bugs
1. **Buffer not cleared on mode switch:** Use `echartsBuffer = {}` and `_lastStoredVal = {}` to reset
2. **SSE reconnect loses data:** Design expects always-new data; historical mode refetches
3. **Paused zoom loses context:** `pausedTimeRange` stores fixed range so unpausing doesn't jump
4. **Rolling stats drift:** Per-sample recomputation ensures consistency; server stats ignored

---

## 12. Function Index

### Device Management (lines 2111–3064)

| Function | Lines | Inputs | Outputs | Side Effects |
|----------|-------|--------|---------|--------------|
| `fetchWithRetry()` | 2121–2168 | url, options, timeout, retries | Promise<Response> | Exponential backoff retry logic |
| `handleLogout()` | 2170–2199 | event | — | Redirects to /logout |
| `loadDevices()` | 2203–2258 | — | — | Populates `loadedDevices`, calls `renderDeviceCards()` |
| `renderDeviceCards()` | 2259–2286 | devices | — | DOM: creates device cards in `#devices-grid` |
| `createDeviceCard()` | 2287–2346 | device | HTMLElement | Card with edit/delete buttons |
| `showErrorState()` | 2347–2361 | customMessage? | — | DOM: error message in `#devices-grid` |
| `addNewDevice()` | 2362–2385 | — | — | Shows `#add-device-modal` |
| `closeAddDeviceModal()` | 2386–2391 | — | — | Hides modal, clears form |
| `toggleDeviceTypeFields()` | 2392–2405 | — | — | Shows/hides Modbus fields based on dropdown |
| `submitNewDevice()` | 2406–2474 | — | — | POST to /api/devices, reloads device list |
| `editDevice()` | 2475–2502 | deviceId | — | Shows `#rename-modal`, prefills name |
| `closeRenameModal()` | 2503–2508 | — | — | Hides modal, clears state |
| `confirmRename()` | 2509–2524 | — | — | Shows `#confirm-modal` |
| `closeConfirmModal()` | 2525–2529 | — | — | Hides modal |
| `proceedWithRename()` | 2530–2542 | — | — | Calls `saveDeviceName()` |
| `saveDeviceName()` | 2543–2585 | deviceId, newName | — | PATCH /api/devices/{id}, updates card |
| `deleteDevice()` | 2586–2602 | deviceId | — | Shows `#delete-device-modal`, stores ID |
| `closeDeleteDeviceModal()` | 2603–2608 | — | — | Hides modal |
| `confirmDeleteDevice()` | 2609–2649 | — | — | DELETE /api/devices/{id}, reloads |
| `initializeDevice()` | 2650–2765 | deviceId, forceReinit? | — | POST /api/devices/{id}/initialize, polls status |
| `startDevice()` | 2766–2860 | deviceId | — | POST /api/devices/{id}/start, updates status badge |
| `stopDevice()` | 2861–2943 | deviceId | — | POST /api/devices/{id}/stop, clears SSE |
| `checkGlobalConfig()` | 2944–2979 | — | — | Checks if global device config is set |
| `updateAllDeviceStatus()` | 2980–2994 | status | — | Updates all device cards with status |
| `applyGlobalConfigToAllDevices()` | 2995–3063 | — | — | POST to apply global config to devices |

### Device Panel (lines 3064–3270)

| Function | Lines | Inputs | Outputs | Side Effects |
|----------|-------|--------|---------|--------------|
| `openDevicePanel()` | 3066–3109 | deviceId | — | Shows `#device-panel`, calls `startEventGraphUpdates()` |
| `closeDevicePanel()` | 3110–3150 | — | — | Hides panel, stops SSE, disposes charts |
| `updateDeviceCardStatus()` | 3151–3167 | deviceId, connected | — | Updates card badge color |
| `pollDeviceStatus()` | 3168–3238 | — | — | Polls /api/devices/{id}/status every 1s |
| `pollErrorStats()` | 3239–3269 | — | — | Polls /api/device/{id}/stats/error (disabled) |
| `updateDeviceCardsWithStatus()` | 3270–3287 | status | — | Updates all cards based on status array |

### UI Modes & Views (lines 3300–3850)

| Function | Lines | Inputs | Outputs | Side Effects |
|----------|-------|--------|---------|--------------|
| `showToast()` | 3316–3352 | message, type? | — | DOM: toast notification (auto-fade 3s) |
| `switchToLiveMode()` | 3353–3418 | — | — | Sets `isLiveMode=true`, shows live controls, starts SSE |
| `switchToHistoricalMode()` | 3436–3537 | — | — | Sets `isLiveMode=false`, hides live, loads historical data |
| `toggleLiveUpdates()` | 3538–3576 | — | — | Toggles `liveUpdatesPaused` |
| `populateHistoricalFilters()` | 3577–3608 | — | — | Creates sensor/event dropdowns |
| `toggleHistoricalSensorDropdown()` | 3609–3622 | event | — | Shows/hides sensor filter dropdown |
| `toggleHistoricalEventDropdown()` | 3623–3637 | event | — | Shows/hides event type dropdown |
| `selectAllHistoricalSensors()` | 3638–3650 | — | — | Checks all sensor checkboxes, re-renders |
| `clearAllHistoricalSensors()` | 3651–3663 | — | — | Unchecks all, re-renders |
| `selectAllHistoricalEvents()` | 3664–3676 | — | — | Checks all event type checkboxes |
| `clearAllHistoricalEvents()` | 3677–3690 | — | — | Unchecks all event types |
| `updateHistoricalDropdownLabels()` | 3691–3709 | — | — | Updates dropdown label text |
| `updateHistoricalEventDropdownLabels()` | 3710–3725 | — | — | Updates event type label |
| `fetchHistoricalEventConfigStates()` | 3726–3755 | — | dict | Fetches per-sensor config states |
| `applyHistoricalEventConfigStates()` | 3756–3775 | states | — | Applies visual state to checkboxes |
| `refreshHistoricalEventConfigs()` | 3776–3781 | — | — | Calls `fetchHistoricalEventConfigStates()` |
| `setTimeRange()` | 3782–3806 | event, minutes | — | Sets `selectedTimeRangeMinutes`, loads data |
| `refreshHistoricalData()` | 3807–3817 | — | — | Reloads historical data for current range |
| `resetEventDataWindow()` | 3833–3852 | — | — | Clears `eventWindowDataStore` |
| `getHistoricalSelectedSensors()` | 3853–3863 | — | int[] | Returns checked sensor IDs |
| `getHistoricalSelectedEventTypes()` | 3864–3874 | — | string[] | Returns checked event type codes |
| `getEventTypesForSensor()` | 3875–3885 | event, sensorId | string[] | Returns triggered event types for sensor |

### Event Data & Windows (lines 3850–4550)

| Function | Lines | Inputs | Outputs | Side Effects |
|----------|-------|--------|---------|--------------|
| `buildEventListRows()` | 3886–3939 | events, selectedSensors, selectedEventTypes | {timestamp, sensorId, ...}[] | Filters/formats event rows |
| `populateEventList()` | 3940–3948 | rows | — | DOM: populates `#event-list-tbody` |
| `renderEventDataPage()` | 3949–4008 | — | — | DOM: renders current page of event list |
| `showEventListView()` | 4009–4015 | — | — | Shows event list, hides detail window |
| `formatEventWindowTime()` | 4016–4025 | tsSeconds | string | Formats timestamp as HH:MM:SS.fff |
| `scrollToEventCenterRow()` | 4026–4036 | — | — | Scrolls event window to center row |
| `loadEventDataWindow()` | 4037–4234 | eventId, sensorId, fallbackInfo | — | Loads ±9s window, shows ECharts modal |
| `showEventWindowGraph()` | 4237–4550 | — | — | Initializes ECharts for event window detail |
| `closeEventWindowGraph()` | 4552–4556 | — | — | Hides modal, disposes ECharts |
| `ewgToggleSeries()` | 4558–4576 | name | — | Toggles series visibility in event window graph |
| `ewgZoomReset()` | 4578–4581 | — | — | Resets zoom to full window |
| `ewgSaveImage()` | 4583–4590 | — | — | Downloads ECharts as PNG |
| `changeEventDataPage()` | 4593–4604 | tableType, direction | — | Moves pagination pointer, re-renders |
| `exportEventDataToCSV()` | 4607–4703 | tableType | — | Downloads CSV of visible data (live or historical) |
| `exportEventWindowCSV()` | 4706–4884 | — | — | Downloads CSV of ±9s window with event config |

### Historical Data Loading (lines 4887–5309)

| Function | Lines | Inputs | Outputs | Side Effects |
|----------|-------|--------|---------|--------------|
| `loadCustomTimeRange()` | 4887–4933 | — | — | Validates custom date inputs, calls `loadHistoricalDataCustom()` |
| `loadHistoricalDataCustom()` | 4936–5118 | startTime, endTime | — | Fetches events in time range, renders ECharts scatter |
| `loadHistoricalData()` | 5121–5309 | minutes | — | Fetches events for last N minutes, renders scatter plot |
| `initEventChart()` | 5312–5454 | — | — | Initializes Chart.js for RX rate graph (disabled) |
| `showEventDetails()` | 5457–5727 | eventData | — | Shows popup with frame data table |
| `closeEventPopup()` | 5730–5735 | — | — | Closes event popup |
| `calculateMovingAverage()` | 5737–5741 | data, window | number | Computes moving average of last N points |
| `updateEventChart()` | 5744–5819 | — | — | Updates Chart.js RX rate graph (every 1s) |
| `startEventChartUpdates()` | 5822–5826 | — | — | Starts interval for `updateEventChart()` |
| `stopEventChartUpdates()` | 5829–5835 | — | — | Stops interval |
| `initRealtimeChart()` | 5838–5950 | — | — | Initializes Chart.js TX/RX graph |
| `updateRealtimeChart()` | 5953–5986 | — | — | Updates TX/RX graph (every 1s) |
| `startChartUpdates()` | 5989–5993 | — | — | Starts interval for `updateRealtimeChart()` |
| `stopChartUpdates()` | 5996–6002 | — | — | Stops interval |

### Live Streaming & ECharts (lines 6004–7100)

| Function | Lines | Inputs | Outputs | Side Effects |
|----------|-------|--------|---------|--------------|
| `getLiveFetchSampleCount()` | 6018–6021 | — | int | Calculates samples needed for current window |
| `getExpectedPollIntervalMs()` | 6023–6035 | — | number | Returns expected poll interval (device-specific or 10ms default) |
| `getCurrentModbusScaling()` | 6037–6045 | — | number | Returns Modbus scaling factor (default 100) |
| `percentile()` | 6047–6057 | values, q | number | Computes percentile (0-1) |
| `isPotentialBinaryPulse()` | 6059–6091 | values | bool | Detects if signal is likely binary (PWM/pulse) |
| `cleanupBinaryPulse()` | 6093–6146 | values | number[] | Auto-binarizes signal to two discrete levels |
| `decimateSensorData()` | 6169–6199 | timestamps, values, factor | {timestamps, values} | Min-max decimation preserving peaks |
| `loadFullBufferCache()` | 6202–6237 | targetSeconds? | — | Loads ~28s of data for zoom operations |
| `updateChartFromCache()` | 6240–6272 | xMin, xMax | — | Updates ECharts from cached buffer |
| `updateTimeRangeIndicator()` | 6275–6288 | xMin, xMax | — | Updates zoom level text display |
| `initEChartsGraph()` | 7322–7421 | — | — | Initializes ECharts instance with config |
| `loadDeviceConfigAndPopulateFilters()` | 6325–6336 | — | — | Creates sensor checkboxes |
| `toggleSensorDropdown()` | 6338–6354 | event | — | Shows/hides sensor filter dropdown |
| `createDropdownCheckbox()` | 6369–6456 | id, label, color, checked | HTMLElement | Creates checkbox with styling and listeners |
| `updateDropdownLabels()` | 6458–6477 | — | — | Updates sensor dropdown label text |
| `getLiveSelectedSensors()` | 6479–6488 | — | int[] | Returns checked sensor IDs (1-12) |
| `selectAllSensors()` | 6490–6503 | — | — | Checks all sensors, re-renders |
| `clearAllSensors()` | 6505–6518 | — | — | Unchecks all, re-renders |
| `_ingestSSEBatch()` | 6531–6642 | data | — | Decodes SSE batch, detects STM32 restarts, appends to echartsBuffer |
| `_rafRender()` | 6645–6775 | — | — | Renders ECharts at 60 FPS with auto-analog/digital detection |
| `initSensorModes()` | 6778–6813 | — | — | Creates 12 sensor mode badges |
| `updateSensorModes()` | 6815–6871 | modes | — | Updates badge colors/text (POWER_ON/STARTUP/BREAK) |
| `updateModeStats()` | 6873–6881 | stats | — | Updates mode count displays |
| `startEventGraphUpdates()` | 6883–6939 | — | — | Opens EventSource, ingests SSE batches |
| `stopEventGraphUpdates()` | 6941–6951 | — | — | Closes EventSource |
| `applyLiveGraphSettings()` | 6953–6955 | — | — | (empty, legacy) |
| `formatWindowTime()` | 6960–6965 | ms | string | Formats ms as "Xs" or "Xmin" |
| `setLiveWindow()` | 6967–7005 | ms | — | Sets `liveWindowMs`, updates UI, force-render if paused |
| `initMouseWheelZoom()` | 7017–7097 | — | — | Attaches wheel listener for zoom with pause-aware centering |
| `_nearestSelectValue()` | 7099–7114 | selectEl, targetValue | int | Finds nearest option in select |
| `updateLiveSensorDataTables()` | 7116–7319 | — | — | Fetches live data, populates decimal table, computes rolling stats |

### Charts & Frames (lines 7320–7643)

| Function | Lines | Inputs | Outputs | Side Effects |
|----------|-------|--------|---------|--------------|
| `updateFrames()` | 7490–7578 | — | — | Fetches TX/RX frames, updates grid tables |
| `exportToExcel()` | 7583–7642 | type | — | Downloads TX or RX frames as CSV |

### Window & Lifecycle Events (lines 7424–7487)

- **DOMContentLoaded** (7424–7465): Initializes charts, starts polling
- **resize** (7468–7475): Debounced ECharts resize
- **beforeunload** (7478–7487): Cleanup on page exit

---

## Summary Statistics

- **Total lines:** 7645
- **Stylesheet:** 1–1253 (CSS, ~200 rules)
- **HTML structure:** 1256–1500 (static DOM, modals)
- **JavaScript:** 2111–7643 (core logic)
  - Device management: ~900 lines
  - UI modes & views: ~500 lines
  - Event data & windows: ~700 lines
  - SSE streaming & ECharts: ~1100 lines
  - Chart.js & utility: ~400 lines
- **Fetch endpoints:** 15 API routes (list above)
- **Global state variables:** 50+ (device, buffers, charts, modes, filters, pagination)
- **Event listeners:** 20+ (click, change, wheel, resize, unload)
- **Modals:** 5 (rename, confirm, add device, delete device, event window)

