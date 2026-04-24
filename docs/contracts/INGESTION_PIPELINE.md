# INGESTION_PIPELINE.md

## 1. End-to-end flow summary

```
                          paho IO thread                       MQTT-Consumer thread (daemon)
                          ┌──────────────┐                     ┌──────────────────────────────┐
 broker → on_message() ──►│ json.loads   │── mqtt_data_queue ─►│ q.get(timeout=0.1)           │
                          │ stats++      │   (SimpleQueue)     │ parse_stm32_adc_payload()    │
                          │ q.put((pl,ts))│                    │ anchor dev_ts → unix ts      │
                          └──────────────┘                     │ drift check (|Δ|>5s reanchor)│
                                                               │ sensor_values -= offset[sid] │
                                                               │                               │
                                                               │ ┌─► EventDetector.add_sensor_data(sv, ts)
                                                               │ │     ├─ buffer_lock: append to timestamp_buffer,
                                                               │ │     │     sensor_buffers_a, sensor_buffers_short
                                                               │ │     ├─ check_mode_transition (POWER_ON/STARTUP/BREAK)
                                                               │ │     ├─ drain _pending_break_queue
                                                               │ │     ├─ variance_lock: _update_variance_state_unlocked()
                                                               │ │     ├─ _check_expired_ttls(ts)
                                                               │ │     └─ enqueue → snapshot_queue, type_a/b/c/d_queue
                                                               │ │           (via worker_manager CVs — separate doc)
                                                               │ │
                                                               │ └─► LiveDataHub.add_snapshot(did, ts, sv)
                                                               │       └─ _lock: append to per-sensor deques
                                                               │                + Event.set() → SSE wakeup
                                                               └───────────────────────────────┘
```

## 2. MQTT callback (on_message)

Location: `/home/embed/hammer/web_server.py:279-304` (production path) with a dormant alternate at `/home/embed/hammer/src/mqtt/client.py:54-95` (used only when the app factory in `src/app/__init__.py` runs; see 7.2).

| Aspect | Behavior |
|---|---|
| Thread | paho-mqtt internal network loop (started by `mqtt_client.loop_start()` at `web_server.py:312`) |
| Work done in callback | `time.time()` timestamp, `msg.payload.decode('utf-8')`, `json.loads(...)`, increment `mqtt_total_rx` / `mqtt_rx_by_device` under `mqtt_stats_lock`, and `mqtt_data_queue.put((payload, receive_ts))` |
| Processing in-callback | None beyond decode+stats. Declared "ULTRA-LIGHT" at line 280-283, target <1ms |
| Error handling | Outer `try/except Exception` at 285-304; on exception, increments `mqtt_total_errors` under `mqtt_stats_lock` and prints `"[MQTT] Error in callback: {e}"`. Malformed JSON is swallowed here — nothing is queued |
| Queue | `mqtt_data_queue = SimpleQueue()` (`web_server.py:162`) — unbounded, lock-free |

## 3. Consumer thread (mqtt_data_consumer)

Defined at `web_server.py:166-235`. Started inside `on_connect` at `web_server.py:265-269` when paho signals `rc == 0`:

```python
mqtt_consumer_running = True
mqtt_consumer_thread = threading.Thread(target=mqtt_data_consumer, daemon=True, name="MQTT-Consumer")
mqtt_consumer_thread.start()
```

