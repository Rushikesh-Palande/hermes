# WORKER_PROTOCOL.md

## 1. Overview

`GlobalWorkerManager` (`src/workers/worker_manager.py:8`) is a process-wide singleton owned by the app layer and shared across every `EventDetector` instance. Its job is to own the background I/O thread pool that absorbs the detection hot path's asynchronous work so the MQTT ingestion thread never blocks on disk I/O.

It launches exactly **six daemon threads** in `start()` at `worker_manager.py:120-127`:

1. `Global-TypeA-Worker` — runs `_type_a_worker` (line 151)
2. `Global-TypeB-Worker` — runs `_type_b_worker` (line 173)
3. `Global-TypeC-Worker` — runs `_type_c_worker` (line 228)
4. `Global-TypeD-Worker` — runs `_type_d_worker` (line 282)
5. `Global-Snapshot-Worker` — runs `_snapshot_worker` (line 341)
6. `Global-Update-Worker` — runs `_update_worker` (line 374)

(Note: the startup log at line 132 says "Started 4 global worker threads" — the message is stale; six threads actually start.)

**Purpose.** The four type-X workers are "compute" workers — they receive sample batches from the producer, dispatch them into per-sensor detector `add_sample()` calls, and fire event callbacks. The snapshot_worker is a drain-only sink (see §2.5). The update_worker is the only worker that actually writes to the `events` table and afterwards publishes MQTT notifications.

**Lifecycle.** `start()` is called once at application bootstrap; after that the threads run forever. A `stop()` method (line 134) exists, but all threads are `daemon=True`, so in production they die whenever the interpreter exits. There is no orderly drain of in-flight batches on shutdown — see §6.

## 2. Queue inventory

All six queues are `collections.deque` (not `queue.Queue`), each guarded by a dedicated `threading.Condition` for producer/consumer wake-ups. Five of them have a `maxlen` that silently drops on overflow (deque semantics); only `update_queue` is unbounded. Queue construction is at `worker_manager.py:46-61`.

| Worker | Queue attr | `maxlen` source (default) | Condition | Batch size attr | Consumer method |
|---|---|---|---|---|---|
| Type A | `type_a_queue` | `queue_maxlen_a` (2000) | `type_a_cv` | `batch_size_a` (100) | `_type_a_worker` |
| Type B | `type_b_queue` | `queue_maxlen_bcd` (500) | `type_b_cv` | `batch_size_bcd` (100) | `_type_b_worker` |
| Type C | `type_c_queue` | `queue_maxlen_bcd` (500) | `type_c_cv` | `batch_size_bcd` (100) | `_type_c_worker` |
| Type D | `type_d_queue` | `queue_maxlen_bcd` (500) | `type_d_cv` | `batch_size_bcd` (100) | `_type_d_worker` |
| Snapshot | `snapshot_queue` | `queue_snapshot_maxlen` (10000) | `snapshot_cv` | `batch_size_snapshot` (200) | `_snapshot_worker` |
| Update | `update_queue` | unbounded | `update_cv` | `batch_size_update` (200) | `_update_worker` |

### 2.1 Type A worker

**Producer.** `EventDetector.add_sensor_data` at `event_detector.py:1063-1069`:

```python
if self.type_a_enabled and self.worker_manager:
    with self.worker_manager.type_a_cv:
        if len(self.worker_manager.type_a_queue) < self.worker_manager.type_a_queue.maxlen:
            self.worker_manager.type_a_queue.append((self, timestamp, list(sensor_snapshot)))
            self.worker_manager.type_a_cv.notify()
        else:
            self.type_a_drop_count += 1
```

**Task tuple (3 elements).** `(detector, timestamp, sensor_snapshot_list)` where `detector` is the `EventDetector` instance itself (not a copy of detectors), `timestamp` is a float Unix time, and `sensor_snapshot_list` is a fresh `list(sensor_snapshot)` of the 12 sensor values.

