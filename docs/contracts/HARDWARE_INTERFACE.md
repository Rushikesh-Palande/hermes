# HARDWARE_INTERFACE.md

## 1. Overview

HERMES is a real-time industrial sensor dashboard. A single STM32 microcontroller (logical `device_id=1`) publishes twelve ADC channels as JSON over MQTT to a local Mosquitto broker; the Flask/gunicorn server subscribes to `stm32/adc`, anchors device timestamps to Unix time, applies per-sensor offsets, feeds an `EventDetector`, and stores results in SQLite.

Direction summary:
- **Inbound** (STM32 → server): `stm32/adc` — sensor samples at ~123 Hz.
- **Outbound** (server → subscribers): `stm32/events/<device>/<sensor>/<TYPE>` — event triggers from the detector; and a legacy per-sensor telemetry fan-out under `{mqtt_base_topic}/device_<id>/sensor_<id>` (never called in production).

## 2. Broker connection

| Property | Value | Source |
|---|---|---|
| Default host | `localhost` | `web_server.py:137`, `src/app/services.py:37`, `src/database/mqtt_config.py:19` |
| Default port | `1883` | `web_server.py:138`, `services.py:38`, `mqtt_config.py:20` |
| Keepalive (web_server path) | `60 s` (overridable via DB key `mqtt_keepalive_s`) | `web_server.py:139`, `web_server.py:311`, `web_server.py:612` |
| Keepalive (src/mqtt path) | `300 s` (hardcoded) | `src/mqtt/client.py:103` |
| Default subscribe QoS | `0` (DB key `mqtt_qos`) | `web_server.py:140`, `web_server.py:261`, `web_server.py:613` |
| Auth | Anonymous unless `mqtt_username`/`mqtt_password` globals set | `web_server.py:144-145`, `web_server.py:252-253`, `src/mqtt/client.py:37-38` |
| TLS | **None** — `connect_async()` is called without TLS context | `web_server.py:311`, `src/mqtt/client.py:103` |
| Client ID | `f"stm32_dashboard_{int(time.time())}"` (generated each init) | `web_server.py:246`, `src/mqtt/client.py:35` |
| LWT / will | None set | — |
| Reconnection | Paho's built-in auto-reconnect triggered by `loop_start()` + `connect_async()`; no custom backoff or retry cap | `web_server.py:311-312`, `src/mqtt/client.py:103-104` |
| MQTT lib optional | Wrapped in `try/except ImportError` on `paho.mqtt.client` | `web_server.py:27-32`, `src/mqtt/client.py:7-12` |

**Production client owner**: `init_mqtt_client()` in `web_server.py:237-316`. Called from `wsgi.py:12` at process start.

**Secondary client owner**: `init_mqtt_client()` in `src/mqtt/client.py:25-110`. Called from `src/app/main.py:114` (only when running `python -m src.app.main`, i.e. not via `run.sh`/gunicorn).

Runtime broker/port/topic values are loaded from the SQLite `app_config` table via `_db_cfg()` (`web_server.py:589-617`), which overrides hard-coded defaults. Credentials come from the separate `mqtt_config` table (`src/database/mqtt_config.py:80-106`, seeded with `broker=localhost`, `port=1883`, `base_topic='canbus/sensors/data'`, `websocket_url='ws://localhost:9001/mqtt'`, `username=NULL`, `password=NULL`, `enabled=1`).

## 3. Inbound topics (hardware → dashboard)

### 3.1 `stm32/adc` (primary)

| Property | Value |
|---|---|
| Topic string | `"stm32/adc"` (constant, overridable via `app_config.stm32_adc_topic`) |
| Source constant | `STM32_ADC_TOPIC` at `web_server.py:48`, `src/mqtt/client.py:14` |
| Subscription | `client.subscribe(STM32_ADC_TOPIC, qos=mqtt_qos)` at `web_server.py:261`; `qos=0` hardcoded at `src/mqtt/client.py:45` |
| Payload format | JSON (UTF-8 decoded at `web_server.py:288`, `src/mqtt/client.py:66`) |
| Re-subscribe path | On topic/QoS change, `unsubscribe(old)` then `subscribe(new)` — `web_server.py:712-714` |

**JSON payload schema**:

| Field | Type | Required | Semantics |
|---|---|---|---|
| `device_id` | int | No | Logical device (default `STM32_DEVICE_ID = 1`, `src/app/services.py:52`, `web_server.py:955`). `web_server.py:187, 292` |
| `ts` | int (ms) | No | STM32 free-running hardware millisecond counter. Not Unix ms — anchored on first message. `web_server.py:192-208`, `src/mqtt/client.py:68-82` |
| `adc1` | array of 6 numbers | Yes (effectively) | Maps to sensors 1..6. Truncated to 6 via `[:6]` (`parser.py:13`, inline at `web_server.py:83`) |
| `adc2` | array of 6 numbers | Yes (effectively) | Maps to sensors 7..12. Truncated to 6 via `[:6]` |

**Sensor ID mapping** (`src/mqtt/parser.py:4-21`):

```python
adc1 = payload.get('adc1', [])[:6]
adc2 = payload.get('adc2', [])[:6]
for i, raw in enumerate(adc1, start=1):  # sensors 1..6
    sensor_values[i] = float(raw)
for i, raw in enumerate(adc2, start=7):  # sensors 7..12
    sensor_values[i] = float(raw)
```

**Edge cases (observed code behavior, no opinions)**:

- **Missing `device_id`**: defaults to `STM32_DEVICE_ID=1` (`web_server.py:187, 292`). In `src/mqtt/client.py:88`, `device_id` is **always** `services.STM32_DEVICE_ID` — the payload's `device_id` is **ignored** on that path.
- **Missing `ts`**: `ts = receive_ts` (wall-clock at callback) — `web_server.py:210`, `src/mqtt/client.py:84`.
- **Truncated `adc1`/`adc2`**: any array shorter than 6 results in fewer entries; `sensor_values` dict may have <12 keys. In `web_server.py`'s inline parser (`web_server.py:76-89`), the index is `len(sensor_values)+1`, so if `adc1=[v1,v2]`, then `adc2[0]` populates sensor **3**, not 7 — i.e. the inline parser packs contiguously. In `src/mqtt/parser.py:4-21` with `enumerate(..., start=7)`, `adc2` always starts at sensor 7 regardless of `adc1` length. Live runtime uses `src/mqtt/parser.py` (`web_server.py:174: from src.mqtt.parser import parse_stm32_adc_payload`).
- **Non-numeric adc values**: `float(raw)` raises → caught by the outer `try/except Exception` in the consumer (`web_server.py:230-233`), increments `mqtt_total_errors` and continues; the sample is lost.
- **Empty `sensor_values`** (both arrays missing or empty): consumer does `if not sensor_values: continue` (`web_server.py:214-215`, `src/mqtt/client.py:86-87`).
- **Extra fields** in payload: silently ignored (dict `.get()`).
- **Non-JSON payload**: `json.loads()` raises → caught, stats error counter bumped, message dropped (`web_server.py:301-304`).

**Two parsers differ**:

| Behavior | `web_server.py:76-89` inline | `src/mqtt/parser.py:4-21` (active) |
|---|---|---|
| Sensor numbering | `len(sensor_values)+1` (contiguous) | Fixed `start=1` / `start=7` |
| Used at runtime | No — shadowed by import on line 174 | Yes — imported inside consumer |

### 3.2 Other subscribed topics

None. A repo-wide search for `.subscribe(` returns only the single `STM32_ADC_TOPIC` subscription in both MQTT paths (`web_server.py:261, 714`; `src/mqtt/client.py:45`).

## 4. Timestamp handling (CRITICAL)

### 4.1 web_server.py path (production)

**State**: `stm32_ts_offsets: dict` keyed by `device_id_str`, storing `server_time - device_time` seconds (`web_server.py:156`). Per-device, not global.

**Algorithm** (`web_server.py:190-210`):

```python
dev_ts_ms = payload.get('ts')
if dev_ts_ms is not None:
    dev_ts_sec = dev_ts_ms / 1000.0
    if device_id_str not in stm32_ts_offsets:
        stm32_ts_offsets[device_id_str] = receive_ts - dev_ts_sec
    ts = dev_ts_sec + stm32_ts_offsets[device_id_str]
    if abs(ts - receive_ts) > TIMESTAMP_DRIFT_THRESHOLD_S:
        stm32_ts_offsets[device_id_str] = receive_ts - dev_ts_sec
        ts = receive_ts
        # "Timestamp drift detected... re-anchored"
else:
    ts = receive_ts
```