| Aspect | Behavior |
|---|---|
| Daemon | `daemon=True` (line 267) |
| Main loop | `while mqtt_consumer_running:` (line 176) |
| Queue get | `mqtt_data_queue.get(timeout=mqtt_consumer_queue_timeout_s)` — default 0.1s (`web_server.py:141`, DB-overridable via `mqtt_consumer_queue_timeout_s` app_config, `web_server.py:614`) |
| On queue timeout | Bare `except: continue` (180-182) — loop iterates and re-checks `mqtt_consumer_running`. Catches `queue.Empty` and anything else |
| Shutdown | Loop exits when `mqtt_consumer_running` goes False. Flag is only ever set True (line 266); **no code path flips it back to False**. The thread dies with the process (daemon) |
| Exception handling | Outer `try/except Exception as e:` (177, 230-233) — prints `"[MQTT Consumer] Error processing data: {e}"` and `traceback.print_exc()`, then loops. A single bad message does NOT kill the thread |
| Per-message work | parse payload → get device_id → anchor timestamp (section 4) → parse sensors → apply offsets (section 5) → `event_detectors[did].add_sensor_data(sv, ts)` (line 224) → `live_data.add_snapshot(did, ts, sv)` (line 226) → update `device_last_data_ts` and `services.device_last_data_ts` (227-228) |

Note: `add_sensor_data` is called only if `device_id_str in event_detectors` (line 223). `add_snapshot` is called unconditionally (line 226).

## 4. Timestamp anchoring (exact algorithm)

Code: `web_server.py:191-210`. Uses the global dict `stm32_ts_offsets = {}  # device_id_str -> float offset (server_time - device_time)` at line 156.

Pseudocode (line numbers in trailing comments):

```python
dev_ts_ms = payload.get('ts')                                          # 191
if dev_ts_ms is not None:
    dev_ts_sec = dev_ts_ms / 1000.0                                    # 193
    if device_id_str not in stm32_ts_offsets:                          # 196
        stm32_ts_offsets[device_id_str] = receive_ts - dev_ts_sec      # 197  first sample: anchor
        print("Initialized offset ...")                                # 198
    ts = dev_ts_sec + stm32_ts_offsets[device_id_str]                  # 201
    if abs(ts - receive_ts) > TIMESTAMP_DRIFT_THRESHOLD_S:             # 205  default 5.0s
        stm32_ts_offsets[device_id_str] = receive_ts - dev_ts_sec      # 206  re-anchor
        ts = receive_ts                                                # 207
        print("Timestamp drift detected ... re-anchored.")             # 208
else:
    ts = receive_ts                                                    # 210  fallback
```

The timestamp passed downstream to `add_sensor_data` and `add_snapshot` is a **unix-seconds float** named `ts`. `TIMESTAMP_DRIFT_THRESHOLD_S` is 5.0 by default (`web_server.py:608`) and is live-tunable via `timestamp_drift_threshold_s` in app_config (`web_server.py:649`).

Re-anchor causes: the STM32 rebooted and its `ts` counter wrapped/reset, OR the server clock jumped (e.g. NTP adjust). Offset is never shrunk for small drift — only a hard reset when |Δ| > 5s.

## 5. Sensor offset application

| Aspect | Detail |
|---|---|
| Cache | `_sensor_offsets: dict = {}` at `web_server.py:725`, shape `{device_id_str: {sensor_id: offset_float}}`. Only non-zero offsets are stored (skipped if all zero) |
| Source table | `sensor_offsets` (device_id, sensor_id, offset); loaded via `db.get_all_offsets_for_cache()` — `src/database/mqtt_database.py:2713-2734` — which filters `WHERE offset != 0.0` |
| Startup load | `_load_sensor_offsets()` at `web_server.py:727-733`, called once at `web_server.py:915` |
| Refresh | `_refresh_sensor_offsets(device_id)` at `web_server.py:735-747`. Wired to `services.refresh_offsets` at `web_server.py:749`. Called by the offsets blueprint whenever the UI saves (`src/app/routes/offsets.py:35, 55, 69`) |
| Application | `web_server.py:218-220`:<br>`_offsets = _sensor_offsets.get(device_id_str)`<br>`if _offsets: sensor_values = {sid: val - _offsets.get(sid, 0.0) for sid, val in sensor_values.items()}`<br>The operation is **subtract** (adjusted = raw − offset). The inline comment at line 217 reads `# Apply per-sensor offsets: adjusted = raw - offset` |
| Missing offset | Defaults to 0.0 via `_offsets.get(sid, 0.0)` — unchanged value |
| Skip path | If the device has no non-zero offsets, `_offsets` is None and the dict-comp is skipped entirely (no copy, no work) |
| Concurrency | `_sensor_offsets` is **not** lock-guarded. Reads are done from the single MQTT-Consumer thread; writes happen from Flask request threads calling `_refresh_sensor_offsets`. The refresh does `_sensor_offsets[device_id_str] = non_zero` or `pop(...)` — both atomic at the CPython dict level for a single-key mutation. The only race is that the consumer may see old-or-new snapshot for a single sample after a UI save, which is acceptable |

