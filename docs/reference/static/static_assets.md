# Static Assets Reference

Covers everything under `/home/embed/hammer/static/`: JS libraries, test/debug HTML pages, stylesheet, and branding image. The SvelteKit rewrite will not reuse these files verbatim, but behaviour encoded here must be preserved.

## File inventory

| File | Size | Type | Status |
|------|------|------|--------|
| `adaptive_sensor_chart.html` | 27 269 B | Test/Debug HTML | Reference / teaching page |
| `app.js` | 25 874 B | Core JS | **Production** |
| `mqtt_realtime_chart.html` | 28 656 B | Test/Debug HTML | Prototype |
| `uplot_event_graph.js` | 20 355 B | Chart library | **Production** |
| `adaptive_chart_engine.js` | 7 741 B | Chart library | Prototype (Chart.js wrapper) |
| `pattern_visualizer.js` | 10 273 B | Visualisation | Prototype (pattern badges) |
| `style.css` | 6 821 B | Stylesheet | **Production** |
| `mqtt_test.html` | 4 120 B | Test/Debug HTML | Diagnostic page |
| `mqtt_event_graph.js` | 191 B | Stub | **Dead code** |
| `embeds-squre.png` | 34 586 B | Image | Logo |

---

## 1. app.js (25 874 B) — production core

**Purpose**
Main dashboard controller. Device lifecycle, frame capture, statistics polling, real-time chart updates. Supports three dashboard variants (Dashboard 1, 2, 3) selected via `localStorage`.

**Global state**
- `isConnected` — device connection boolean
- `statsUpdateInterval` — 500 ms poller
- `framesUpdateInterval` — 1 s frame poller
- `lastStatsSample` `{time, total_tx, total_rx}` for FPS
- `API_BASE` string (default empty)

**Function index**

| Line | Function | Purpose |
|------|----------|---------|
| 11–23 | `log(msg, type)` | Append to `#log-output` or console |
| 26–28 | `formatNumber(n)` | Locale thousands |
| 30–36 | `formatFps(v)` | FPS sanitize |
| 39–48 | `formatUptime(s)` | H:MM:SS |
| 51–55 | `formatBytes(b)` | B/KB/MB |
| 58–89 | `openDevice()` async | `POST /api/device/open`; enable buttons, save `localStorage` |
| 92–129 | `closeDevice()` async | `POST /api/device/close`; clear intervals, disable buttons |
| 132–148 | `getDeviceInfo()` async | `GET /api/device/info`; populate info cards |
| 151–201 | `initChannels()` async | `POST /api/device/init {baud_rate, mode}`; start polling |
| 204–281 | `startContinuousStream()` async | `POST /api/stream/start {frame_rate, counter_mode}` |
| 284–321 | `stopContinuousStream()` async | `POST /api/stream/stop` |
| 324–369 | `updateStats()` async | `GET /api/stats`; update stat cards |
| 372–442 | `updateFrames()` async | `GET /api/frames/grids`; update `#tx-grid` / `#rx-grid` |
| 445–464 | `highlightMatchingFrame(counter, payload, grid)` | Cross-highlight TX↔RX rows |
| 467–471 | `startStatsUpdate()` | `setInterval(updateStats, 500)` |
| 474–477 | `startFramesUpdate()` | `setInterval(updateFrames, 1000)` |
| 480–490 | `updateConnectionStatus(connected)` | Set `#connection-status` badge (guarded) |
| 493–497 | `updateTestStatus(status)` | Set `#test-status` badge (**no guard**) |
| 500–503 | `clearLog()` | Empty `#log-output` |
| 510–542 | `initChannel(ch)` async | `POST /api/channel/init` (Dashboard 2) |
| 545–583 | `startChannel(ch)` async | `POST /api/channel/start` |
| 586–620 | `stopChannel(ch)` async | `POST /api/channel/stop` |
| 623–642 | `clearChannel(ch)` async | `POST /api/channel/clear` |
| 645–652 | `updateChannelStatus(ch, text, color)` | Channel badge |
| 655–669 | `enableChannelControls(enabled)` | Disable/enable ch0/ch1 controls |
| 672–688 | DOMContentLoaded listener | Init event listeners |