- **Anchor**: first message for a given `device_id_str` sets `offset = receive_ts - dev_ts_sec`.
- **Re-anchor threshold**: `TIMESTAMP_DRIFT_THRESHOLD_S` — default `5.0` seconds, loaded from DB key `timestamp_drift_threshold_s` (`web_server.py:608`). On breach, offset is reset to current wall clock and `ts` is set to `receive_ts` for that sample.
- **Fallback when `ts` missing**: `ts = receive_ts` (wall-clock seconds).
- **Downstream units**: `ts` is **float seconds (Unix epoch)**. Passed as-is into `EventDetector.add_sensor_data(sensor_values, ts)` (`web_server.py:224`) and `LiveDataHub.add_snapshot(device_id_str, ts, sensor_values)` (`web_server.py:226`). `LiveDataHub` enforces monotonicity by bumping identical/older timestamps by `0.000001` s (`web_server.py:455-457`).

### 4.2 src/mqtt/client.py path (secondary)

**State**: module-level `_stm32_anchor: dict` (`src/mqtt/client.py:20`) — a **single** `{'unix': ..., 'stm32': ...}` mapping, **not keyed by device**. Cleared on every (re)connect (`src/mqtt/client.py:44`).

**Algorithm** (`src/mqtt/client.py:68-84`):

```python
stm32_ts_ms = payload.get('ts')
if stm32_ts_ms is not None:
    if 'unix' not in _stm32_anchor:
        _stm32_anchor['unix'] = recv_time
        _stm32_anchor['stm32'] = stm32_ts_ms
    ts = _stm32_anchor['unix'] + (stm32_ts_ms - _stm32_anchor['stm32']) / 1000.0
    if abs(ts - recv_time) > 5.0:  # hardcoded 5s
        _stm32_anchor['unix'] = recv_time
        _stm32_anchor['stm32'] = stm32_ts_ms
        ts = recv_time
else:
    ts = recv_time
```

- Drift threshold hardcoded `5.0 s` (not DB-tunable).
- Anchor cleared on reconnect (`_stm32_anchor.clear()` at `on_connect` line 44) — the web_server path does **not** clear its per-device offsets on reconnect.
- Single-anchor design: cannot support multiple devices correctly.

## 5. Sensor offsets

**Storage**: SQLite `sensor_offsets` table (`device_id`, `sensor_id`, `offset`, `updated_at`), read via:
- `MQTTDatabase.get_all_offsets_for_cache()` — returns `{device_id_str: {sensor_id: offset}}` for all devices with at least one non-zero offset (`src/database/mqtt_database.py:2713-2734`).
- `MQTTDatabase.get_all_sensor_offsets(device_id)` — per-device reload, missing rows default to `0.0` (`src/database/mqtt_database.py:2669-2687`).

**In-memory cache**: `_sensor_offsets: dict` in `web_server.py:725`. Populated at startup by `_load_sensor_offsets()` (`web_server.py:727-733`), called at `web_server.py:915`. Per-device refresh via `_refresh_sensor_offsets(device_id)` (`web_server.py:735-747`), exposed as `services.refresh_offsets` (`web_server.py:749`).

**Refresh trigger**: `src/app/routes/offsets.py:36-37, 56-57, 70-71` call `services.refresh_offsets(device_id)` after UI save / delete operations, so the in-memory cache is updated without restart.

**Application point**: Inside the MQTT consumer, per sample, **after** parsing and **before** feeding the detector/live hub (`web_server.py:217-220`):

```python
_offsets = _sensor_offsets.get(device_id_str)
if _offsets:
    sensor_values = {sid: val - _offsets.get(sid, 0.0) for sid, val in sensor_values.items()}
```

**Semantics**: `corrected = raw - offset`. If no entry exists for `device_id_str` in the cache (no non-zero offsets), no correction is applied (fast path — dict lookup short-circuits).

**Note**: The `src/mqtt/client.py` secondary path does **not** apply sensor offsets at all (no reference to `_sensor_offsets` in that module).

## 6. Outbound topics (dashboard → subscribers)

### 6.1 `stm32/events/<device_id>/<sensor_id>/<EVENT_TYPE>`

Template (`src/mqtt/client.py:160-167`):

```python
base_topic = "stm32/events"   # hardcoded literal, not from app_config
event_type_label = str(event_type).upper()
topic = f"{base_topic}/{device_id}/{sensor_id}/{event_type_label}"
```