## 6. EventDetector.add_sensor_data entry point

`src/detection/event_detector.py:970-1147`.

Signature: `def add_sensor_data(self, sensor_values: Dict[int, float], timestamp: float, raw_frames: List[Dict] = None)`.

Locks taken (in order):
1. `self.buffer_lock` (line 979) — wraps the buffer append block, ending at ~1032. Runs mode transitions and pending-break drain inside this lock.
2. `self.variance_lock` (line 1042) — acquired **outside** buffer_lock, batches all 12 sensors into one acquisition via `_update_variance_state_unlocked`. Explicit comment at 1033: "Update variance state OUTSIDE buffer_lock to avoid blocking SSE stream".
3. `self.worker_manager.snapshot_cv` / `type_a_cv` / `type_b_cv` / `type_c_cv` / `type_d_cv` — each a `threading.Condition`, acquired briefly per-type (lines 1054, 1064, 1090, 1112, 1139) to append + `notify()`.

State updated under `buffer_lock`:
- `self.timestamp_buffer.append(timestamp)` — **only if `type_a_enabled`** (981-983)
- `self.sensor_buffers_a[sid].append((ts, value))` — always (line 1000)
- `self.sensor_buffers_short[sid].append((ts, value))` — always (line 1001)
- Missing sensors: holds last value from `self.last_sensor_snapshot` or 0.0 on first sample (1003-1011); still appends to both buffers
- `self.last_sensor_snapshot = sensor_snapshot` (1012)
- `check_mode_transition(sid, value, ts)` called per present sensor (995) — POWER_ON/STARTUP/BREAK state machine
- Drains `self._pending_break_queue` into `self.pending_break_events` (1015-1025)

Data-gap detection happens inside `_update_variance_state_unlocked` under `variance_lock` (see section 8).

Queues written (all via `worker_manager` when present; fallback locals on the detector exist but unused in normal operation):

| Queue | Condition | Payload | Drop tracker |
|---|---|---|---|
| `worker_manager.snapshot_queue` | unconditional when `worker_manager` (1053) | `(int(device_id), ts, sensor_values)` | `self.snapshot_drop_count` (1060) |
| `worker_manager.type_a_queue` | `self.type_a_enabled` (1063) | `(self, ts, list(sensor_snapshot))` | `self.type_a_drop_count` (1069) |
| `worker_manager.type_b_queue` | `self.type_b_enabled` (1078) via `local_batch_b` → flush by size ≥ `BATCH_SIZE`(50) or time ≥ `BATCH_FLUSH_INTERVAL` | tuple with snapshot + detector refs + `sensor_active_states` + `sensor_startup_time` | (no counter — batch flush is best-effort) |
| `worker_manager.type_c_queue` | `self.type_c_enabled` (1100), same batching | same shape as B | — |
| `worker_manager.type_d_queue` | `self.type_d_enabled` (1122), same batching | B's shape + `avg_t3_snapshot` dict | — |

Early-return conditions: **none**. `add_sensor_data` always runs to completion. Each type block is gated by its own `type_*_enabled` flag; snapshot queueing is unconditional. The comment at 1155 ("must run even if detection is disabled, because add_sensor_data() always queues snapshots") documents this.

`_check_expired_ttls(timestamp)` is called at line 1048, after variance update and before queue enqueues.

## 7. LiveDataHub (TWO implementations)

### 7.1 web_server.py's LiveDataHub

