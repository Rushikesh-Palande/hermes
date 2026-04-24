# EVENT_DETECTION_CONTRACT.md

## 1. Overview

The HERMES event detection subsystem observes 12 sensors per device, detects four distinct "event types" (A, B, C, D) and one "mode transition" event (BREAK), and schedules each detection for database storage through a two-phase TTL + post-window hold pipeline. A single authoritative class — `EventDetector` at `/home/embed/hammer/src/detection/event_detector.py` — owns all per-device state. The three non-variance types (B, C, D) are implemented as one instance of their detector class per sensor (`AvgTypeB`, `AvgTypeC`, `AvgTypeD` at `/home/embed/hammer/src/events/avg_type_b.py`, `avg_type_c.py`, `avg_type_d.py`). Type A detection is computed inline inside `EventDetector` (the helper `check_event_a` in `/home/embed/hammer/src/events/event_a.py` exists but is NOT on the live path — the live path uses the incremental running-sum calculation in `_calculate_variance_incremental` at `event_detector.py:1667-1756`). The rewrite must not depend on `check_event_a`; production detection is the incremental O(1) variant.

Priority hierarchy (strictly enforced by `_start_ttl_timer` at `event_detector.py:736-819`, priority map at line 764): **D (4) > C (3) > B (2) > A (1)**. Higher priority blocks lower priority from starting a timer, and higher priority preempts (clears) lower-priority timers and pending post-event entries that are already running.

BREAK is **outside this priority system**. Confirmed at `event_detector.py:669-734`: `_check_expired_ttls` drains BREAK events from a separate `pending_break_events` dict, and `_start_ttl_timer` never touches BREAK. BREAK is not merged with A/B/C/D timers; it uses `pending_break_events` and writes `sensor{N}_event_break = 'Yes'`.

Configuration is a two-layer model. Every type has a **global config row** (tables: `event_config_type_a`, `event_config_type_b`, `event_config_type_c`, `event_config_type_d`) and an optional **per-sensor override table** (`event_config_type_a_per_sensor`, `event_config_type_b_per_sensor`, `event_config_type_c_per_sensor`, `event_config_type_d_per_sensor`). For Type A, whether per-sensor is used is gated by `db.get_device_per_sensor_mode(device_id)` at `event_detector.py:1226`. For B/C/D, per-sensor entries always override the global parameters row by row; there is no per-device toggle.

Data flow (one-way): MQTT sample → `add_sensor_data(sensor_values, timestamp)` (line 970) → per-sensor circular buffers + variance state + mode transition check → per-type queues on `worker_manager` → per-type global worker threads → per-sensor detector's `add_sample` → `_handle_type_X_event` callback → `_start_ttl_timer` → (on the next `add_sensor_data` tick) `_check_expired_ttls` promotes timer → pending_post_event → at `trigger_time + POST_WINDOW_SECONDS` → `worker_manager.update_queue` → `batch_update_event_detection` → SQLite `events` table.

## 2. Shared machinery

### 2.1 Circular buffers

Hardware samples at ~123 Hz on STM32 but code declares `SAMPLE_RATE_HZ = 100` (line 41). The constant is used for **deque sizing only**, not for timing; timing uses wall-clock timestamps from the MQTT payload. Two circular buffer families exist:

- `BUFFER_DURATION_SECONDS_A = 60` (line 39): sized for Type A's maximum T1. Per-sensor deque at `sensor_buffers_a[sensor_id]` (line 102-105), `maxlen = self.buffer_size_a = int(buffer_duration_seconds_a * SAMPLE_RATE_HZ)` (line 84). Each entry is a `(timestamp, value)` tuple. A parallel `timestamp_buffer` (line 113) mirrors only for Type A enabled code paths.
- `BUFFER_DURATION_SECONDS_OTHER = 20` (line 40): sized for B/C/D. Per-sensor deque at `sensor_buffers_short[sensor_id]` (line 107-110). These deques are **shared** with `AvgTypeB`/`AvgTypeC`/`AvgTypeD` via the `shared_buffer=` constructor argument (lines 210, 227, 245), so when `event_detector.add_sensor_data` appends, it also populates the detectors' `signal_buffer`. Detectors themselves do not append when `using_shared_buffer=True` (avg_type_b.py:136-138).

Per-sensor Type A variance state (not a raw-value buffer; running sums) at `sensor_variance_state[sensor_id]` (line 257-267) contains `running_sum`, `running_sum_sq`, `window_count`, `initialized`, `last_timestamp`, and a `window_deque(maxlen = variance_window_deque_secs * SAMPLE_RATE_HZ)` (default 78s). This deque is the time-windowed collection that feeds the running sums.

Each of B, C, D also keeps an internal `window_deque` inside its detector instance (avg_type_b.py:105, avg_type_c.py:102, avg_type_d.py:79) — these are the INCREMENTAL sliding windows for T2/T3/T4, with `maxlen = int(T × sample_rate × 1.2)`. Type B additionally multiplies by a configurable `window_headroom` (default 1.2, line 49).

All buffer sizes can be overridden via `app_config` (read through `_cfg` at lines 66-73), keys: `sample_rate_hz`, `buffer_duration_a_s`, `buffer_duration_bcd_s`, `variance_window_deque_secs`, `data_gap_reset_s`, `type_b_init_fill_ratio`, `type_b_window_headroom`, `type_c_init_fill_ratio`, `type_d_init_fill_ratio`, `type_d_1sec_buffer_slots`, `event_pre_window_s`, `event_post_window_s`, `queue_maxlen_a`, `queue_snapshot_maxlen`, `worker_batch_flush_interval_s`.

### 2.2 Locks

Four separate locks exist on `EventDetector`:

- `buffer_lock` (line 137): Guards `sensor_buffers_a`, `sensor_buffers_short`, `timestamp_buffer`, `last_sensor_snapshot`, `_pending_break_queue` drain, and `pending_break_events` registration. Also passed as `shared_lock=` to each B/C/D detector (lines 211, 228, 246), so it also guards every per-sample detector update. This is the highest-traffic lock.
- `variance_lock` (line 279): Guards `sensor_variance_state` updates and reads. Deliberately separate from `buffer_lock` to let `get_current_stats()` run without blocking the SSE stream (comment at line 278).
- `ttl_lock` (line 344): Guards `active_ttl_timers`, `pending_post_event`, `last_event_timestamp`, `last_event_ttl`. `pending_break_events` is modified under `buffer_lock` (during registration from `_pending_break_queue` drain at line 1014-1025) and read without a lock in `_check_expired_ttls` at line 672 — see also `_extract_data_window` which takes `buffer_lock` at line 449.
- Each detector's internal `self.lock` (avg_type_b.py:112, avg_type_c.py:109, avg_type_d.py:119): When `shared_lock` is passed in from `EventDetector`, it becomes the same object as `buffer_lock`. The detector's `add_sample`, `get_statistics`, `get_recent_events`, `reset`, `update_parameters` all take this lock.

Documented lock ordering: `_check_expired_ttls` (line 537-734) explicitly releases `ttl_lock` before calling `_extract_data_window` (which takes `buffer_lock`) to avoid lock-order inversion (comment at line 549-553, 591-592). Similarly `_save_wide_event` is called outside `buffer_lock` after the Type A detection run (comment at line 1563-1566).

### 2.3 Mode switching (POWER_ON / STARTUP / BREAK / "NORMAL")