**Null-check defects**
Dashboard 3 omits elements that Dashboards 1/2 have. The following call sites are **unguarded** and will throw on Dashboard 3:

- `updateTestStatus()` (493–497): touches `#test-status` without `if (el)`.
- `updateStats()` (347–350, 358–363): `#stat-total-tx`, `#stat-total-rx`, `#stat-tx-fps`, `#stat-rx-fps`, and all `#counter-*` nodes accessed without null checks.
- `updateFrames()` (378–408): `#tx-grid` and `#rx-grid` assumed.

Guarded: `updateConnectionStatus()` (482), counter-mode reads (216–218, 243–246), channel-controls iteration (657–668).

**Endpoints called**
`POST /api/device/open`, `POST /api/device/close`, `GET /api/device/info`, `POST /api/device/init`, `POST /api/stream/start`, `POST /api/stream/stop`, `GET /api/stats`, `GET /api/frames/grids`, `POST /api/channel/{init,start,stop,clear}`.

**DOM requirements**
Header: `#connection-status`, `#test-status`.
Device info: `#device-hardware`, `#device-serial`, `#device-channels`, `#device-hw-version`, `#device-fw-version`, `#device-dr-version`.
Controls: `#baud-rate`, `#mode`, `#btn-open`, `#btn-close`, `#btn-init`, `#btn-stream-start`, `#btn-stream-stop`, `#stream-rate-preset`, `#stream-rate-custom`, `#counter-mode` (optional).
Stats: `#stat-total-tx`, `#stat-total-rx`, `#stat-tx-fps`, `#stat-rx-fps`, `#stat-uptime` (optional).
Counter: `#counter-tx`, `#counter-rx-expected`, `#counter-rx-last`, `#counter-lost`, `#counter-duplicate`, `#counter-outoforder` (optional).
Frames: `#tx-grid`, `#rx-grid`.
Channels: `#ch0-*`, `#ch1-*` (optional, Dashboard 2 only).

**localStorage keys**
- `devicesConnected` (bool-ish)
- `forceSync` (timestamp)
- `channelsInitialized` (bool-ish)
- `deviceConfig` JSON: `{baud_rate, mode, frame_rate, auto_init, streaming_active, counter_mode, channel_status}`

---

## 2. adaptive_chart_engine.js (7 741 B) — Chart.js wrapper

**Purpose**
Thin wrapper over Chart.js 4 for real-time streaming. Point-to-point only (no interpolation).

**Class `AdaptiveChartEngine`**
```js
new AdaptiveChartEngine(canvasId, {
  maxPoints: 10000, darkMode: true, enableZoom: true,
  label, showLegend, xAxisLabel, yAxisLabel
})
```

**Public methods**

| Line | Method | Purpose |
|------|--------|---------|
| 173–176 | `updateData(arr)` | Replace dataset |
| 182–193 | `appendRealtime(pt \| arr)` | Append + trim to `maxPoints` |
| 198–201 | `clear()` | Empty data |
| 206–208 | `getDataCount()` | Count |
| 213–217 | `resetZoom()` | If zoom plugin present |
| 222–227 | `destroy()` | Chart.js destroy |

**Config**
Line chart, `tension=0` (linear). `chartjs-plugin-zoom` enabled. Dark palette: `#0f172a` / `#10b981`.

**Dependencies**
Chart.js 4.4.0+, chartjs-adapter-date-fns 3.0.0+, chartjs-plugin-zoom 2.0.1+ (CDN).

**Status**
Prototype. uPlot supersedes for production dashboards.

---

## 3. uplot_event_graph.js (20 355 B) — production chart engine