| Element | Value |
|---|---|
| `base_topic` | `"stm32/events"` — **hardcoded** in `publish_event_mqtt`, not sourced from `services.mqtt_base_topic` |
| `device_id` | int, from the event record |
| `sensor_id` | int, 1..12 |
| `EVENT_TYPE` | Uppercased via `str(event_type).upper()`. Known values feeding in: `'a'`, `'b'`, `'c'`, `'d'`, `'break'` → published as `A`, `B`, `C`, `D`, `BREAK` |
| QoS | `0` (hardcoded, `src/mqtt/client.py:174`) |
| Retain | Not set (defaults to `False`) |

**Payload** (`src/mqtt/client.py:169-172`):

```python
{
  "timestamp": "YYYY-MM-DD HH:MM:SS.mmm",   # local time via datetime.fromtimestamp + format truncated to ms
  "sensor_value": float | None              # None → serialised as null; else float rounded via float(...) cast
}
```

Note: the human-readable string `event_time` uses `datetime.fromtimestamp(timestamp)` (local tz) and truncates to milliseconds via `[:-3]`. If `sensor_value is None`, both the formatted log label (`"NA"` at line 163) and the JSON field are `None`; otherwise JSON field is `float(sensor_value)`.

**Publish sites** (two distinct code paths, both publish per event):

1. **Detector TTL-direct path** (`src/detection/event_detector.py:647-665`) — fallback synchronous path when `worker_manager` is absent. Publishes immediately after `insert_event_direct()`.
2. **Worker-batch path** (`src/workers/worker_manager.py:407-460`) — the normal production path. After the update worker persists a batch, it iterates, resolves `event_type` from `event_flags` (first match of `a|b|c|d|break`), looks up `event_id` via `find_event_id`, derives `sensor_value` from `sensor_snapshot[sensor_id-1]` (or `average` fallback), and calls `publish_event_mqtt()`. Events flagged `publish_immediate` (the detector already published) are skipped (line 411-414).

If `services.mqtt_client is None` when `publish_event_mqtt` is invoked, it lazily calls `init_mqtt_client()` (`src/mqtt/client.py:145-148`). Publishing is guarded by `services.mqtt_enabled && services.mqtt_client && services.mqtt_global_enabled` (`src/mqtt/client.py:149`).

Stats: on successful publish, `services.event_total_published` and `services.event_published_by_device[str(device_id)]` are incremented under `services.event_stats_lock` (`src/mqtt/client.py:175-181`).

### 6.2 Per-sensor telemetry (legacy `publish_sensor_data_mqtt`)

Defined in two places, identical logic:

- `web_server.py:318-349`
- `src/mqtt/client.py:113-137`

**Topic template**:

```python
if device_id is not None:
    topic = f"{mqtt_base_topic}/device_{device_id}/sensor_{sensor_id}"
else:
    topic = f"{mqtt_base_topic}/sensor_{sensor_id}"
```

| Element | Value |
|---|---|
| `mqtt_base_topic` (web_server.py default) | `"stm32/sensors/data"` (`web_server.py:142`, `services.py:39`) |
| `mqtt_base_topic` (DB seed / MQTTConfigDB) | `"canbus/sensors/data"` (`src/database/mqtt_config.py:21,54,69`) |
| Effective runtime | DB `app_config.mqtt_base_topic` overrides via `_db_cfg` (`web_server.py:615`) |
| QoS | `0` |
| Retain | Not set |

**Payload**:

```python
{
  "timestamp": timestamp or int(time.time() * 1000),   # ms since epoch
  "value": float(value),
  "sensor_id": sensor_id,
  "device_id": device_id
}
```

**Cadence / call sites**: A repo-wide grep for `publish_sensor_data_mqtt(` outside its two definitions and the `__init__.py` re-export returns **zero call sites** in production code. It is exported in `src/mqtt/__init__.py:2,4` but never invoked by the dashboard or detector. This path is legacy.

### 6.3 Other published topics

Grep for `.publish(` in production source:
- `web_server.py:339` — `publish_sensor_data_mqtt` (legacy, unused).
- `src/mqtt/client.py:134` — `publish_sensor_data_mqtt` (legacy, unused).
- `src/mqtt/client.py:174` — `publish_event_mqtt` (production outbound).