The code has three modes per sensor, not four: integer codes `0=POWER_ON`, `1=STARTUP`, `2=BREAK` (line 385-387, `mode_names` lookup at 895 and 964). The comment "NORMAL" in the task prompt does not correspond to a code state; "STARTUP" is the running/active state where event detection is permitted. `sensor_active_states[sensor_id]` (line 390) mirrors `sensor_modes[sensor_id] == 1`.

Per-sensor state variables (all index [0..12], index 0 unused, sensors 1..12 used):
- `sensor_modes` — the mode integer
- `sensor_above_start_time` — timestamp of first crossing above `startup_threshold` during the current window (reset on exit or on grace expiry)
- `sensor_below_start_time` — timestamp of first crossing below `break_threshold` while in STARTUP
- `sensor_active_states` — `True` only in STARTUP; gates detection
- `sensor_startup_time` — timestamp when sensor entered STARTUP (used by workers to enforce "must have T seconds of STARTUP data before any event can fire")
- `sensor_above_drop_time` — timestamp of transient drop below `startup_threshold` during the startup countdown; if the dip lasts `startup_reset_grace_s = 1.0` seconds (line 396), the startup timer resets

State machine (per sensor, implemented by `check_mode_transition` at lines 855-955, called from `add_sensor_data` at line 995 for every received sensor value):

```
[POWER_ON (0)]
    ├── raw_value > startup_thr AND sustained startup_dur seconds
    │       (with grace window: transient dips < startup_reset_grace_s do not reset)
    │       ──▶ [STARTUP (1)]    sensor_startup_time = now, sensor_active_states = True
    └── otherwise: stay in POWER_ON

[STARTUP (1)]
    ├── raw_value < break_thr AND sustained break_dur seconds
    │       ──▶ [BREAK (2)]      append (sensor_id, crossing_time) to _pending_break_queue
    │                            sensor_active_states = False, sensor_startup_time = None
    └── otherwise: stay in STARTUP

[BREAK (2)]
    ├── raw_value > startup_thr AND sustained startup_dur seconds
    │       ──▶ [STARTUP (1)]    (no new BREAK event; the previous BREAK is still waiting in
    │                            pending_break_events for its post-window)
    └── otherwise: stay in BREAK
```

When `mode_switching_enabled == False` (line 879), `check_mode_transition` returns `True` immediately with zero state change — all detection types run unconditionally (the sensor is effectively always "STARTUP").

Detection gating by state:
- Type A: `_run_type_a_detection` (line 1517) skips the threshold comparison when `mode_switching_enabled and not sensor_active_states[sensor_id]`. Running sums ARE still updated (line 1513) so that on entering STARTUP the window is already primed.
- Types B/C/D: `worker_manager._process_type_X` skips the detector entirely (`continue` at worker_manager.py:208, 263, 320) when `sensor_active_states[sensor_id]` is `False`. This means B/C/D detectors do NOT see samples during POWER_ON or BREAK — their windows will be re-warmed after STARTUP entry. There is also a post-STARTUP gate that requires `timestamp - sensor_startup_time[sensor_id] >= detector.T2/T3/T4` before any sample is fed (worker_manager.py:212-216, 267-271, 324-328).

Thresholds default globally to `startup_threshold=100.0`, `break_threshold=50.0`, `startup_duration_seconds=0.1`, `break_duration_seconds=2.0` (lines 364-367). Minimum duration is clamped to 0.001 s (line 366-367). Per-sensor overrides come from `database.get_mode_switching_per_sensor_configs()` (line 380), stored in `self.sensor_mode_thresholds[sensor_id]` (line 378-383), and looked up first (lines 886-890).

### 2.4 Data-gap reset

All four detection types look for a wall-clock gap between consecutive samples. The threshold is `data_gap_reset_s` (default 2.0 s, line 87 / 1614) for Type A; inside B/C/D detectors it is a hardcoded constant `2.0` (avg_type_b.py:145, avg_type_c.py:142, avg_type_d.py:156). When triggered the affected state is wiped:

- Type A (`_update_variance_state_unlocked` line 1614, `_calculate_variance_incremental` line 1696): clears `window_deque`, zeroes `running_sum`, `running_sum_sq`, `window_count`, marks `initialized=False`.
- Type B (avg_type_b.py:147): clears `window_deque`, `running_sum=0`, `window_count=0`, `initialized=False`.
- Type C (avg_type_c.py:143): same as B but for its T3 window.
- Type D (avg_type_d.py:157-165): clears `window_deque_t4`, zeros `running_sum_t4`/`window_count_t4`, marks `initialized=False`, clears `avg_t4_buffer`, clears `one_sec_averages`, zeros `t5_running_sum`, `t5_initialized=False`, clears `last_completed_second`.

Circular raw buffers (`sensor_buffers_a`, `sensor_buffers_short`) are NOT cleared on a gap — only the derived running-sum state is. Debounce timers are also NOT cleared by a data-gap reset (see Section 11).

## 3. Type A — Variance (CV%) detection

### 3.1 Spec summary

Type A fires when the **coefficient of variation** over a T1-second sliding window exceeds a threshold:

```
CV%(t) = (sqrt(population_variance) / |mean|) × 100
      where variance = E[X²] - (E[X])²  over samples in (t - T1, t]
```

Fire condition: `CV% > threshold_lower` (the upper threshold is loaded but not checked — `_run_type_a_detection` at line 1541 compares strictly to `sensor_threshold = threshold_lower`).

### 3.2 Incremental sliding window state

`sensor_variance_state[sensor_id]` (line 257-267) holds:

- `running_sum`: Σx over the current window
- `running_sum_sq`: Σx² over the current window
- `window_count`: N (number of samples currently inside the window)
- `initialized`: flips to True when `window_count >= int(T1 × SAMPLE_RATE_HZ × 0.9)` (line 1731). The `0.9` init-fill ratio is hardcoded here, unlike B/C/D which read it from `app_config`.
- `last_timestamp`: most recent sample time, used only for data-gap detection
- `window_deque`: the raw `(ts, value)` pairs currently in the window, sized by `variance_window_deque_secs × SAMPLE_RATE_HZ`

Per-sample update in `_calculate_variance_incremental` (lines 1667-1756):

```
window_start = current_time - T1
while window_deque and window_deque[0].ts < window_start:
    old_ts, old_val = window_deque.popleft()
    if initialized:
        running_sum    -= old_val
        running_sum_sq -= old_val²
        window_count   -= 1
window_deque.append((current_time, new_value))
running_sum    += new_value
running_sum_sq += new_value²
window_count   += 1
if not initialized and window_count >= int(T1 * SAMPLE_RATE_HZ * 0.9):
    initialized = True
if window_count < 2: return None
mean     = running_sum / window_count
variance = max(0.0, running_sum_sq / window_count - mean²)
return (variance, mean)
```

Note: while `initialized == False`, evictions do NOT subtract from the running sums — this is intentional so that the very first full window's sums are correctly populated. Once `initialized` flips True, eviction subtracts from the sums exactly as new samples are added.

### 3.3 Detection loop

Live path: `add_sensor_data` (line 970) pushes `(self, timestamp, sensor_snapshot)` onto `worker_manager.type_a_queue` at line 1066. The global Type A worker thread (`worker_manager._type_a_worker` lines 151-171) dequeues and calls `detector.process_type_a_sample(timestamp, sensor_snapshot)` (event_detector.py:1434-1438), which runs `_run_type_a_detection(current_time, sensor_snapshot)` (lines 1440-1597). Type A runs once per sample batch, not on a fixed interval.