**Purpose**
uPlot-based 12-sensor line chart. Uniform-grid + zero-order-hold resampling. ~25 KB vs Chart.js ~42 KB; renders much faster at 123 Hz.

**Class `UPlotEventGraph`**
```js
new UPlotEventGraph(containerId, sensorCount = 12)
```

**Key methods**

| Line | Method | Purpose |
|------|--------|---------|
| 45–94 | `init()` | Build uPlot with 12 sensors, empty data |
| 255–303 | `updateData(timestamps, sensors, expectedStepMs)` | Resample + push |
| 591–597 | `resize()` | Responsive |
| 602–607 | `destroy()` | Cleanup |

**Resampling**
- `buildLockedUniformTimeline()` (331–358): converts irregular timestamps to locked uniform grid; maintains phase across updates to prevent visual jitter.
- `resampleHold()` (397–418): zero-order hold — holds last value until next sample. No interpolation (exact behaviour preserved).
- Phase tracking: `gridStepMs`, `gridPhaseMs`; small corrections at 0.1× delta per tick prevent drift.

**Square-wave cleanup (optional)**
- `setSquareCleanup(on)` (420–422)
- `getSeriesForRender()` (424–429)
- `isSquareLike()` (456–469)
- `cleanupBinaryPulse()` (503–556): two-level threshold mapping for stable binary PWM-like signals.

**Colours (12 sensors)**
1. `#FF6384` pink · 2. `#36A2EB` blue · 3. `#FFCE56` yellow · 4. `#4BC0C0` teal · 5. `#9966FF` purple · 6. `#FF9F40` orange · 7. `#FF6384` pink (repeat) · 8. `#C9CBCF` grey · 9. `#4BC0C0` teal (repeat) · 10. `#00FF80` spring green · 11. `#0080FF` azure · 12. `#FF8080` light red.

**Dependencies**
uPlot library (external — NOT bundled in `static/`). Assumes `window.uPlot`.

**Null-check state**
Init, updateData, and applySeriesPathMode all guard with early return — defensively correct.

---

## 4. pattern_visualizer.js (10 273 B) — advanced visualisation

**Purpose**
Pattern-detection badges over sensor data. Identifies waveform shape (constant / square / sine / smooth / random / trend / step / spikes / saturation / dropouts / unknown) and renders coloured indicators.

**Class `PatternVisualizer(containerId)`**

**State**
- `patternHistory[sensorKey]` — last 100 changes
- `currentPatterns[sensorKey]` — current `{pattern, confidence, metadata}`
- `patternColors`, `patternIcons` — 12 entries each

**Public methods**

| Line | Method | Purpose |
|------|--------|---------|
| 45–73 | `updatePattern(sensorId, type, confidence, metadata)` | Record + badge update |
| 171–195 | `getPatternStats(sensorId)` | `{totalSamples, patternCounts, mostCommonPattern, currentPattern}` |
| 200–249 | `createPatternLegend()` | Colour legend DOM |
| 254–301 | `renderPatternTimeline(sensorId, canvasId)` | Timeline on canvas |

**DOM updates**
- `_updatePatternBadge()` (78–116): inserts `<span class="pattern-badge-container">` adjacent to `#sensor-{id}`. Guarded.
- `_updateTooltip()` (121–166): inserts tooltip div. Guarded.
- `renderPatternTimeline()` (260–261): canvas null-guard present.

**Status**
Prototype. Not wired into active dashboards.

---

## 5. mqtt_event_graph.js (191 B) — stub / dead code

Entire contents:
```js
/**
 * mqtt_event_graph.js - Browser-side MQTT WebSocket removed.
 * Server ingests MQTT data via paho-mqtt (stm32/adc topic).
 * ECharts graph polls /api/live_sensor_data REST endpoint.
 */
```

**Status**
Dead. Delete during rewrite.

---

## 6. style.css (6 821 B) — production stylesheet