**Consumer.** `_type_a_worker` (`worker_manager.py:151-171`). It waits on `type_a_cv` with a `queue_timeout_s` (default 0.5 s), then pops up to `batch_size_a` tasks (default 100), releases the lock, and for each task calls `detector.process_type_a_sample(timestamp, sensor_snapshot)` (line 168). The worker does **not** touch the DB directly — all Type A event rows reach the DB only via the TTL handoff to `update_queue` (see §10).

**Retry.** None. Exceptions are caught at the outer loop (line 169) and printed; the batch is silently dropped.

### 2.2 Type B worker

**Producer.** `EventDetector.add_sensor_data` at `event_detector.py:1078-1094`. Producer first buffers into a per-detector `self.local_batch_b` list (initialized at line 286, `BATCH_SIZE = 50`). At `event_detector.py:1082`:

```python
self.local_batch_b.append((timestamp, sensor_snapshot, self.type_b_detectors,
                           self._handle_type_b_event,
                           self.sensor_active_states.copy(),
                           self.sensor_startup_time.copy()))
```

It flushes into `worker_manager.type_b_queue` when either 50 tasks accumulate or `BATCH_FLUSH_INTERVAL` (default 0.1 s, configurable via `worker_batch_flush_interval_s`) elapses. Flush path: `event_detector.py:1089-1094` extends the global deque under `type_b_cv`.

**Task tuple (6 elements).** `(timestamp, sensor_snapshot, detectors, callback, sensor_active_states, sensor_startup_time)`.