Inside the function, under `buffer_lock`: for each sensor_id 1..12, fetch `new_value = sensor_snapshot[sensor_id - 1]`, look up per-sensor `(timeframe, sensor_threshold)` if `type_a_use_per_sensor`, else use the global values, call `_calculate_variance_incremental`. The call is unconditional so the window stays primed during POWER_ON/BREAK (comment lines 1509-1512). After the call, if the sensor is not in STARTUP mode (when `mode_switching_enabled`), skip the threshold comparison. Otherwise compute `variance_pct = (sqrt(variance) / max(|mean|, 1e-9)) × 100` (lines 1526-1527) and compare strictly greater-than `sensor_threshold` (line 1541).

### 3.4 Debounce

`type_a_debounce_seconds` is a single global value (line 194), read from `event_config_type_a.debounce_seconds` by `_load_type_a_config` (line 1252). Per-sensor debounce is stored in `_a_debounce_start: Dict[sensor_id, timestamp]` (line 195).

Debounce rule (`_run_type_a_detection` lines 1542-1553):
1. `variance_pct > threshold` and `_a_debounce_start[sensor_id]` is None → set `_a_debounce_start[sensor_id] = current_time`, do NOT trigger yet.
2. `variance_pct > threshold` and `current_time - _a_debounce_start[sensor_id] >= debounce_s` → trigger; the original crossing timestamp is preserved.
3. `variance_pct <= threshold` → set `_a_debounce_start[sensor_id] = None` (silent reset).

Aggregated fire timestamp (lines 1582-1591): If any sensor fires this tick, `save_timestamp = min(_a_debounce_start[sid] or event_timestamp for sid in triggered_sensors)` — that is, the **earliest** crossing across triggered sensors. After firing, `_a_debounce_start[sid]` is reset to `None` for each fired sensor. This is a per-tick aggregation; sensors that did not fire keep their crossing timer running.

### 3.5 Fire → save flow

For each triggered sensor, `_save_wide_event` (line 1789-1835) builds `event_data = {'timestamp': event_timestamp, 'window_avg': average, 'variance': variance, 'trigger_value': sensor_snapshot[sensor_id-1] or average, 'sensor_snapshot': sensor_snapshot}` and calls `_start_ttl_timer(sensor_id, 'A', event_timestamp, event_data)` (line 1826-1831). `save_timestamp` (the aggregated crossing time) IS the `event_timestamp`/`trigger_time` handed to the TTL system and ultimately stored as the row `timestamp`.

### 3.6 Config parameters (Type A)

Global row columns (from `event_config_type_a`): `timeframe_seconds` (T1), `threshold_lower`, `threshold_upper` (loaded but unused in detection), `enabled`, `ttl_seconds`, `debounce_seconds`. Defaults: TTL=5.0 (line 314), debounce=0.0.

Per-sensor row columns (from `event_config_type_a_per_sensor`): `timeframe_seconds`, `threshold_lower`, `threshold_upper`, `enabled`, `ttl_seconds`.

Load path: `_load_type_a_config` (lines 1223-1255):
1. `use_per_sensor = db.get_device_per_sensor_mode(device_id)` — a per-device flag separate from per-sensor-row enabled flags.
2. If `use_per_sensor`: keep using the global row's `timeframe_seconds`, threshold, etc., but also load `type_a_per_sensor_configs` so that per-sensor rows override both `timeframe_seconds` and `threshold_lower` inside `_run_type_a_detection` (lines 1495-1503).
3. If not `use_per_sensor`: global thresholds and timeframe for all 12 sensors.
4. `type_a_enabled = bool(global_config['enabled'])` — the GLOBAL enabled flag is the master switch. The per-sensor `enabled` column is loaded into `type_a_per_sensor_configs` but is NOT consulted anywhere in `_run_type_a_detection` (lines 1486-1553). This is the behavior described in Section 10.
5. `_a_debounce_start = {}` is cleared.
6. `_update_type_a_buffer_size` (line 1255 → 1257-1288) may resize `sensor_buffers_a` and `timestamp_buffer` based on `max(per-sensor T1 if use_per_sensor else global T1) + 20s` (line 1277).

## 4. Type B — Post-window deviation

### 4.1 Spec summary

Type B fires when the **latest sample** falls outside a tolerance band centered on the T2-second rolling average:

```
avg_T2(t) = mean of samples in (t - T2, t]
lower = avg_T2(t) - (REF_VALUE × lower_threshold%) / 100      # REF_VALUE = 100.0
upper = avg_T2(t) + (REF_VALUE × upper_threshold%) / 100
fire when latest sample < lower OR latest sample > upper
```

`REF_VALUE` is the constant `100.0` from `src/events/constants.py:6`. Because it is 100, `REF_VALUE × pct / 100 == pct`, but the code always writes it out explicitly as `(REF_VALUE * lower_threshold / 100.0)` (avg_type_b.py:192-193). Event code: `-2`.

### 4.2 AvgTypeB detector

Per-sensor instance stored at `event_detector.type_b_detectors[sensor_id]` (line 198). Constructor (avg_type_b.py:37-113) accepts `shared_buffer` (the `sensor_buffers_short[sensor_id]` deque) and `shared_lock` (`buffer_lock`), so the detector re-uses the same storage.

`add_sample(timestamp, value)` (lines 115-258):
1. Acquire `self.lock` (= `buffer_lock`).
2. If `not using_shared_buffer`, append `(timestamp, value)` to `signal_buffer`. In live operation `using_shared_buffer` is True so the detector does not append (EventDetector already did so at line 1001).
3. Data-gap reset: if `timestamp - last_timestamp > 2.0`, clear window state.
4. Slide window: pop front while front `ts < timestamp - T2`, subtracting from `running_sum` and `window_count` only when `initialized`.
5. Append new sample; add to running sum.
6. Initialization: flips True when `window_count >= int(T2 × sample_rate × init_fill_ratio)` (default 0.9). Returns `("OK", None)` until initialized and until `window_count >= 2`.
7. `avg_T2 = running_sum / window_count` → `current_avg = last_window_avg = avg_T2`. This is the value that `_extract_data_window` reads when building the BLOB's rolling averages.
8. Compute band; check if `value < lower_bound OR value > upper_bound`.
9. If out-of-range → apply debounce (next section) → if firing, append to `event_history` with `event_code = -2`, return `("EVENT", -2)`.

### 4.3 Debounce

Same semantics as A. State: `self.debounce_seconds` (0 = disabled) and `self._debounce_start_ts` (avg_type_b.py:108-109).

Rule (avg_type_b.py:211-224): when out-of-range, if `_debounce_start_ts is None` set it to the current `timestamp` and return OK; else if `timestamp - _debounce_start_ts < debounce_seconds` return OK; else fire with `event_ts = _debounce_start_ts` (the original crossing time) and clear `_debounce_start_ts`. When back in-range (line 251) `_debounce_start_ts = None` silently.

### 4.4 Block state

There is NO `block_start_time` in the live continuous code path. Early block-scan versions had one, but the current (`CONTINUOUS`) implementation has no blocking semantics at all. The deque `window_deque` is time-based and always slides with the latest sample. `update_parameters(T2=new_T2)` (lines 348-391) does reset all window state (`window_deque.clear()`, `running_sum = 0.0`, `window_count = 0`, `initialized = False`, `last_timestamp = None`) and resize `window_deque` when T2 changes (lines 384-391). This "reset on T2 change" prevents stale samples with a T2 mismatch from contaminating the fresh window.

### 4.5 Fire path

Two live paths depending on `worker_manager`:

Path A (`worker_manager` present, production): `add_sensor_data` appends to `local_batch_b` (line 1082), flushes to `worker_manager.type_b_queue` on size (`BATCH_SIZE=50`) or time (`BATCH_FLUSH_INTERVAL`, default 0.1s). Global worker `_process_type_b` (worker_manager.py:194-226) iterates all 12 detectors, applies mode/startup-window gates, calls `detector.add_sample(timestamp, val)`, and on "EVENT" calls `callback(sensor_id, event_info, sensor_snapshot)` which is `_handle_type_b_event` (event_detector.py:2412-2442).

Path B (no worker_manager, local fallback): `add_sensor_data` pushes to `type_b_queue` + notify cv (lines 1096-1098). Local `_type_b_loop` (lines 2337-2359) iterates detectors and invokes `_handle_type_b_event` on EVENT.

`_handle_type_b_event` builds `event_data = {'timestamp', 'window_avg', 'trigger_value', 'lower_bound', 'upper_bound', 'sensor_snapshot'}` and calls `_start_ttl_timer(sensor_id, 'B', event_info['timestamp'], event_data)`.

### 4.6 Config

Global row (`event_config_type_b`): `t2_seconds`, `threshold_lower`, `threshold_upper`, `pre_event_seconds`, `post_event_seconds`, `enabled`, `ttl_seconds`, `debounce_seconds`.

Per-sensor row (`event_config_type_b_per_sensor`): `T2`, `lower_threshold`, `upper_threshold`, `enabled`.

Load path: `_load_type_b_config` (lines 1291-1314) reads the global debounce once, then for each sensor_id looks up `device_configs.get(sensor_id)` from `db.get_type_b_per_sensor_configs()`. If present, uses per-sensor T2/lower/upper; else uses global. The global enabled flag sets `type_b_enabled`. The per-sensor enabled flag is loaded but NOT consulted — see Section 10.

## 5. Type C — Range-based on avg_T3

### 5.1 Spec summary

Type C fires when the **T3-second average itself** leaves absolute bounds:

```
avg_T3(t) = mean of samples in (t - T3, t]
fire when avg_T3 < lower_threshold OR avg_T3 > upper_threshold
```

Thresholds are **absolute values** (user-defined, e.g. 40.0 and 60.0), not percentages. Event code: `-3`. Note violation type reported by the detector: `BELOW_LOWER` or `ABOVE_UPPER` (avg_type_c.py:223-228).

### 5.2 AvgTypeC detector

Constructor (avg_type_c.py:36-110) and `add_sample` (112-259) structurally mirror Type B. Sliding T3 window maintained in `window_deque` + `running_sum` + `window_count` + `initialized`. Init-fill ratio (default 0.9) gates the first fire. Event-data capture anchors to `event_ts` which, in the non-debounce case, is `self.window_end_time if self.window_end_time else timestamp` (line 216). In the debounce case, it is `self._debounce_start_ts`.

`current_avg` (avg_type_c.py:184) holds the latest computed `avg_T3`. This field is read by Type D via the `avg_t3_snapshot` map built each tick (see Section 5.5).

CHECK constraint: no strict `threshold_lower < threshold_upper` validation in the detector or EventDetector — if misconfigured the condition `avg_T3 < lower OR avg_T3 > upper` simply always triggers or never triggers. There is also no `<=` vs `<` distinction; both sides use strict `<` and `>` (avg_type_c.py:201).

### 5.3 Debounce

Identical structure to A and B (avg_type_c.py:207-216): first out-of-range sets `_debounce_start_ts`, subsequent out-of-range returns OK until `debounce_seconds` elapses, in-range silently clears. When firing, `event_ts = _debounce_start_ts` (original crossing).

### 5.4 Config

Global row (`event_config_type_c`): `t3_seconds`, `threshold_lower`, `threshold_upper`, `pre_event_seconds`, `post_event_seconds`, `enabled`, `ttl_seconds`, `debounce_seconds`.

Per-sensor row (`event_config_type_c_per_sensor`): `T3`, `lower_threshold`, `upper_threshold`, `enabled`.

Load path: `_load_type_c_config` (lines 1317-1342) mirrors B.

### 5.5 Coupling with Type D

Type D's detection uses `avg_T3` from Type C (NOT its own T4 or T5 average). In the live path, `add_sensor_data` snapshots `avg_t3_snapshot = {sid: self.type_c_detectors[sid].current_avg for sid in ...}` at lines 1126-1131 **while holding `buffer_lock` (which is also C's shared lock)**, so the read is implicitly serialized with C's writes.

However, the Type D worker (`worker_manager._process_type_d` at worker_manager.py:303-339) processes the batch outside that lock; by the time the worker runs, C's `current_avg` may have advanced further. The code deliberately relies on the snapshot taken at enqueue time (line 1127-1130) rather than reading `c_detector.current_avg` live at processing time. In contrast, the local fallback `_type_d_loop` (lines 2385-2410) reads `c_detector.current_avg` at worker-processing time (line 2401) — slightly different semantics.

If Type C is disabled (`type_c_enabled == False`), `type_c_detectors[sensor_id].current_avg` remains at its last-written value (or `None` if C was never initialized for that sensor), because `add_sensor_data` skips C enqueueing when `type_c_enabled` is False (line 1100). Consequently Type D will receive a stale or `None` `avg_t3_from_c`. `AvgTypeD.add_sample` (avg_type_d.py:234) treats `None` as "no trigger possible" and returns `OK` — so a stale None from disabled C suppresses all Type D events. This coupling must be preserved in the rewrite.

## 6. Type D — Two-stage averaging on avg_T5

### 6.1 Spec summary

Type D is the highest-priority detector. It builds two baselines hierarchically:

```
Stage 1: avg_T4(t) = rolling mean of raw samples over last T4 seconds (default 10s).
Stage 2: each completed wall-clock second, take the mean of all avg_T4 samples whose
         timestamps fall in that second (bucket [sec, sec+1)), store in one_sec_averages
         (bounded deque, default maxlen = 1800 slots = 30 min).
Stage 3: avg_T5 = mean of the last int(T5) entries of one_sec_averages (default T5=30s).
Band:    lower = avg_T5 - (REF_VALUE × tolerance%) / 100          # REF_VALUE = 100.0
         upper = avg_T5 + (REF_VALUE × tolerance%) / 100          # symmetric
Test:    fire when avg_T3_from_C < lower OR avg_T3_from_C > upper
```

Event code: `-4`. Violation type: `REJECT_LOW` or `REJECT_HIGH` (avg_type_d.py:263-267).

The tolerance is single-valued: the constructor and `update_parameters` assign `self.upper_threshold = self.lower_threshold` always (avg_type_d.py:62, 440, 443). Even when `_load_type_d_config` reads distinct upper and lower values, the `AvgTypeD.update_parameters` call at line 1363-1370 passes both but lines 438-440 force them equal. The "single symmetric tolerance" invariant is maintained by the detector class.

### 6.2 AvgTypeD detector

Constructor (avg_type_d.py:43-120). Key state:
- `window_deque_t4` — the Stage-1 T4 sliding window (raw samples)
- `running_sum_t4`, `window_count_t4`, `initialized`
- `avg_t4_buffer` — deque of `(timestamp, avg_T4)` pairs within the current/recent second, `maxlen = sample_rate × 2`
- `one_sec_averages` — deque of `(second, one_sec_avg)` pairs, maxlen `one_sec_buffer_slots` (default 1800)
- `t5_running_sum` — running sum of the latest T5 entries of `one_sec_averages`
- `t5_initialized` — flips True once `len(one_sec_averages) >= int(T5)`
- `last_completed_second` — tracker for Stage-2 bucketing