Location: `web_server.py:430-566`. This is the instance actually in use in production — `live_data = LiveDataHub(max_samples=LIVE_BUFFER_MAX_SAMPLES, total_sensors=TOTAL_SENSORS)` (`web_server.py:800`), `LIVE_BUFFER_MAX_SAMPLES=2500` default (`web_server.py:66`, ~20s @ 123Hz), `TOTAL_SENSORS=12`.

| Aspect | Detail |
|---|---|
| Structure | `self._devices[did] = { 'timestamps': deque(maxlen=max_samples), 'sensors': {1..total_sensors: deque(maxlen=max_samples)}, 'seq': 0 }` (442-446) |
| Events | `self._events[did] = threading.Event()` (447) |
| Lock | Single `self._lock = threading.Lock()` (436) |
| add_snapshot sig | `(device_id: str, timestamp: float, sensor_values: dict) -> int` (450) |
| Monotonicity hack | Lines 453-457: if `timestamp <= last_ts`, force `timestamp = last_ts + 0.000001` (1µs). This is unique to this copy |
| seq counter | `dev['seq'] += 1` per call (459); returned. Never resets for the life of the process |
| Missing-sensor behavior | `dev['sensors'][sid].append(sensor_values.get(sid))` — **writes `None`** for missing sensors (462) |
| Event signal | `ev.set()` fired AFTER releasing `_lock` (464-467), comment: "so SSE threads wake immediately" |
| Methods | `add_snapshot` (450), `get_data` (470), `get_since` (496), `get_seq` (537), `get_total_seq` (544), `wait_for_data` (548) |
| get_since | Returns `None` if no new data; computes `first_seq = latest_seq - buffer_len + 1` and slices by `last_seq` offset. Returns `{timestamps, sensor_data, seq_from, seq_to}` |
| get_total_seq | Sums `seq` across all devices under `_lock` |
| wait_for_data | Blocks on the per-device `threading.Event` with deadline, polls 50ms if device not yet initialised |
| Thread-safety | All reads + writes to `_devices` under `_lock`. `_events` dict read without lock outside `add_snapshot` but entries are only inserted, never removed |

### 7.2 src/app/live_data.py's LiveDataHub

142-line alternate at `/home/embed/hammer/src/app/live_data.py`. Only constructed inside the unused factory `src/app/__init__.py:50-53`. Production `web_server.py` does NOT call `create_app()` — it registers blueprints directly at `web_server.py:101` and creates its own `LiveDataHub` at line 800. The `src/mqtt/client.py` on_message path (section 2) also only runs if the factory does. **The production pipeline always uses 7.1; 7.2 is dormant and exists as a refactor-in-progress.**

Behavioral differences vs 7.1:

| Behavior | web_server.py (7.1) | src/app/live_data.py (7.2) |
|---|---|---|
| Missing-sensor policy | Appends `None` | Holds last value; 0.0 on first sample (lines 33-46) |
| Monotonicity fix | Yes (`last_ts + 1e-6`) | **No** — accepts the timestamp as given (line 32) |
| `get_data` / `get_since` | Uses `list(deque)[start:]` | Uses `itertools.islice(deque, start, None)` for zero-copy slicing (lines 68, 75, 104, 114) |
| `get_seq` / `get_total_seq` | Present | **Absent** — only `add_snapshot`, `get_data`, `get_since`, `wait_for_data` |
| Event set timing | After lock | After lock (same) |

## 8. Data-gap detection

Threshold: `self.data_gap_reset_s`, read from app_config key `data_gap_reset_s` at `event_detector.py:87`, default `2.0`. DB default also 2.0 (`src/database/mqtt_database.py:224`). Live-tunable at `event_detector.py:2023-2024`.