- `timestamp` — float.
- `sensor_snapshot` — list of 12 sensor floats (passed by reference — **not** copied in the producer; see quirk below).
- `detectors` — **a live reference to `self.type_b_detectors`** (the EventDetector's own dict of per-sensor Type B detectors). This is a known quirk: it is neither a snapshot nor a copy. If the detector dict is mutated (config reload, per-sensor reconfig) between enqueue and worker consumption, the worker sees the new state. For Type B detectors this is usually safe because mutation replaces whole entries under a lock, but the protocol does not guarantee atomicity across a batch.
- `callback` — bound method `self._handle_type_b_event`, captured at enqueue.
- `sensor_active_states` — `.copy()` snapshot (shallow dict of `{sensor_id: bool}`).
- `sensor_startup_time` — `.copy()` snapshot (shallow dict of `{sensor_id: float or None}`).

**Consumer.** `_type_b_worker` (`worker_manager.py:173-192`) waits on `type_b_cv`, pops up to `batch_size_bcd` (default 100), then calls `_process_type_b(task)` for each (line 189).

`_process_type_b` (`worker_manager.py:194-226`) unpacks using a length check that preserves backward compatibility with three legacy tuple shapes:

- `len(task) == 6` → full new format (current).
- `len(task) == 5` → older: no `sensor_startup_time`.
- fallback (4-tuple) → no active_states, no startup_time.

**Only the 6-tuple is produced by current code**, so the 4- and 5-tuple branches are dead (§9). For each sensor in `detectors`, the worker skips sensors that are not `active` (mode switching), skips sensors whose startup window has not yet reached `detector.T2`, then calls `detector.add_sample(timestamp, val)` where `val = sensor_snapshot[sensor_id - 1]`. When the returned status is `"EVENT"`, the worker fetches the most recent event via `detector.get_recent_events(count=1)` and invokes `callback(sensor_id, event_info, sensor_snapshot)`. The callback ultimately enqueues a TTL pending event; the final DB write happens later via the TTL machinery (§10).

**Retry.** None. Exceptions in the worker loop or in `_process_type_b` propagate to the outer loop, are printed, and the rest of the batch is abandoned.

### 2.3 Type C worker

Mirrors Type B exactly.

**Producer.** `event_detector.py:1100-1116`. Local buffer `self.local_batch_c`, flushed at size 50 or every 0.1 s. Task tuple at line 1104 is identical in shape to Type B but carries `self.type_c_detectors` (not `type_b_detectors`).

**Task tuple (6 elements).** `(timestamp, sensor_snapshot, type_c_detectors_ref, self._handle_type_c_event, sensor_active_states.copy(), sensor_startup_time.copy())`. Same live-reference quirk on the detectors dict as Type B.

**Consumer.** `_type_c_worker` (`worker_manager.py:228-247`) → `_process_type_c` (lines 249-280). The window-duration gate uses `detector.T3` rather than T2. Callback is `self._handle_type_c_event`.

### 2.4 Type D worker

**Producer.** `event_detector.py:1122-1143`. Before appending to `self.local_batch_d`, the producer builds a snapshot of Type C's running averages to pass as cross-event-dependency input:

```python
avg_t3_snapshot = {
    sid: self.type_c_detectors[sid].current_avg
    for sid in range(1, 13)
    if sid in self.type_c_detectors
}
self.local_batch_d.append((timestamp, sensor_snapshot, self.type_d_detectors,
                           self._handle_type_d_event,
                           self.sensor_active_states.copy(),
                           self.sensor_startup_time.copy(),
                           avg_t3_snapshot))
```

So Type D's payload carries the state of the **C detectors' `current_avg`** at the moment of enqueue.

**Task tuple (7 elements).** `(timestamp, sensor_snapshot, type_d_detectors_ref, callback, sensor_active_states_copy, sensor_startup_time_copy, avg_t3_snapshot)`.

**Consumer.** `_type_d_worker` (`worker_manager.py:282-301`) → `_process_type_d` (lines 303-339). The unpacker handles 4/5/6/7-element tuples (`len(task) >= 7` takes the current path). Only the 7-tuple is produced today. Window gate uses `detector.T4`. The worker calls `detector.add_sample(timestamp, val, avg_t3_from_c=avg_t3)` where `avg_t3 = avg_t3_snapshot.get(sensor_id)`. Callback mirrors Type B/C.

### 2.5 Snapshot worker

**Producer.** `event_detector.py:1050-1060`:

```python
if self.worker_manager:
    with self.worker_manager.snapshot_cv:
        if len(self.worker_manager.snapshot_queue) < self.worker_manager.snapshot_queue.maxlen:
            self.worker_manager.snapshot_queue.append((int(self.device_id), timestamp, sensor_values))
            self.worker_manager.snapshot_cv.notify()
        else:
            self.snapshot_drop_count += 1
```

**Task tuple (3 elements).** `(device_id_int, timestamp_float, sensor_values_dict)`. The shape matches `insert_continuous_sensor_data_batch` at `mqtt_database.py:2996`, which expects `(device_id, timestamp, sensor_values_dict)`.

**Consumer.** `_snapshot_worker` (`worker_manager.py:341-372`) waits on `snapshot_cv`, drains up to `batch_size_snapshot` (default 200), then...

```python
if batch:
    # DISABLED: Don't save continuous data to DB (only save event windows ±9s)
    # self.snapshot_db.insert_continuous_sensor_data_batch(batch)
    pass  # Just drain the queue, don't save to DB
```

**The actual `insert_continuous_sensor_data_batch` call is commented out at line 363.** The `snapshot_queue` fills from the detection hot path and drains to `/dev/null`. The producer-side comment at `event_detector.py:1051` explicitly acknowledges this: "Snapshot worker drains this but doesn't save to DB".

**Consequence.** Only *event rows* (i.e., rows produced by `_update_worker` calling `batch_update_event_detection`) get persisted. There is no continuous-sample audit trail in the `events` table from this path. Live data is served from an in-memory `LiveDataHub` ring buffer (≈2000 samples) per the comment on line 362. The data window attached to each event row provides a local ±9 s excerpt, but nothing between events is persisted.

The queue-and-notify mechanism is retained because downstream code (and the producer at `event_detector.py:1054`) use `snapshot_cv` as a fence/notify signal for timing purposes, and because re-enabling the insert requires no producer changes.

### 2.6 Update worker

**Producer (two call sites, both in the TTL save path).**

1. Type A/B/C/D TTL-ready event path at `event_detector.py:631-644`:

```python
if self.worker_manager:
    update_payload = {
        'device_id':       int(self.device_id),
        'timestamp':       trigger_time,
        'sensor_id':       sensor_id,
        'event_flags':     {(event_type.lower(),): 'Yes'},
        'average':         event_data.get('window_avg') or event_data.get('smoothed_avg'),
        'sensor_snapshot': event_data.get('sensor_snapshot'),
        'data_window':     data_window_bytes,
        'publish_immediate': False,
    }
    with self.worker_manager.update_cv:
        self.worker_manager.update_queue.append(update_payload)
        self.worker_manager.update_cv.notify()
```

2. Break-event TTL path at `event_detector.py:707-720`, identical shape with `event_flags={('break',): 'Yes'}` and `average=None`.

A third helper `_queue_event_update` at `event_detector.py:1203-1221` builds an equivalent payload (and is the only code path that sets `retry: 0` explicitly), but is not used for the normal TTL save flow.

**Task shape (dict).** Keys used on the consumer side: `device_id`, `timestamp`, `sensor_id`, `event_flags` (dict keyed by `(event_type,)` single-element tuples with `'Yes'`/`'No'` values), `average`, `sensor_snapshot`, `data_window` (bytes — a JSON blob with embedded `event_info` metadata, see `event_detector.py:600-629`), `publish_immediate` (always `False` in current code — §4.1), and optionally `retry` (incremented on failures).

**Consumer.** `_update_worker` (`worker_manager.py:374-465`). Waits on `update_cv`, drains up to `batch_size_update` (default 200), then calls `self.update_db.batch_update_event_detection(batch)` (line 394). Three outcomes:

- Returned list is non-empty (retryable failures). For each event the worker increments `retry`, drops the event after 5 attempts with a stdout log `[GlobalWorkerManager] Dropping event update after retries: ...`, re-appends survivors, notifies `update_cv`, sleeps `sleep_on_error` (default 0.05 s). See §7.
- Returned list empty and batch published cleanly. The worker then iterates each event in `batch` and (unless `publish_immediate` is set) publishes it to MQTT (§4). If any step inside the publish loop raises, the exception is caught at line 459 and printed; the DB row is already written, so no retry is attempted for publish failures.

After any batch, the worker sleeps `sleep_idle_update` (default 0.05 s) before re-entering the wait loop (line 462).

## 3. DB connection ownership

`GlobalWorkerManager.__init__` (`worker_manager.py:16-24`) instantiates **two new `MQTTDatabase` objects** in addition to the shared one passed by the caller:

- `self.db` — the shared database instance (injected). Used by Type A/B/C/D workers indirectly (they call `detector.process_type_a_sample` / `detector.add_sample`, which reach back into the detector's own `self.db`, which is the shared instance). Mostly read-only from the worker's perspective.
- `self.snapshot_db = MQTTDatabase(db_path)` — a dedicated `MQTTDatabase` connection, intended for the snapshot worker's `insert_continuous_sensor_data_batch` call. Currently unused since the call is commented out (§2.5), but the connection is nonetheless opened and kept open.
- `self.update_db = MQTTDatabase(db_path)` — dedicated connection used by `_update_worker` for `batch_update_event_detection` (line 394) and `find_event_id` (line 435).

Each `MQTTDatabase` constructor runs `_init_database` (schema creation / migration), so starting the worker manager causes **three additional schema-init passes** on top of the main app's instance — four total on startup (main, worker-shared-use, snapshot, update). This has only startup cost; at steady state the connections are independent.

The motivation (per comments at lines 13-14 and 377-379) is to avoid SQLite lock contention between continuous-data inserts and event UPDATEs by letting WAL mode interleave writers.

## 4. MQTT publish after save

The update worker is the only place event MQTT notifications are sent in the normal path. The publish block runs only if `batch_update_event_detection` returned no failures (`worker_manager.py:406-460`).

### 4.1 `publish_immediate` flag

Every `update_payload` dict carries a `publish_immediate` boolean. The intended design:

- `publish_immediate=True` → the detector already called `publish_event_mqtt` directly at event-trigger time (for latency-sensitive paths), so the worker must **not** re-publish after the DB write.
- `publish_immediate=False` → the worker owns the MQTT publish.

In current code, **only `False` is ever set** — at `event_detector.py:640` and `event_detector.py:716`. The `True` branch (the `if event.get('publish_immediate'): ... continue` block at `worker_manager.py:411-414`) is dead. The helper `_queue_event_update` at `event_detector.py:1206` accepts `publish_immediate` as a parameter and defaults to `False`; no caller in the codebase passes `True`.

### 4.2 Event type discovery on publish

At `worker_manager.py:415-420`:

```python
event_flags = event.get('event_flags', {}) or {}
event_type = None
for candidate in ['a', 'b', 'c', 'd', 'break']:
    if event_flags.get((candidate,)) == 'Yes':
        event_type = candidate
        break
```

The worker iterates the ordered tuple `['a', 'b', 'c', 'd', 'break']` and publishes **only the first `'Yes'` flag found**. If an event dict sets multiple type flags to `'Yes'` (possible when two detector types fire on the same row and are coalesced inside the 500 ms UPDATE tolerance window — §5), only the earliest-matched type in the list is emitted to MQTT; the others are silently invisible to subscribers.

In practice, because each producer payload at `event_detector.py:636` sets `event_flags = {(event_type.lower(),): 'Yes'}` (exactly one flag per enqueue), this only bites when two payloads update the *same* DB row (the UPDATE picks it up via `ABS(timestamp - ?) < 0.500` at `mqtt_database.py:3125`). Subscribers should not treat this channel as a complete event stream when multiple event types overlap in time.

### 4.3 Topic and payload format

Publishing goes through `publish_event_mqtt` in `src/mqtt/client.py:140-185`.

- **Topic:** `stm32/events/<device_id>/<sensor_id>/<TYPE>` (TYPE uppercase; e.g. `stm32/events/3/7/A`, `stm32/events/3/7/BREAK`).
- **Payload:** JSON `{"timestamp": "YYYY-MM-DD HH:MM:SS.mmm", "sensor_value": <float|null>}`.
- QoS: 0.
- Counters: `services.event_total_published` and `services.event_published_by_device[device_key]` are incremented under `services.event_stats_lock` (client.py:175-181).
- If MQTT is disabled/unavailable, the publish is skipped silently except under `MQTT_EVENT_DEBUG=1`.

The `sensor_value` the worker passes is the sensor's value at event time: `sensor_snapshot[sensor_id - 1]` if present (`worker_manager.py:446-447`), else `event['average']` as fallback.

## 5. `find_event_id` behavior

Called at `worker_manager.py:435-440` just before MQTT publish, to supply `event_id` as part of the published payload's context. The implementation is at `mqtt_database.py:3355-3385`:

```python
column = f"sensor{sensor_id}_event_{event_type}"
cursor.execute(f"""
    SELECT id FROM events
    WHERE device_id = ?
      AND {column} = 'Yes'
      AND ABS(timestamp - ?) < ?
    ORDER BY ABS(timestamp - ?)
    LIMIT 1
""", (device_id, timestamp, tolerance_s, timestamp))
```

Behavior:

- Opens a **new `sqlite3.connect` per call** with `check_same_thread=False` and a 30 s busy timeout (lines 3365-3366). This bypasses `self.update_db`'s pooled connection and leaks a connection open/close on every event published. Closed explicitly at line 3379.
- Matches on the per-event-type column `sensor<N>_event_<type>` = `'Yes'`.
- Tolerance defaults to 0.5 s (`tolerance_s=0.5` at line 3356). Picks the row with smallest `ABS(timestamp - ?)`.
- Validates `sensor_id ∈ [1..12]` and `event_type ∈ {'a','b','c','d','break'}`; returns `None` otherwise.
- Errors are caught at line 3383 and return `None`; the worker downstream handles `None` gracefully (publishes without an event_id).

**Correctness caveat.** If two events of the same type fire on the same sensor within 500 ms, the UPDATE at `mqtt_database.py:3118-3129` also coalesces them into the same row (same tolerance window). So `find_event_id` will always find the coalesced row — but consumers cannot distinguish the two triggers. If the DB ever contains two separate rows within tolerance (e.g., from different code paths), `find_event_id` returns the closest by timestamp, which may not be the intended one.

## 6. Shutdown behavior

`stop()` at `worker_manager.py:134-149`:

```python
self.running = False
with self.type_a_cv: self.type_a_cv.notify_all()
# ... same for all other CVs ...
for t in self.threads:
    t.join(timeout=1.0)
```

However:

- All threads are created with `daemon=True` (lines 121-126), so at interpreter exit they terminate immediately without running the post-`self.running=False` drain.
- There is **no flush loop** that forces queues empty before exit. Any tasks pending in `type_a_queue`, `type_b_queue`, `type_c_queue`, `type_d_queue`, `snapshot_queue`, or `update_queue` at shutdown are lost.
- In particular, events that have been sitting in `update_queue` but not yet written with `batch_update_event_detection` will never hit the DB or MQTT.
- `stop()` is not wired into any atexit handler in this file.
- Per audit: a `mqtt_consumer_running` flag exists elsewhere in the codebase but is never set to `False` during shutdown.

## 7. Error handling / retry protocol

The retry protocol lives entirely inside `_update_worker` and only covers DB-write failures (`worker_manager.py:394-405`):

```python
failed_updates = self.update_db.batch_update_event_detection(batch)
if failed_updates:
    with self.update_cv:
        for event in failed_updates:
            retry_count = int(event.get('retry', 0)) + 1
            if retry_count > 5:
                print(f"[GlobalWorkerManager] Dropping event update after retries: {event}")
                continue
            event['retry'] = retry_count
            self.update_queue.append(event)
        self.update_cv.notify_all()
    time.sleep(self.sleep_on_error)
```

- `retry` starts at 0 (default) and is incremented each time a batch returns the event in `failed_updates`.
- Max retries = 5 (`retry_count > 5` ⇒ drop).
- Dropped events log a one-line `print` to stdout; there is no metric, no counter, no Prometheus/structured log.
- No circuit breaker: the worker keeps pulling from the queue regardless of how often it fails, and sleeps only `sleep_on_error` (default 0.05 s) between retries.
- No dead-letter queue: dropped events are discarded entirely.
- `batch_update_event_detection` is itself tolerant: on `OperationalError "database is locked"` it sleeps 0.2 s and retries the whole batch once (`mqtt_database.py:3156-3163`); if that also fails, it returns the full batch as failed. On other `sqlite3.Error`, it prints and returns the full batch as failed. Under normal successful paths it returns `[]` (empty — all events either updated or fallback-inserted; see §10).

The A/B/C/D workers have **no retry at all**: they catch `Exception` at the outermost level (lines 169-171, 190-192, 245-247, 299-301), print with `traceback.print_exc()`, and continue. Any detection state lost inside a single batch is gone.

The snapshot worker catches and sleeps 1 s on error (line 372).

## 8. Configuration knobs

All read via `database.get_app_config_value(key, fallback)` in `__init__` (`worker_manager.py:26-43`). All can be live-updated through `apply_app_config_live` (lines 74-109), which also supports resizing the bounded deques in place.

| Key | Default | Attribute | Effect |
|---|---|---|---|
| `worker_batch_size_a` | 100 | `batch_size_a` | Max tasks per Type A drain |
| `worker_batch_size` | 100 | `batch_size_bcd` | Max tasks per Type B/C/D drain (shared) |
| `worker_batch_size_snapshot` | 200 | `batch_size_snapshot` | Max tasks per snapshot drain |
| `worker_batch_size_update` | 200 | `batch_size_update` | Max events per DB update batch |
| `worker_queue_timeout_s` | 0.5 | `queue_timeout_s` | Condition-variable wait timeout (all workers) |
| `worker_sleep_idle_snapshot_s` | 0.01 | `sleep_idle_snapshot` | Snapshot worker post-batch sleep |
| `worker_sleep_idle_update_s` | 0.05 | `sleep_idle_update` | Update worker post-batch sleep |
| `worker_sleep_on_error_s` | 0.05 | `sleep_on_error` | Sleep after retry-requeue |
| `queue_maxlen_a` | 2000 | `type_a_queue.maxlen` | Bounded Type A queue size |
| `queue_maxlen_bcd` | 500 | `type_b/c/d_queue.maxlen` | Bounded B/C/D queue size (shared default) |
| `queue_snapshot_maxlen` | 10000 | `snapshot_queue.maxlen` | Bounded snapshot queue size |

Note: no dedicated `worker_batch_size_b/c/d` exists — all three share `worker_batch_size`. The update queue is **always unbounded** (line 61 has no `maxlen`) and is not tunable.

A producer-side flush knob lives on the detector itself (`event_detector.py:290`): `worker_batch_flush_interval_s` (default 0.1 s) controls how often `local_batch_b/c/d` is forcibly flushed into the global queue regardless of batch size.

## 9. Known dead code paths

1. **`publish_immediate=True` branch** (`worker_manager.py:411-414`). No caller in the codebase sets this key to `True`; both producer sites (`event_detector.py:640`, `:716`) hardcode `False`.
2. **4-tuple and 5-tuple legacy shapes** in `_process_type_b`, `_process_type_c`, `_process_type_d` (worker_manager.py:195-203, 250-258, 303-315). Current producers always emit the 6-tuple (B/C) or 7-tuple (D); the older branches remain for migration robustness but are never hit at runtime.
3. **`insert_continuous_sensor_data_batch` call** in `_snapshot_worker` is commented out (worker_manager.py:363). The method itself (`mqtt_database.py:2996`) remains reachable from nowhere in the current hot path.
4. **`insert_event_direct`** (`mqtt_database.py:3306-3353`) is only reached from the `else: self.worker_manager is None` fallbacks at `event_detector.py:645-667` and `event_detector.py:722-734`. In production the app always creates a `GlobalWorkerManager`, so these branches are effectively unreachable. The synchronous publish immediately after direct insert (line 658) is likewise dead.

## 10. Interaction with TTL / pending post-events

The TTL machinery (`_start_ttl_timer` at `event_detector.py:736` and `_check_expired_ttls` called at `event_detector.py:1048`) is the bridge between the compute workers and the update worker.

Flow:

1. Hot path: `add_sensor_data` enqueues Type A/B/C/D tasks into the respective worker queues (A directly, B/C/D via local batches — §§2.1-2.4).
2. The type worker pops the task, runs `detector.add_sample(...)` (B/C/D) or `detector.process_type_a_sample(...)` (A).
3. On `status == "EVENT"`, the per-type worker fires the callback (`_handle_type_b_event` / `_handle_type_c_event` / `_handle_type_d_event`). These callbacks register a **pending TTL entry** (`pending_*_events` dict, e.g. `pending_break_events` at line 672) on the detector, storing `trigger_time`, `save_at` (the TTL expiry), and `event_data`.
4. Every subsequent `add_sensor_data` call runs `_check_expired_ttls(timestamp)` at `event_detector.py:1048`. When `timestamp >= entry['save_at']`, the expiry loop (code around `event_detector.py:600-667`) extracts the `data_window_bytes` blob (via `_extract_data_window`), decorates it with `event_info` / type-keyed `bounds_*` JSON (lines 604-627), and enqueues the final `update_payload` dict into `worker_manager.update_queue` under `update_cv` (lines 631-644). Break events go through the parallel path at lines 669-721.
5. The update_worker batches up to 200 of these payloads and calls `self.update_db.batch_update_event_detection(batch)` (worker_manager.py:394).
6. Inside `batch_update_event_detection` (`mqtt_database.py:3060-3173`), each event first tries UPDATE against an existing row within 500 ms tolerance (the snapshot row, if the snapshot-insert path were enabled, or an earlier event row for the same sensor within tolerance). If no row matches, it falls back to `_insert_event_fallback(cursor, event)` (called at line 3135) which INSERTs a new `events` row with the full metadata including `data_window`.
7. After a successful batch, update_worker moves on to MQTT publishing (§4).

**The handoff point** — the single line where a detected-and-aged event crosses from the compute side to the persistence side — is:

- `event_detector.py:643` (for A/B/C/D events): `self.worker_manager.update_queue.append(update_payload)` under `update_cv` context manager at line 642.
- `event_detector.py:719` (for BREAK events): same pattern, under the break-events TTL loop.

Everything upstream is in-memory detection state on the producer side; everything downstream is DB + MQTT on the worker side.

## 11. File / line reference index

- `worker_manager.py:8` — class `GlobalWorkerManager`.
- `worker_manager.py:16-24` — DB connections (shared, snapshot, update).
- `worker_manager.py:26-43` — config knobs loaded from `app_config`.
- `worker_manager.py:46-61` — queue construction (deques + conditions).
- `worker_manager.py:70-72` — `set_event_detectors`.
- `worker_manager.py:74-109` — `apply_app_config_live`.
- `worker_manager.py:111-132` — `start`.
- `worker_manager.py:134-149` — `stop` (ineffective under daemon threads).
- `worker_manager.py:151-171` — `_type_a_worker`.
- `worker_manager.py:173-192` — `_type_b_worker`.
- `worker_manager.py:194-226` — `_process_type_b` (6/5/4-tuple unpack, T2 gate).
- `worker_manager.py:228-247` — `_type_c_worker`.
- `worker_manager.py:249-280` — `_process_type_c` (T3 gate).
- `worker_manager.py:282-301` — `_type_d_worker`.
- `worker_manager.py:303-339` — `_process_type_d` (7/6/5/4-tuple unpack, T4 gate, `avg_t3_from_c`).
- `worker_manager.py:341-372` — `_snapshot_worker` (drain-to-nowhere, insert call commented out line 363).
- `worker_manager.py:374-465` — `_update_worker` (batch UPDATE, retry, MQTT publish).
- `worker_manager.py:394` — DB write call.
- `worker_manager.py:396-405` — retry re-queue with max 5.
- `worker_manager.py:415-420` — event-type discovery (first-match wins).
- `worker_manager.py:435-440` — `find_event_id` call.
- `worker_manager.py:451-458` — `publish_event_mqtt` call.
- `event_detector.py:281-306` — producer-side local batch buffers and fallback queues.
- `event_detector.py:631-644` — update-queue enqueue for A/B/C/D TTL-ready events.
- `event_detector.py:707-720` — update-queue enqueue for break events.
- `event_detector.py:1053-1058` — snapshot-queue enqueue.
- `event_detector.py:1063-1069` — type_a_queue enqueue.
- `event_detector.py:1078-1094` — local_batch_b → type_b_queue flush.
- `event_detector.py:1100-1116` — local_batch_c → type_c_queue flush.
- `event_detector.py:1122-1143` — local_batch_d → type_d_queue flush (with `avg_t3_snapshot`).
- `event_detector.py:1203-1221` — `_queue_event_update` helper (currently unused by main flow).
- `event_detector.py:2260-2264` / `:2292-2296` / `:2324-2328` — stop-path flushes of local batches.
- `mqtt_database.py:2996-3058` — `insert_continuous_sensor_data_batch` (unused by worker today).
- `mqtt_database.py:3060-3173` — `batch_update_event_detection` (called from `_update_worker`).
- `mqtt_database.py:3118-3129` — the 500 ms UPDATE-match tolerance window.
- `mqtt_database.py:3135` — `_insert_event_fallback` path when no UPDATE row matches.
- `mqtt_database.py:3156-3163` — single-retry on "database is locked".
- `mqtt_database.py:3306-3353` — `insert_event_direct` (fallback path when worker_manager is None).
- `mqtt_database.py:3355-3385` — `find_event_id` (per-event-type column, 500 ms tolerance).
- `mqtt/client.py:140-185` — `publish_event_mqtt` (topic, payload, counters).