Per-sample advancement (`add_sample` lines 127-293):
1. Acquire lock; detect data gap (>2.0 s) — reset ALL three stages.
2. Stage 1: slide window_deque_t4 by T4 seconds, update `running_sum_t4`/`window_count_t4`. Gate on `initialized` (once `window_count_t4 >= int(T4 × sample_rate × init_fill_ratio)`).
3. If initialized, compute `avg_T4 = running_sum_t4 / window_count_t4`; set `current_avg_t4`; append `(timestamp, avg_T4)` to `avg_t4_buffer`.
4. Stage 2: `current_second = int(timestamp)`. If `last_completed_second is None`, set it to `current_second - 1`. For each `sec` in range `last_completed_second + 1` to `current_second - 1` inclusive: call `_calculate_one_sec_avg(sec)` which scans `avg_t4_buffer` for entries with `sec <= ts < sec+1`, averages them, appends `(sec, avg)` to `one_sec_averages`, and calls `_update_t5_buffer(avg)`. Then set `last_completed_second = current_second - 1`.
5. Stage 3: if `len(one_sec_averages) < int(T5)` return OK. Else `t5_initialized = True`, `avg_T5 = t5_running_sum / min(len(one_sec_averages), int(T5))` (avg_type_d.py:218).
6. Compute band. If `avg_t3_from_c is None` return OK (no C input → no trigger). Else compare; apply debounce; on fire append to event_history with `event_code = -4`.

`_update_t5_buffer` (avg_type_d.py:307-318): after `_calculate_one_sec_avg` has already appended the new entry, if `len(one_sec_averages) > int(T5)`, subtract the entry at index `len - T5 - 1` (the one that just fell out of the T5 window) from `t5_running_sum`; then add the new value. This maintains the running sum of the last T5 entries in O(1).

### 6.3 Warmup

During the first few seconds after startup, the sensor won't fire Type D events because:
1. Stage 1 needs `T4 × sample_rate × 0.9` samples (~9s at defaults).
2. Stage 2 needs at least one completed wall-clock second after Stage 1 initialization before the first entry lands in `one_sec_averages`.
3. Stage 3 needs `int(T5)` completed entries in `one_sec_averages` (~30 seconds at default T5=30).

Total effective warmup: approximately `T4 + T5` seconds after first valid sample (roughly 40 s at defaults). During warmup, `add_sample` returns `("OK", None)` at every return site before the comparison.

### 6.4 Debounce

Same pattern (avg_type_d.py:247-256): suppress on first out-of-range, suppress inside the debounce window, fire with `event_ts = _debounce_start_ts` (the crossing) once elapsed. Non-debounce case uses `event_ts = self.window_end_time or timestamp` (line 256).

### 6.5 Config

Global row (`event_config_type_d`): `t4_seconds`, `t5_seconds`, `threshold_lower`, `threshold_upper`, `pre_event_seconds`, `post_event_seconds`, `enabled`, `ttl_seconds`, `debounce_seconds`.

Per-sensor row (`event_config_type_d_per_sensor`): `T4`, `T5`, `lower_threshold`, `upper_threshold`, `enabled`, plus a `tolerance` field read by `apply_type_d_per_sensor_configs` (line 1972).

Load path: `_load_type_d_config` (lines 1344-1370). Again the per-sensor enabled flag is loaded but not consulted during detection (Section 10).

## 7. BREAK events

### 7.1 Spec

BREAK fires on the STARTUP → BREAK transition inside `check_mode_transition` (lines 904-914). The stored event `timestamp` is `crossing_time = sensor_below_start_time[sensor_id]` — the moment the value first fell below `break_threshold`, NOT the moment the duration elapsed. Sensor must sustain `value < break_thr` for `break_duration_seconds` continuously to fire.

BREAK is outside the A/B/C/D priority system. `_start_ttl_timer` is never called for BREAK. Instead the crossing is pushed onto `_pending_break_queue` (line 907) and later registered into `pending_break_events` (line 1014-1025) when a sensor snapshot is available.

### 7.2 check_mode_transition logic

Recap of state machine (per sensor, lines 855-955):

```
function check_mode_transition(sensor_id, raw_value, timestamp):
    if not mode_switching_enabled: return True
    now = timestamp or time.time()
    (startup_thr, break_thr, startup_dur, break_dur) = per-sensor override or globals
    mode = sensor_modes[sensor_id]

    if mode == STARTUP:
        if raw_value < break_thr:
            if sensor_below_start_time[sid] is None:
                sensor_below_start_time[sid] = now
            elif (now - sensor_below_start_time[sid]) >= break_dur:
                crossing_time = sensor_below_start_time[sid]
                _pending_break_queue.append((sid, crossing_time))
                sensor_modes[sid] = BREAK
                sensor_active_states[sid] = False
                sensor_startup_time[sid] = None
                sensor_below_start_time[sid] = None
                sensor_above_start_time[sid] = None
                return False   # BREAK — events suspended
        else:
            sensor_below_start_time[sid] = None
        return True             # still STARTUP

    else:   # POWER_ON or BREAK
        if raw_value > startup_thr:
            if sensor_above_start_time[sid] is None:
                sensor_above_start_time[sid] = now
                sensor_above_drop_time[sid] = None
            else:
                sensor_above_drop_time[sid] = None
            if (now - sensor_above_start_time[sid]) >= startup_dur:
                sensor_modes[sid] = STARTUP
                sensor_above_start_time[sid] = None
                sensor_above_drop_time[sid] = None
                sensor_below_start_time[sid] = None
                sensor_active_states[sid] = True
                sensor_startup_time[sid] = now
                return True
        else:
            if sensor_above_start_time[sid] is not None:
                if sensor_above_drop_time[sid] is None:
                    sensor_above_drop_time[sid] = now
                elif (now - sensor_above_drop_time[sid]) >= startup_reset_grace_s:
                    sensor_above_start_time[sid] = None
                    sensor_above_drop_time[sid] = None
        return False
```

Key subtleties:
- STARTUP → BREAK always records the crossing as the original `sensor_below_start_time`, not the time of transition.
- While waiting for `startup_dur`, a value-drop enters a grace period tracked by `sensor_above_drop_time`. If the value stays below `startup_thr` for at least `startup_reset_grace_s = 1.0` seconds, the startup timer resets; otherwise a brief dip is forgiven.
- Once in STARTUP, there is no symmetric "break_reset_grace" — any single recovery above `break_thr` resets `sensor_below_start_time` immediately (line 917).

### 7.3 Global vs per-sensor mode-switching config

Global source (`database.get_mode_switching_config`, line 361-368): `enabled`, `startup_threshold`, `break_threshold`, `startup_duration_seconds`, `break_duration_seconds`. Minimum durations clamped to 0.001 s.

Per-sensor source (`database.get_mode_switching_per_sensor_configs()[device_id]`, line 380-383): same four threshold/duration keys. Per-sensor overrides fully replace the global value per-sensor-per-key. There is no per-sensor `enabled` flag for mode switching — it is device-wide.

Live update hooks: `apply_mode_switching_per_sensor_configs(configs)` (line 1372-1379) replaces the whole dict; `delete_mode_switching_per_sensor_config(sensor_id)` (line 1381-1383) pops one entry; `apply_app_config_live` (line 1983-2059) updates the four globals.

### 7.4 Storage

`_pending_break_queue` (line 401): list of `(sensor_id, crossing_time)` staged by `check_mode_transition`. Drained inside `add_sensor_data` (lines 1015-1025) once `sensor_snapshot` is available so the event's `sensor_snapshot` field can be populated:

```
pending_break_events[sensor_id] = {
    'trigger_time': crossing_time,
    'event_data': {
        'timestamp': crossing_time,
        'trigger_value': sensor_snapshot[sensor_id - 1],
        'sensor_snapshot': list(sensor_snapshot),
    },
    'save_at': crossing_time + POST_WINDOW_SECONDS,
}
```

Only ONE pending BREAK per sensor can exist; if a second BREAK registration occurs before the first saves, it overwrites the dict entry (line 1017). In practice this is impossible because once in BREAK mode the sensor must return to STARTUP (via STARTUP logic) before another BREAK can happen.

`_check_expired_ttls` (lines 669-734) drains `pending_break_events` when `timestamp >= entry['save_at']`. Each fires `_extract_data_window(trigger_time, event_type='B')` (the 'B' argument selects T2-based rolling averages in the BLOB — there is no 'BREAK' event-type variant in `_extract_data_window`'s switch at line 425-438), then posts to `worker_manager.update_queue` with `event_flags = {('break',): 'Yes'}`. This writes column `sensor{sensor_id}_event_break = 'Yes'` via `batch_update_event_detection` (mqtt_database.py:3103).

## 8. TTL + two-phase post-window hold

### 8.1 Structure

`active_ttl_timers` (line 340-343): nested dict `{1..12: {'A': None|timer, 'B': None|timer, 'C': None|timer, 'D': None|timer}}`. A `timer` object is `{'trigger_time': float, 'ttl': float, 'event_data': dict}`.

`pending_post_event` (line 349): dict keyed by `(sensor_id, event_type)` → `{'trigger_time': float, 'event_data': dict, 'save_at': trigger_time + POST_WINDOW_SECONDS}`. Bounded at 48 entries (12 × 4).

`last_event_timestamp` (line 355), `last_event_ttl` (line 356): dicts keyed by `(sensor_id, event_type)`. Populated on every successful timer start (lines 818-819). Used for Rule 3b race-guard.

When an event fires:
- `_start_ttl_timer` (lines 736-819) applies the priority rules, then on success sets `active_ttl_timers[sid][type] = {trigger_time, ttl, event_data}` (line 813-817) and updates `last_event_*` (818-819). The `save_timestamp` from `_save_wide_event` (A) or `event_info['timestamp']` from `_handle_type_X_event` (B, C, D) is the `trigger_time`.

Phase 1 (TTL wait): `_check_expired_ttls` (lines 537-667) is called at the tail of every `add_sensor_data` (line 1048). Expiry uses the incoming sample's `timestamp` as the clock — no separate thread or timer. Under `ttl_lock`:
```
for sensor_id in 1..12:
    for event_type in ABCD:
        timer = active_ttl_timers[sensor_id][event_type]
        if timer is None: continue
        elapsed = timestamp - timer['trigger_time']
        if elapsed >= timer['ttl']:
            pending_post_event[(sid, type)] = {
                'trigger_time': timer['trigger_time'],
                'event_data':   timer['event_data'],
                'save_at':      timer['trigger_time'] + POST_WINDOW_SECONDS,
            }
            active_ttl_timers[sensor_id][event_type] = None
```

Phase 2 (post-window wait): same `_check_expired_ttls` pass continues:
```
for key, entry in pending_post_event.items():
    if timestamp >= entry['save_at']:
        ready.append((key[0], key[1], entry['trigger_time'], entry['event_data']))
        del pending_post_event[key]
```

Outside `ttl_lock`, each `ready` entry: call `_extract_data_window(trigger_time, event_type)` to build the ±9s BLOB (see Section 9.2), embed `event_info` + `bounds_X` into the BLOB, and append to `worker_manager.update_queue` with `event_flags = {(event_type.lower(),): 'Yes'}`. The DB batcher then performs the 500 ms dedup merge (Section 8.4).

### 8.2 Rules

Rule 1 (block lower priority): `_start_ttl_timer` checks for active timers or pending entries of any higher-priority type (lines 767-774). If any found → immediate return, no timer started.

Rule 2 (clear lower priority): next, for any lower-priority type with an active timer or pending entry (lines 776-784), set `active_ttl_timers[sid][low_type] = None`, `pending_post_event.pop((sid, low_type), None)`, and also clear `last_event_timestamp[(sid, low_type)]` and `last_event_ttl[(sid, low_type)]`. This allows preempted types to re-trigger after the higher type resolves.

Rule 3 (merge same type): if `active_ttl_timers[sid][event_type]` is already set, return without touching it (lines 788-789). If a matching entry in `pending_post_event` exists, also return (lines 790-791). The existing TTL keeps counting; duplicates are silently absorbed.

Rule 3b (late-arrival race guard): lines 800-805. If `_last_ts` and `_last_ttl` are set for this `(sid, type)` key, and the incoming event's `timestamp < _last_ts + _last_ttl`, the event is inside a previous TTL window that already expired from the main thread's perspective (e.g. `ttl=10s`, `post_window=9s` — both structures cleared in the same `_check_expired_ttls` tick). A late worker processing a timestamp-8 batch would otherwise start a spurious second timer. Rule 3b rejects it. Rule 2 clearing of `last_event_*` ensures preempted lower-priority types can re-trigger correctly after a higher-priority event ends.

Rule 4 (start timer): line 808-819. TTL value is chosen as `per_sensor_ttl[sid][event_type]` if present (Type A only — lines 322-334, 841-850), else global `ttl_config[event_type]` (line 313-318). Timer is written, `last_event_*` updated.

### 8.3 BREAK interaction with TTL

BREAK completely bypasses `_start_ttl_timer` and `active_ttl_timers`. It enters `pending_break_events` directly from `_pending_break_queue` in `add_sensor_data` (line 1014-1025), with no TTL phase — only the post-window wait. The entry is saved when `timestamp >= trigger_time + POST_WINDOW_SECONDS` in `_check_expired_ttls` (lines 669-677). A/B/C/D priority rules do not apply: BREAK saves regardless of any active timer or pending entry for that sensor.

### 8.4 500 ms dedup in `batch_update_event_detection`

In `mqtt_database.batch_update_event_detection` (mqtt_database.py:3060-3145): for each event_update, the UPDATE uses a subquery `WHERE device_id = ? AND ABS(timestamp - ?) < 0.500 ORDER BY ABS(timestamp - ?) LIMIT 1` (lines 3119-3129). If a row exists within 500 ms of the new event's timestamp, the UPDATE modifies that row — merging this event's flags, variance, average, and `data_window` BLOB into the existing row. If `data_window` is already set and the new event provides one, the new BLOB OVERWRITES the prior one (line 3109-3111, unconditional). If no row within 500 ms exists, `_insert_event_fallback` creates a new row. The practical consequence: two events on the same device within 500 ms collapse to one row, and the later event's BLOB wins. This is a database-layer concern; full details in DATABASE_CONTRACT.md.

## 9. Event row assembly (`_save_wide_event` and friends)

### 9.1 Fields populated

`_save_wide_event` (line 1789-1835) does NOT directly write to the DB — it only starts the TTL timer. The actual write path is `_check_expired_ttls` → `worker_manager.update_queue` → `batch_update_event_detection`. The update payload (lines 632-641 for A/B/C/D, lines 708-717 for BREAK) is:

```
{
    'device_id':       int(device_id),
    'timestamp':       trigger_time,
    'sensor_id':       sensor_id,
    'event_flags':     {(event_type.lower(),): 'Yes'},    # or {('break',): 'Yes'}
    'average':         event_data['window_avg'] or event_data['smoothed_avg'],  # Type D uses 'smoothed_avg'
    'sensor_snapshot': event_data['sensor_snapshot'],
    'data_window':     data_window_bytes,
    'publish_immediate': False,
}
```

In `batch_update_event_detection` this maps to columns on the `events` table:
- `event_datetime` — `strftime("%Y-%m-%d %H:%M:%S.fff", datetime.fromtimestamp(timestamp))` (line 3089)
- `device_id` — from payload
- `timestamp` — from payload
- `sensor{N}_variance` — from `variance` (populated only for Type A)
- `sensor{N}_average` — from `average`
- `sensor{N}_event_a / _b / _c / _d / _break` — set to `'Yes'` via `event_flags` (mqtt_database.py:3102-3108)
- `data_window` — the JSON BLOB described in 9.2

Per-sensor `sensor{N}_value` is NOT set here by the event path — it is set when the continuous snapshot writer creates the row with the raw snapshot (see DATABASE_CONTRACT.md). The event path only UPDATEs the detection columns (and optionally `data_window`).

### 9.2 data_window BLOB format

Built by `_extract_data_window(trigger_time, event_type)` (lines 404-535). Output is `json.dumps(data, separators=(',', ':')).encode('utf-8')`. No compression; UTF-8 JSON bytes.

Structure:
```
{
  "window_start": trigger_time - POST_WINDOW_SECONDS,   # e.g. -9s
  "window_end":   trigger_time + POST_WINDOW_SECONDS,   # e.g. +9s
  "event_center": trigger_time,
  "sensor_1":  [{"timestamp": ts, "value": v, "avg": rolling_avg, "cv": cv_pct}, ...],
  "sensor_2":  [...],
  ...
  "sensor_12": [...],
  "event_info": {
    "sensor_id":    sensor_id,
    "event_type":   "A" | "B" | "C" | "D" | "BREAK",
    "sensor_value": trigger_value or snapshot[sensor_id-1],
    "variance":     variance or None,
    "average":      window_avg or smoothed_avg or None,
    "timestamp":    trigger_time,
    "lower_bound":  lower_bound or None,
    "upper_bound":  upper_bound or None
  },
  "bounds_B": {"lower_bound": ..., "upper_bound": ...},    # only for Type B events
  "bounds_C": {...},                                        # only for Type C events
  "bounds_D": {...}                                         # only for Type D events
}
```

The `event_info` block is added in `_check_expired_ttls` after the BLOB is built (lines 602-627). The type-keyed `bounds_X` sub-objects are added so that if the 500 ms dedup collapses two events of different types into one row, each type's bounds survive even when the top-level `event_info` is overwritten by the later event (comment lines 620-622).

Per-sample rolling-average window per sensor is chosen by `event_type` (lines 425-438):
- `A` → uses T1 (per-sensor if `type_a_use_per_sensor`, else global `timeframe_seconds`, default 10.0)
- `B` → uses the live `type_b_detectors[sensor_id].T2`
- `C` → uses `type_c_detectors[sensor_id].T3`
- `D` → uses `type_d_detectors[sensor_id].T5` (the long baseline)

The `cv` field is always computed using T1 (global T1 or per-sensor T1 when `type_a_use_per_sensor`) regardless of event_type (lines 441-447, 479-506) — it's a parallel running-CV calculation so the trigger's CV% can be read back.

Sample alignment: the buffer's timestamps jitter around 123 Hz (actual hardware rate), not 100 Hz. `n = min(len(ref), min(len(sensor_snaps[s]) for s in 1..12))` (line 456) takes the common length and iterates i in [0, n); only samples with `window_start <= ts <= window_end` enter the output list (lines 512-515). Expected rows per sensor at ±9s × 123 Hz ≈ 2215 samples. Four fields per sample, 12 sensors → ~100 KB JSON per event BLOB uncompressed.

## 10. Per-sensor enabled flag (CRITICAL behavior point)

Each per-sensor config table (`event_config_type_a_per_sensor`, `event_config_type_b_per_sensor`, `event_config_type_c_per_sensor`, `event_config_type_d_per_sensor`) has an `enabled` column (mqtt_database.py:554, 1736, 1928, 2132, 2274, 2282). The column is persisted and read into `type_a_per_sensor_configs`, and analogous dicts for B/C/D via `get_type_X_per_sensor_configs`.

However, in the live detection paths:
- Type A: `_run_type_a_detection` (lines 1486-1553) does `sensor_config = sensor_configs_cache.get(sensor_id)` and proceeds unless that returns None (line 1498). The `enabled` field INSIDE the sensor_config is never consulted.
- Type B: `_load_type_b_config` (1291-1314) and `apply_type_b_per_sensor_configs` (1891-1903) read `T2`, `lower_threshold`, `upper_threshold` from per-sensor rows but do not read `enabled`. The detector runs on every sensor as long as `type_b_enabled` is True globally.
- Type C: `_load_type_c_config` (1317-1342) and `apply_type_c_per_sensor_configs` (1927-1937) likewise.
- Type D: `_load_type_d_config` (1344-1370) and `apply_type_d_per_sensor_configs` (1968-1981) likewise.

**Current behavior: the per-sensor `enabled` flag is stored in the DB but is not honored by the live detection loop.** The rewrite should preserve the DB column (used by the UI and by other consumers) and additionally actually consult it in the per-sensor detection path — gating detection off for a sensor when its per-sensor row has `enabled = False`. The global `enabled` flag on each `event_config_type_X` table remains the master switch for the type.

## 11. reload_config side effects

`reload_config` (lines 1385-1417):
1. Saves `was_type_a_enabled = self.type_a_enabled`.
2. Calls `_load_type_a_config`, `_load_type_b_config`, `_load_type_c_config`, `_load_type_d_config` in that order.
3. Inside `_load_type_a_config`, `_a_debounce_start = {}` (line 1253) — Type A debounce timers are CLEARED.
4. Inside `_load_type_b_config`, each detector's `update_parameters(debounce_seconds=...)` clears `_debounce_start_ts` to None (avg_type_b.py:381). Additionally, if T2 changed, the detector's window state (`window_deque`, `running_sum`, `window_count`, `initialized`, `last_timestamp`) is wiped (lines 383-391).
5. Inside `_load_type_c_config`, analogous: `_debounce_start_ts = None`, and if T3 changed, window state wiped.
6. Inside `_load_type_d_config`, `_debounce_start_ts = None`; if T4 changed, Stage-1 state wiped; if T5 changed, `t5_running_sum` is recomputed from the last T5 entries of `one_sec_averages` (avg_type_d.py:465-472). `one_sec_averages` itself is NOT cleared on T5 change, only recomputed over.
7. After the four loads, `reload_config` hard-wipes per-sensor Type A variance state (lines 1396-1403): for every sensor_id 1..12, clears `window_deque`, zeros running sums, `initialized=False`, `last_timestamp=None`. This happens regardless of whether T1 actually changed.
8. If `type_b_enabled`, calls `reset_type_b_detectors` (line 1406-1407) which calls `detector.reset()` on each of the 12 B detectors — full wipe of `signal_buffer`, `event_history`, `window_deque`, running sums, `initialized`, `last_timestamp`. Analogous for C and D (lines 1408-1411). Note `reset()` DOES clear `_debounce_start_ts` implicitly by not touching it — wait, actually `reset()` in avg_type_b.py:327-346 does NOT touch `_debounce_start_ts`. So a stale debounce crossing can survive `reset()`. This is a nuance to preserve.
9. If Type A was previously disabled but is now enabled, and no detection thread is running, `reload_config` starts the local detection thread (lines 1414-1417). When running under `worker_manager`, this branch is skipped.

State that SURVIVES `reload_config`:
- `sensor_buffers_a`, `sensor_buffers_short`, `timestamp_buffer` — raw circular buffers are never cleared by config reload (only by `_resize_buffers_live` on buffer-size change, lines 2061-2095). But note `_load_type_a_config` → `_update_type_a_buffer_size` may resize `sensor_buffers_a` and `timestamp_buffer` if T1 changed (lines 1282-1288) — preserving the last `target_size` samples.
- `active_ttl_timers`, `pending_post_event`, `last_event_timestamp`, `last_event_ttl`, `pending_break_events`, `_pending_break_queue` — TTL and BREAK state is untouched. In-flight events are NOT cancelled by a config reload.
- Mode state (`sensor_modes`, `sensor_active_states`, `sensor_startup_time`, etc.) — untouched.
- `sensor_variance_state[sid]['window_deque']` — untouched by plain `reload_config`, but wiped by the explicit loop at lines 1397-1403.

`reload_ttl_config` (lines 821-853) is a separate, narrower reload: re-reads `event_config_type_a/b/c/d.ttl_seconds` into `ttl_config` and rebuilds `per_sensor_ttl` from `get_all_type_a_per_sensor_configs_for_device`. It does NOT touch detection state, buffers, or active timers. New TTL values apply to future events.

## 12. Sample rate assumption

`SAMPLE_RATE_HZ` is declared 100 (line 41) but may be overridden via `app_config['sample_rate_hz']` (line 74). The STM32 hardware typically runs at ~123 Hz. The constant's uses are strictly sizing-related (never used for timing), and every time-based computation uses wall-clock timestamps:

- Sizing `sensor_buffers_a` maxlen: `buffer_size_a = buffer_duration_a_s × SAMPLE_RATE_HZ` (line 84). At SAMPLE_RATE_HZ=100 and the hardware running at 123 Hz, the deque fills 1.23× faster, so the actual buffer duration is ~81% of the nominal — a ±9 s window may only span ±7.3 s of history if `buffer_duration_a_s=60`. This is safe for B/C/D because their `window_deque`s are time-indexed via timestamps (the `popleft()` loop checks `oldest_ts < window_start_time`) and will correctly drop entries older than T2/T3/T4.
- Sizing each detector's internal `window_deque` maxlen: `int(T × sample_rate × 1.2)` (e.g. avg_type_b.py:105). At 123 Hz actual rate vs 100 Hz nominal the 1.2 headroom provides exactly enough room.
- Init-fill thresholds: `expected_samples = int(T × sample_rate × init_fill_ratio)`. At 123 Hz real rate and 100 Hz nominal, the threshold is hit ~23% earlier than intended, meaning detectors initialize sooner than the nominal T-window duration. This is acceptable.

Timing (the threshold tests, debounce elapsed-time checks, TTL elapsed, post-window elapsed, mode-transition durations, gap detection) always uses the sample's `timestamp` directly. So the detection semantics are correct regardless of actual rate.

## 13. File:line references for every claim above

- Priority hierarchy: `event_detector.py:764` (priority map), `736-819` (`_start_ttl_timer`).
- BUFFER_DURATION constants: `39-41`.
- app_config reads: `66-95`.
- Per-sensor deques: `102-110`, shared with B/C/D detectors at `210, 227, 245`.
- `sensor_variance_state`: `257-267`.
- Locks: `buffer_lock:137`, `variance_lock:279`, `ttl_lock:344`, detector `shared_lock`: `211, 228, 246`.
- Mode state init: `385-396`; grace period `396`.
- `check_mode_transition`: `855-955`; BREAK crossing registration `907`; STARTUP grace `942-951`.
- `_pending_break_queue` drain in `add_sensor_data`: `1014-1025`.
- `add_sensor_data`: `970-1147`; worker-queue enqueuing `1066, 1082, 1104, 1131`; `_check_expired_ttls` call `1048`.
- Data gap reset (A): `1614, 1696`; B: `avg_type_b.py:145`; C: `avg_type_c.py:142`; D: `avg_type_d.py:156-165`.
- Type A detection: `_run_type_a_detection:1440-1597`; incremental: `_calculate_variance_incremental:1667-1756`; debounce: `1542-1591`; per-sensor config read: `1495-1507`; gating on `sensor_active_states`: `1517`.
- `_save_wide_event` (A): `1789-1835`.
- `AvgTypeB.add_sample`: `avg_type_b.py:115-258`; band formula: `192-194`; debounce: `211-224`; data-gap: `143-151`.
- `AvgTypeC.add_sample`: `avg_type_c.py:112-259`; range test: `201`; violation label: `223-228`; debounce: `207-216`.
- `AvgTypeD.add_sample`: `avg_type_d.py:127-293`; Stage 1: `169-197`; Stage 2 bucketing: `199-209`; Stage 3: `211-219`; band: `221-226`; symmetric tolerance invariant: `62, 438-443`; warmup: returns at `190, 193, 214, 236`; debounce: `247-256`; `_calculate_one_sec_avg`: `295-305`; `_update_t5_buffer`: `307-318`.
- Type D reading C's `current_avg`: event_detector.py:`1126-1131` (snapshot build), worker_manager.py:`303-339` (processing); local fallback `event_detector.py:2400-2402`.
- `_handle_type_b_event`: `2412-2442`; `_handle_type_c_event`: `2444-2472`; `_handle_type_d_event`: `2474-2503`.
- `_start_ttl_timer`: `736-819`; Rule 1: `767-774`; Rule 2: `776-784`; Rule 3: `788-791`; Rule 3b: `793-805`; Rule 4: `807-819`.
- `_check_expired_ttls`: `537-734`; Phase 1: `564-580`; Phase 2: `582-590`; BLOB embedding `event_info`/`bounds_X`: `600-627`; BREAK drain: `669-734`.
- `_extract_data_window`: `404-535`; event-type window selection: `425-438`; CV% pass: `479-506`; output assembly: `508-535`.
- TTL config and per-sensor load: `312-334`; `reload_ttl_config`: `821-853`.
- BREAK queue: `_pending_break_queue:401, pending_break_events:402`; BREAK drain from queue: `1014-1025`; BREAK expiry: `669-734`.
- `_load_type_a_config`: `1223-1255`; `_update_type_a_buffer_size`: `1257-1288`; `_load_type_b_config`: `1291-1314`; `_load_type_c_config`: `1317-1342`; `_load_type_d_config`: `1344-1370`.
- `reload_config`: `1385-1417`.
- Per-sensor enabled in DB schema: `mqtt_database.py:554, 1736, 1928, 2132, 2274, 2282`; not consulted in detection: `event_detector.py:1495-1498` (A), `_load_type_b_config:1299-1314`, `_load_type_c_config:1329-1342`, `_load_type_d_config:1357-1370`.
- 500 ms dedup in batch UPDATE: `mqtt_database.py:3118-3129`; data_window overwrite: `3109-3111`.
- `sample_rate_hz` app_config override: `74`, detector `window_deque` sizing: `avg_type_b.py:105, avg_type_c.py:102, avg_type_d.py:79`.
- Worker manager gates: sensor_active: `worker_manager.py:207, 262, 319`; startup-window (T2/T3/T4): `212-216, 267-271, 324-328`.
- `check_event_a` helper (NOT live path): `event_a.py:8-56`.
- `REF_VALUE = 100.0`: `src/events/constants.py:6`.