**Structure (by line range)**
- 1–5 reset
- 7–12 body (gradient `linear-gradient(135deg, #667eea, #764ba2)`, 20 px padding)
- 14–17 `.container` (48 px margin)
- 19–28 header (white card, 10 px radius, shadow)
- 40–55 `.status-badge` (8×16 px padding, `.disconnected #ff6b6b`, `.connected #51cf66` with pulse)
- 57–63 `.card`
- 124–174 buttons (`.btn-primary #667eea`, `.btn-success #51cf66`, `.btn-danger #ff6b6b`)
- 176–221 stats cards + highlight/success gradients
- 223–283 frame monitor table (max-height 400 px, `.frame-tx #fff3cd`, `.frame-rx #d1ecf1`, `.highlighted #28a745 !important`)
- 286–318 log container (dark `#1e1e1e`, `.log-entry.success #4ec9b0`, `.error #f48771`, `.info #569cd6`)
- 321–336 `::-webkit-scrollbar`
- 339–353 `@keyframes pulse` + `.status-badge.connected` animation
- 356–438 responsive breakpoints (`768px`, `1600px`, `1920px`, `2560px`)

**`!important` overrides**
Only on `.frame-table .highlighted` (lines 262–263) — `background` and `color`.

**Z-index**
Only one non-trivial value: `.pattern-tooltip` uses `z-index: 1000` (declared in `pattern_visualizer.js`, not style.css).

**Palette summary**
- Primary gradient `#667eea → #764ba2`
- Success `#51cf66` / `#40c057` / `#37b24d`
- Error `#ff6b6b` / `#fa5252`
- Dark bg `#1e1e1e`, light text `#d4d4d4`
- Frame TX `#fff3cd`, RX `#d1ecf1`, highlight `#28a745`

---

## 7. Test / debug HTML pages

### 7.1 adaptive_sensor_chart.html (27 269 B)

**Purpose**
Interactive Chart.js pattern-detection demo. Generators: square wave, ECG heartbeat, sine curve, high-freq oscillation, random noise, live stream.

**URL**
Served statically at `/static/adaptive_sensor_chart.html`.

**Pattern detection (`analyzeData`, 360–462)**
- Variance, stddev
- Zero-crossing rate
- Derivative / slope variance
- Plateau detection
- Peak detection
- Coefficient of variation

**Classification thresholds (414–444)**

| Pattern | Predicate | Confidence |
|---------|-----------|------------|
| Square | `uniqueness < 0.3 ∧ plateauRatio > 0.5` | 0.60–0.95 |
| ECG | `peakFreq 0.02–0.15 ∧ crossingRate > 0.15` | 0.70–0.95 |
| Smooth | `derivVariance < 0.3 × variance ∧ uniqueness > 0.5` | 0.65–0.90 |
| Oscillating | `crossingRate > 0.4 ∧ cv > 0.3` | 0.60–0.92 |
| Noise | `variance > 1.0 ∧ uniqueness > 0.8` | 0.55–0.85 |

**Per-pattern style (`applyChartConfig`, 478–569)**
- Square → stepped, orange, 3 px
- ECG → red with peak markers
- Smooth → green, smooth curves, 2.5 px
- Oscillating → purple with point markers
- Noise → grey scatter-style

**Demo generators (611–668)**
`loadSquareWave / loadECGSignal / loadSmoothCurve / loadOscillating / loadRandomNoise / toggleStream`.

**Status**
Reference / teaching page. Classification thresholds are valuable to port.

### 7.2 mqtt_realtime_chart.html (28 656 B)

**Purpose**
ECharts + MQTT.js demo: browser subscribes to broker, auto-detects chart type per topic, renders.

**URL**
`/static/mqtt_realtime_chart.html`.

**Dependencies**
ECharts 5.5.0, MQTT.js 5.3.5.