Where checked:
- **`_update_variance_state_unlocked`** at `event_detector.py:1611-1620` (inside `variance_lock`, called from `add_sensor_data` line 1044). On gap: clears the variance `window_deque`, zeros `running_sum` / `running_sum_sq` / `window_count`, and sets `initialized = False` for that sensor. Scope: per-sensor variance state only.
- **`_calculate_variance_incremental`** at `event_detector.py:1694-1702` — same reset pattern, used by the detection-loop path rather than the ingestion path.

No reset of `sensor_buffers_a`, `sensor_buffers_short`, `timestamp_buffer`, LiveDataHub deques, mode state, or Type B/C/D state occurs on a gap. Gap detection is **per-sensor variance-only**.

## 9. Queue backpressure

| Queue | Bound | Drop policy |
|---|---|---|
| `mqtt_data_queue` (SimpleQueue, `web_server.py:162`) | Unbounded | None — grows to memory limit if consumer stalls |
| `worker_manager.snapshot_queue` (`src/workers/worker_manager.py:60`) | `queue_snapshot_maxlen` default 10000 | Producer checks `len < maxlen` BEFORE append; increments `snapshot_drop_count` on full (`event_detector.py:1056-1060`) |
| `worker_manager.type_a_queue` | `queue_maxlen_a` default 2000 | Same pattern; increments `type_a_drop_count` (1065-1069) |
| `worker_manager.type_b/c/d_queue` | `queue_maxlen_bcd` default 500 | `deque.extend()` of local batch — deque's own `maxlen` truncates silently, no drop counter (1091, 1113, 1140). Local batches flushed by `BATCH_SIZE=50` samples OR `BATCH_FLUSH_INTERVAL=0.1s` |
| `worker_manager.update_queue` | Unbounded (`worker_manager.py:61`, comment "to prevent drops") | None |
| EventDetector fallback `type_b/c/d_queue` (`event_detector.py:298-300`) | `QUEUE_MAXLEN` default 2000 | deque maxlen truncates silently; path only used when no `worker_manager` |
| LiveDataHub deques (per-device per-sensor) | `max_samples=2500` | deque maxlen truncates oldest |

If the detector is slower than MQTT ingress: `mqtt_data_queue` (unbounded) absorbs the backlog; consumer thread drains as fast as the detector's `add_sensor_data` allows; if `snapshot_queue` fills up (>10000 pending), `snapshot_drop_count` increments and the sample is dropped from the DB-write path but STILL lands in `sensor_buffers_*` and LiveDataHub (those happen earlier/independently).

## 10. Concurrency model

| Thread | Role | Touches |
|---|---|---|
| paho IO thread (`mqtt_client.loop_start()`) | Runs `on_message` | `mqtt_data_queue.put`, `mqtt_stats_lock`, `mqtt_total_rx`, `mqtt_rx_by_device` |
| MQTT-Consumer (daemon, `web_server.py:267`) | Drains `mqtt_data_queue` | `stm32_ts_offsets`, `_sensor_offsets` (read), `event_detectors[...]` (read), `live_data.add_snapshot`, `device_last_data_ts`, `services.device_last_data_ts` |
| Flask request threads | HTTP API reading live_data | `live_data.get_data/get_since/get_seq/wait_for_data` (all under `_lock`); `_refresh_sensor_offsets` writes `_sensor_offsets` |
| Flask SSE streams | Subscribe to `live_data._events[did]` | `wait_for_data`, `get_since` |
| GlobalWorkerManager snapshot worker | Drains `snapshot_queue` | Writes DB (but see `worker_manager.py:275` comment — drain-but-noop for saves) |
| GlobalWorkerManager type-a/b/c/d workers | Drain respective queues | Run detection algorithms; update `worker_manager.update_queue` |
| Update worker | Drains `update_queue` | DB event inserts/updates |
| DeviceWorker poll threads (`web_server.py:1920+`) | Non-MQTT devices only | Call `event_detectors[dev_id].add_sensor_data(..., raw_frames=...)` and `live_data.add_snapshot` for `dev_id != STM32_DEVICE_ID` (1956 early-continue guards against MQTT double-write) |
| `mqtt_stats_lock` | Guards mqtt_total_rx/tx, mqtt_rx_by_device, mqtt_tx_by_device, mqtt_total_errors |
| `EventDetector.buffer_lock` | timestamp_buffer, sensor_buffers_a, sensor_buffers_short, last_sensor_snapshot, mode state, `_pending_break_queue`/`pending_break_events` |
| `EventDetector.variance_lock` | `sensor_variance_state[sid]` (running sums, window_deque) |
| `EventDetector.ttl_lock` | `active_ttl_timers`, `pending_post_event`, `last_event_timestamp`, `last_event_ttl` |
| `worker_manager.*_cv` (Condition) | Each queue + its notify/wait |
| `LiveDataHub._lock` | `_devices[did]` deques + seq; NOT held when `_events[did].set()` fires |