Test/simulator tooling (not part of the server) publishes to `stm32/adc` as a producer: `control_s10.py:53`, `test_comprehensive.py:46`, `mqtt_test_publisher.py`, `validate_realtime.sh`, `trigger_s10_demo.sh`, `test_all_sensors_mqtt.sh`, `trigger_s10_events.md`.

## 7. Two parallel MQTT paths

| Aspect | `web_server.py` path | `src/mqtt/client.py` path |
|---|---|---|
| Runs under `run.sh` / `wsgi.py` / gunicorn production | **Yes** (`wsgi.py:8-12` → `web_server.init_mqtt_client()`) | No |
| Runs under `python -m src.app.main` | No | **Yes** (`src/app/main.py:6,114`) |
| Client init function | `web_server.py:237-316` | `src/mqtt/client.py:25-110` |
| Keepalive | `60 s` (DB tunable) | `300 s` (hardcoded) |
| Parser used | `src/mqtt/parser.py:parse_stm32_adc_payload` (imported at `web_server.py:174`) | Same `src/mqtt/parser.py:parse_stm32_adc_payload` |
| `device_id` source | `payload.get('device_id', 1)` — honours payload | **Ignored**; always `services.STM32_DEVICE_ID` |
| Timestamp offsets | Per-device dict `stm32_ts_offsets`, preserved across reconnects | Single global `_stm32_anchor`, cleared on every `on_connect` |
| Drift threshold | DB-tunable `TIMESTAMP_DRIFT_THRESHOLD_S` (default 5.0) | Hardcoded 5.0 |
| Sensor offsets applied | **Yes** (`web_server.py:217-220`) | **No** |
| Threading model | Ultra-light `on_message` → `SimpleQueue.put` → dedicated `mqtt_data_consumer` daemon (`web_server.py:166-235, 265-268`) | All work inline in `on_message` callback |
| Stats | `mqtt_total_rx/tx/errors`, `mqtt_rx_by_device` under `mqtt_stats_lock` (`web_server.py:147-153, 294-296`) | Only `_message_count` / `_last_message_time` module globals (`src/mqtt/client.py:21-22, 56-60`) |
| Publishing | Defines its own `publish_sensor_data_mqtt`; event publishing still delegates to `src/mqtt/client.publish_event_mqtt` (see `event_detector.py:657`, `worker_manager.py:408`) | Defines `publish_sensor_data_mqtt` and `publish_event_mqtt` |

Per CLAUDE.md (lines 281-283): "The active runtime MQTT path is `mqtt_data_consumer()` in `web_server.py`, which owns `stm32_ts_offsets` for timestamp anchoring. `src/mqtt/client.py` is a secondary path not called by the live server."

## 8. Known edge cases / quirks

- **`adc1` length < 6 in inline parser**: `web_server.py:76-89` would shift `adc2` sensor numbering (contiguous), but this parser is **shadowed** at runtime by the import at `web_server.py:174`, which uses `src/mqtt/parser.py`'s fixed-start enumeration. With the active parser, missing tail values in `adc1` yield fewer keys (1..N) and `adc2` still starts at 7.
- **Non-JSON payload**: caught in `on_message` try/except, increments `mqtt_total_errors`, message dropped (`web_server.py:301-304`).
- **`float()` failure on non-numeric element**: raises inside the consumer, caught by the consumer's outer `try/except`; the entire sample is dropped (`web_server.py:230-233`).
- **Broker disconnect**: `on_disconnect` sets `mqtt_enabled = False` and `services.mqtt_enabled = False` (`web_server.py:273-277`, `src/mqtt/client.py:50-52`). No explicit reconnect loop — Paho's `loop_start` background thread handles reconnection automatically. Secondary path clears `_stm32_anchor` on successful `on_connect` re-fire; primary path does not.
- **Queue backpressure**: `mqtt_data_queue = SimpleQueue()` is **unbounded** (`web_server.py:162`). No dropping inside the queue; if the consumer stalls, memory grows. Consumer loop uses `get(timeout=0.1)` to allow clean shutdown (`web_server.py:180-182`).
- **LiveDataHub monotonicity**: any `timestamp <= last_ts` is bumped to `last_ts + 0.000001` before storage (`web_server.py:453-457`). Consumers therefore never see repeated or out-of-order timestamps in the live buffer.
- **Lazy client init in publish path**: `publish_event_mqtt` calls `init_mqtt_client()` if `services.mqtt_client is None` (`src/mqtt/client.py:145-148`). This means under `src/app/main.py` runtime, event publishing uses the **secondary** client; under gunicorn/`wsgi.py`, `services.mqtt_client` is already populated by the primary `init_mqtt_client` (`web_server.py:247: services.mqtt_client = mqtt_client`), so the lazy init is a no-op.
- **Subscribe/resubscribe on config change**: changes to `stm32_adc_topic` or `mqtt_qos` trigger `unsubscribe(old_topic)` then `subscribe(new_topic, qos=new_qos)` live without reconnect (`web_server.py:711-715`). Broker/port/keepalive/websocket_url changes trigger a full `init_mqtt_client()` reconnect (`web_server.py:706-710`).
- **EventDetector data-gap reset**: configurable `data_gap_reset_s` (default 2.0 s, `src/detection/event_detector.py:87`) — when samples are paused longer than this, detector resets windows. Not an MQTT layer behavior but triggered by ingestion gaps.