**Features**
- `connectMQTT()` (677–733): broker `ws://broker.hivemq.com:8000/mqtt`, topic `sensors/data/#`, auto-reconnect 5 s / 30 s timeout.
- `processMessage()` (549–608): 4 payload shapes — `{data:[[x,y]…]}` / `{values:[…], timestamp}` / `{timestamp, value}` / bare number.
- `detectChartType()` (478–511): square (`uniqueY ≤ 2, ratio < 0.1`), line (`ratio > 0.8 ∧ sorted`), bar (unsorted x).
- `getOrCreateSeries()` (516–536): max 1000 pts/series, 10-colour palette.
- `scheduleChartUpdate()` (613–620): throttled 50 ms.

**Null-check defects**
- `updateSeriesInfo()` (789–804): `document.getElementById('series-info-container').innerHTML = ''` — no guard.
- `updateConnectionStatus()` (747–755): `statusCard`, `statusText`, `.status-indicator` all accessed unguarded — will crash if absent.

**Status**
Prototype / reference. Production uses uPlot + server-side MQTT.

### 7.3 mqtt_test.html (4 120 B)

**Purpose**
Diagnostic: fetch MQTT config from `http://192.168.1.115:8080/api/mqtt/config`, connect to WebSocket broker, subscribe `{baseTopic}/#`, log all messages.

**Expected payload**
```json
{"sensor_id": "sensor1", "value": 42.5, "timestamp": 1234567890, "can_id": "0x123"}
```

**Log palette**
success `#0f0` / error `#f00` / info `#ff0` / data `#0ff`.

**Status**
Dev-only diagnostic; not user-facing.

---

## 8. embeds-squre.png (34 586 B)

Logo. No analysis required.

---

## Dead-code & migration summary

| File | Status | Action in rewrite |
|------|--------|-------------------|
| `app.js` | Production | **Port** behaviour to Svelte stores + components |
| `uplot_event_graph.js` | Production | **Port** to TypeScript; reuse resample logic exactly |
| `style.css` | Production | **Port** palette + breakpoints (Tailwind or CSS vars) |
| `adaptive_chart_engine.js` | Prototype | **Drop** — uPlot supersedes |
| `pattern_visualizer.js` | Prototype | Review; drop unless pattern badges are a product requirement |
| `mqtt_event_graph.js` | Dead | **Delete** |
| `adaptive_sensor_chart.html` | Reference | Extract classification thresholds to a lib; drop the HTML |
| `mqtt_realtime_chart.html` | Prototype | Drop; server handles MQTT. Keep any useful chart-type heuristics |
| `mqtt_test.html` | Diagnostic | Replace with a backend health-check endpoint |
| `embeds-squre.png` | Asset | Keep |

## Breaking changes to watch in the rewrite
1. **Null-check defects**: many `document.getElementById` reads in `app.js` and `mqtt_realtime_chart.html` are unguarded. SvelteKit bindings with `{#if el}` eliminate this class of bug.
2. **Cross-tab sync via localStorage**: current `deviceConfig / devicesConnected / channelsInitialized` keys support cross-tab state; replace with Svelte store + optional IndexedDB if persistence is needed.
3. **Polling intervals**: replace raw `setInterval` with Svelte lifecycle (`onDestroy` to clear). Prefer SSE/WebSocket where practical.
4. **DOM queries**: `document.getElementById(...)` → Svelte `bind:this`.
5. **Three dashboard variants** (Dashboard 1/2/3): the conditional rendering via `localStorage` must be replaced with explicit routes or feature flags; the current approach is the source of the null-check defects.

## Behaviour to preserve
- **Exact point-to-point rendering** from uPlot (no interpolation).
- **Zero-order hold** resampling when consecutive samples have identical value.
- **Phase-locked uniform grid** — prevents visual jitter at 123 Hz.
- **Binary-pulse cleanup** for stable PWM-like signals.
- **12-sensor colour palette** (users identify sensors by colour).
- **500 ms stats / 1 s frames** polling cadence (current default; can be tuned, but document the change).