Lock-ordering note: `add_sensor_data` acquires `buffer_lock` first, releases it, THEN acquires `variance_lock`, THEN the worker CVs — no nesting. Explicit comment at `event_detector.py:1033` and ordering comment at 412 ("Called OUTSIDE ttl_lock to avoid lock-order issues with buffer_lock").

`_sensor_offsets` and `stm32_ts_offsets` are both unguarded globals; relied on CPython GIL + single-writer / multi-reader patterns.

## 11. End-to-end timing

From the "ULTRA-LIGHT" callback comment at `web_server.py:280-283` and the consumer loop structure:

| Hop | Typical time | Source |
|---|---|---|
| paho RX → on_message complete | <1ms target | Callback docstring 283 |
| on_message enqueue → consumer get | ≤ `mqtt_consumer_queue_timeout_s`=0.1s worst case, but SimpleQueue wake is immediate when producer puts | 180 |
| Consumer parse+anchor+offset | O(N_sensors) dict comp | 213-220 |
| `add_sensor_data` | Under buffer_lock: 12 deque appends + mode transition checks; variance update for all 12 under one lock; 4 queue appends. Comment at 1044 says "OPTIMIZATION: Batch all 12 sensors into single lock acquisition (12× faster)" | 979-1147 |
| `add_snapshot` | One lock, 1+12 deque appends, one Event.set | 450-468 |

Known bottlenecks documented in code:
- Comment at `event_detector.py:1033` — variance update moved outside buffer_lock specifically "to avoid blocking SSE stream".
- Comment at 980-981 — Type A timestamp_buffer append skipped when disabled to save "12 append operations" at 100Hz.
- Comment at 1084-1087 — B/C/D batching changed from pure size-based to `BATCH_SIZE OR BATCH_FLUSH_INTERVAL` to reduce event latency "0.4s → 0.1s".
- Comment at `web_server.py:160-161` — the SimpleQueue split was introduced to "enable 100 Hz sustained throughput".

Latency instrumentation: `_maybe_log_latency(stage, device_id, sample_ts, extra)` at `web_server.py:370-427` — writes `ts/stage/device_id/lag_ms/...` lines to `latency_log['path']` when enabled; captures `snapshot_q`, `event_up_q`, `snapshot_drop` counters.

## 12. File/line references