## 9. Sample rates

| Source | Value | Usage |
|---|---|---|
| `EventDetector.SAMPLE_RATE_HZ` | `100` (class default; overridable via `app_config.sample_rate_hz`) | Buffer sizing, Type A window sample counts (`event_detector.py:41-43, 74, 84-85`) |
| `AvgTypeB/C/D` etc. via `sample_rate` param | Inherited from `EventDetector.SAMPLE_RATE_HZ` (`event_detector.py:203, 220, 237`) | Averaging window allocations |
| `Type A` event module constant | `SAMPLE_RATE_HZ = 100` (`src/events/event_a.py:6`) | Type A algorithm internals |
| `Modbus TCP default` | `DEFAULT_SAMPLE_RATE_HZ = 100`, min 1, max 1000 (`src/modbus/modbus_tcp_device.py:43-45`) | Legacy Modbus polling (unrelated to MQTT) |
| Observed STM32 rate | `~123 Hz` per CLAUDE.md:244 and `web_server.py:66` comment (`LIVE_BUFFER_MAX_SAMPLES = 2500 # 20 seconds @ 123 Hz`) | Actual hardware emission rate — drives real throughput |
| `LIVE_BUFFER_MAX_SAMPLES` | `2500` default, overridable env+DB | UI ring buffer depth (`web_server.py:66, 593`) |

CLAUDE.md notes (line 224): "the actual STM32 hardware emits ~123 Hz — the code constant is a conservative buffer-sizing value." The ingestion pipeline itself is rate-agnostic — it processes whatever messages arrive.

## 10. File/line references (index)

- Inbound topic constant: `web_server.py:48`, `src/mqtt/client.py:14`, `src/database/mqtt_database.py:216` (app_config seed row)
- Primary MQTT init: `web_server.py:237-316`
- Secondary MQTT init: `src/mqtt/client.py:25-110`
- Primary `on_message` (enqueue): `web_server.py:279-304`
- Consumer thread: `web_server.py:166-235`
- Parser (active): `src/mqtt/parser.py:4-21`
- Parser (inline shadowed): `web_server.py:76-89`
- Timestamp offsets (web_server): `web_server.py:156, 190-210`, drift constant at `web_server.py:608`
- Timestamp anchor (src/mqtt): `src/mqtt/client.py:20, 44, 68-82`
- Sensor offset cache: `web_server.py:725, 727-747, 915`, applied at `web_server.py:217-220`
- Offset DB: `src/database/mqtt_database.py:2669-2734`
- Offset refresh routes: `src/app/routes/offsets.py:36-71`
- Event publish (topic template): `src/mqtt/client.py:140-185`
- Event publish from detector: `src/detection/event_detector.py:657-665`
- Event publish from worker: `src/workers/worker_manager.py:407-460`
- Legacy sensor telemetry publish: `web_server.py:318-349`, `src/mqtt/client.py:113-137`
- Broker config DB: `src/database/mqtt_config.py` (all), defaults at lines 18-26
- STM32 device constants: `src/app/services.py:52-53`, `web_server.py:955-956`
- Production entry: `run.sh:52-62`, `wsgi.py:8-18`
- Secondary entry: `src/app/main.py:114`
- Live runtime sample rate: `src/detection/event_detector.py:41`, CLAUDE.md:224
- LiveDataHub monotonicity: `web_server.py:430-468` (esp. 453-457)
- Config-live apply (MQTT reconnect/resubscribe): `web_server.py:621-717`