| Claim | File:line |
|---|---|
| `SimpleQueue` import | `web_server.py:18` |
| `mqtt_data_queue = SimpleQueue()` | `web_server.py:162` |
| `mqtt_consumer_running` / `_thread` globals | `web_server.py:163-164` |
| `mqtt_data_consumer` definition | `web_server.py:166-235` |
| Consumer queue get + timeout | `web_server.py:180` |
| Timestamp anchor init | `web_server.py:196-198` |
| Compute `ts` | `web_server.py:201` |
| Drift re-anchor (`> TIMESTAMP_DRIFT_THRESHOLD_S`) | `web_server.py:205-208` |
| Fallback `ts = receive_ts` | `web_server.py:210` |
| Parse sensor values | `web_server.py:213` |
| Offset subtract application | `web_server.py:217-220` |
| Call `add_sensor_data` | `web_server.py:223-224` |
| Call `add_snapshot` | `web_server.py:226` |
| `on_message` definition | `web_server.py:279-304` |
| Consumer thread launch | `web_server.py:265-269` |
| `mqtt_client.loop_start()` | `web_server.py:312` |
| `TIMESTAMP_DRIFT_THRESHOLD_S` default 5.0 | `web_server.py:608` |
| `TIMESTAMP_DRIFT_THRESHOLD_S` live update | `web_server.py:649` |
| `_sensor_offsets` cache dict | `web_server.py:725` |
| `_load_sensor_offsets` | `web_server.py:727-733` |
| `_refresh_sensor_offsets` | `web_server.py:735-747` |
| `services.refresh_offsets` wiring | `web_server.py:749` |
| `_load_sensor_offsets()` startup call | `web_server.py:915` |
| `get_all_offsets_for_cache` DB method | `src/database/mqtt_database.py:2713-2734` |
| Offsets blueprint save → refresh | `src/app/routes/offsets.py:35,55,69` |
| LiveDataHub (web_server copy) | `web_server.py:430-566` |
| `add_snapshot` monotonicity hack | `web_server.py:453-457` |
| `add_snapshot` missing-sensor None | `web_server.py:462` |
| `get_seq` / `get_total_seq` | `web_server.py:537-546` |
| `wait_for_data` | `web_server.py:548-566` |
| `live_data = LiveDataHub(...)` | `web_server.py:800` |
| Alt LiveDataHub | `src/app/live_data.py:1-142` |
| Alt `add_snapshot` hold-last-value | `src/app/live_data.py:28-52` |
| Alt factory instantiation (dormant) | `src/app/__init__.py:50-53` |
| Alt `on_message` (dormant) | `src/mqtt/client.py:54-95` |
| `add_sensor_data` definition | `src/detection/event_detector.py:970-1147` |
| `buffer_lock` init | `src/detection/event_detector.py:137` |
| `variance_lock` init | `src/detection/event_detector.py:279` |
| `ttl_lock` init | `src/detection/event_detector.py:344` |
| Type A buffer conditional append | `src/detection/event_detector.py:982-983` |
| sensor_buffers_a / short appends | `src/detection/event_detector.py:1000-1001` |
| Missing-sensor hold-last-value | `src/detection/event_detector.py:1004-1011` |
| `_pending_break_queue` drain | `src/detection/event_detector.py:1015-1025` |
| Variance update outside buffer_lock | `src/detection/event_detector.py:1033-1044` |
| `_check_expired_ttls(timestamp)` | `src/detection/event_detector.py:1048` |
| Snapshot queue append + drop counter | `src/detection/event_detector.py:1053-1060` |
| Type A queue append + drop counter | `src/detection/event_detector.py:1063-1069` |
| Type B batched enqueue | `src/detection/event_detector.py:1078-1094` |
| Type C batched enqueue | `src/detection/event_detector.py:1100-1116` |
| Type D batched enqueue | `src/detection/event_detector.py:1122-1143` |
| `data_gap_reset_s` from cfg | `src/detection/event_detector.py:87` |
| Gap reset in variance unlocked | `src/detection/event_detector.py:1611-1620` |
| Gap reset in incremental variance | `src/detection/event_detector.py:1694-1702` |
| Live-update `data_gap_reset_s` | `src/detection/event_detector.py:2023-2024` |
| `data_gap_reset_s` DB default 2.0 | `src/database/mqtt_database.py:224` |
| Worker queue bounds | `src/workers/worker_manager.py:46-61` |
| `mqtt_consumer_queue_timeout_s` DB default 0.1 | `src/database/mqtt_database.py:336` |
| `BATCH_SIZE=50`, `BATCH_FLUSH_INTERVAL` | `src/detection/event_detector.py:289-290` |
| DeviceWorker polling path | `web_server.py:1940-1961` |
| STM32 double-write guard | `web_server.py:1956` |
| `_maybe_log_latency` | `web_server.py:370-427` |
