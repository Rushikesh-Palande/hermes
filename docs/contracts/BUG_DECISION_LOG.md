# BUG_DECISION_LOG.md

Every finding from `CODEBASE_AUDIT.md` (40 issues across 18 sections) is classified here with an explicit rewrite decision:

- **FIX** — the rewrite must not reproduce this behaviour. It's a real defect with no downstream dependency. Flag in release notes.
- **PRESERVE** — the observed behaviour, even if non-ideal, is something hardware or downstream consumers depend on. The rewrite must reproduce it bit-for-bit.
- **FIX+FLAG** — fix the behaviour, but the change is user-visible. Requires a migration note, a feature flag, and an explicit cutover plan.

Each entry has a short rationale so future maintainers can challenge the decision without re-reading the full audit.

## Severity key

- **C** — critical (crashes, data loss, silent incorrectness)
- **H** — high (user-visible wrong behaviour, not crashing)
- **M** — medium (performance, maintainability, edge cases)
- **L** — low (cleanliness, dead code)

## Decisions

### Critical (C)

| # | Finding | Decision | Rationale |
|---|---|---|---|
| 1 | `web_server.py:1913` — `EventDetector(database=device.db, ...)` crashes because `device` is `None` when starting a non-STM32 device via `/api/devices/<id>/start`. | **FIX** | Real crash. Only STM32 is ever started in production today, so no downstream relies on the crash behaviour. Rewrite: typed storage layer + constructor that requires an explicit `storage: StorageBackend` parameter; impossible to pass None. |
| 2 | `web_server.py:2263` — `save_type_a_config` silently resets `ttl_seconds` and `debounce_seconds` to DB defaults on every save. | **FIX** | Bug. UI users are losing their settings invisibly. Rewrite: each config endpoint accepts a full typed payload (pydantic); unset fields preserve prior values via an explicit `PATCH` semantics rather than positional defaults. |
| 3 | `event_detector.py:187` — Type D constructor default `upper_threshold=40.0` (should be `60.0` to match `lower_threshold`). First-install DB has `lower==upper` → band always fails. | **FIX** | Typo. No downstream relies on it (everyone has overridden via UI by the time D fires). Rewrite: eliminate silently-asymmetric defaults — Type D tolerance is a single `tolerance_pct` field. |
| 4 | `event_detector.py:1470 + 1042` — double-lock race between `buffer_lock` and `variance_lock` over `sensor_variance_state`. Variance can momentarily glitch to 0 or infinity. | **FIX** | Data-integrity bug. Rewrite: single lock per sensor state, or use a lock-free ring buffer of (ts, value) pairs and compute variance from that. |
| 5 | `mqtt_database.py:3060-3143` — 500 ms dedup merges different event types into the same row. The later writer's `data_window` BLOB overwrites the earlier writer's. Data loss when A/B/C/D fire within 500 ms of each other on the same sensor. | **FIX+FLAG** | Real data loss in edge cases, but any downstream consumer querying the old events table depends on the "one row, multiple flags" representation. Rewrite: **separate rows per event type** (tall/narrow schema in `DATABASE_CONTRACT.md`'s successor). Migration preserves old rows verbatim, new rows are never coalesced. Document in API changelog that an event window in v1 that combined A+B is exposed as two events in v2. |

### High (H)

| # | Finding | Decision | Rationale |
|---|---|---|---|
| 6 | Dual entry points: `web_server.py` and `src/app/__init__.py` + `src/app/main.py`. Most divergences flow from this. | **FIX** | Architectural debt. Rewrite has one entry path (`services/api/main.py` + `services/ingest/__main__.py`), no duplication possible. |
| 7 | `event_detectors` dict uses mixed int/str keys across `web_server.py:1770/1914` and `web_server.py:989`. Devices started via one path can't be found via the other. | **FIX** | Confined bug; no downstream depends on mixed keys. Rewrite: storage layer exposes typed `DeviceId` (int); all detector registries are keyed by `DeviceId`. |
| 8 | `web_server.py:1738-1774 initialize_device_api` branches on removed `device_type` column → `KeyError` on any non-legacy device. | **FIX** | Crash. Modbus path is legacy and will be removed in the rewrite; the MQTT-only initialize flow becomes the only path. |
| 9 | Type A aggregated debounce uses EARLIEST crossing across sensors for the event `save_timestamp` — wrong for per-sensor events that crossed later. | **FIX** | Spec violation per `docs/events/EVENT_A.md` ("fires at original crossing time"). Rewrite: per-sensor debounce start, stored in `sensor_variance_state[sid]`, used verbatim as save_timestamp for that sensor's event. |
| 10 | `event_detector.py:1126-1131` — Type D reads `type_c_detectors[sid].current_avg` without holding Type C's lock. Type D only fires when Type C is also enabled (undocumented coupling). | **FIX** | Undocumented dependency and lock-free cross-reads. Rewrite: Type D computes its own `avg_T3`-equivalent from the same sample stream; no dependency on Type C's enabled state. |
| 11 | `device_detail.html:4099` — UI uses response key `configs` but API returns `configs_by_device`. B-band reconstruction for legacy BLOBs silently fails. | **FIX** | UI bug. New UI is built from scratch against an OpenAPI-typed client, so this class of drift can't happen. |
| 12 | `device_detail.html:4326-4333` — Type D dynamic band drawn relative to a T5-rolling-avg of raw samples, not detector's `avg_T5` baseline. UI shows a different quantity than the detector used. | **FIX** | UI bug. New UI queries the detector's `avg_T5` directly via the event metadata; no re-implementation in JS. |
| 13 | `mqtt_database.py:1493 update_device` silently drops unknown fields. `sample_rate_hz` passed in from `start_device_api` is never persisted. | **FIX** | Silent data loss. Rewrite: typed `UpdateDeviceRequest` pydantic model; unknown fields are a 422 validation error, not silently ignored. |
| 14 | Per-sensor `enabled` flag stored in DB (`event_config_type_*_per_sensor.enabled`) but ignored by detection logic. UI shows a toggle that does nothing. | **FIX** | Feature lie. Rewrite: `detection_config.enabled` is checked per-sensor at every detection call (`EventDetector.process_sample`). |
| 15 | `worker_manager.py:418-422` — when multiple event types fire on the same row, only the first-matched type (ordered a,b,c,d,break) publishes to MQTT. Others are silently invisible. | **FIX** | Data loss on external MQTT subscribers. Rewrite: separate rows per type means every event publishes its own MQTT message. |
| 16 | `device_detail.html:5490, 5656, 7499, 7588` — UI calls `/api/db/frames`, `/api/frames/grids`, `/api/frames/export` which don't exist. CAN-era leftovers. | **FIX** | Dead UI paths. New UI doesn't call them. Endpoints deleted. |

### Medium (M)

| # | Finding | Decision | Rationale |
|---|---|---|---|
| 17 | `mqtt_database.py:1436` — `get_device` / `get_all_devices` / `get_events` / `find_event_id` open a new sqlite connection per call, bypassing `self.lock`. | **FIX** | Performance bug. Rewrite: single connection pool (SQLAlchemy), consistent lock/transaction semantics. |
| 18 | `mqtt_database.py:1919, 2118` — Type C/D `max(upper, lower+0.01)` silently rewrites user-provided upper threshold. UI round-trip lies. | **FIX+FLAG** | Silent data mutation. Rewrite: if `upper <= lower`, 422 validation error. Flag in migration notes: users who relied on the quiet rewrite will get an explicit error instead. |
| 19 | `event_config.html:3068` — UI shows per-sensor TTL inputs for Types B/C/D, but DB only stores a single global TTL per type. Round-trip fiction. | **FIX** | UI lie → spec becomes real. Rewrite: TTL is per-`(device, sensor, event_type)` in `detection_config.params['ttl_s']`. |
| 20 | `avg_type_b.py:145`, `avg_type_c.py:142`, `avg_type_d.py:156` — hardcoded `gap > 2.0` instead of sourcing from `data_gap_reset_s` app_config. | **FIX** | Config surface is lying. Rewrite: detection modules receive the gap threshold in their constructor; no hardcoded literal. |
| 21 | `event_detector.py:1082, 1104, 1131` — worker task tuples contain a live reference to per-sensor detectors dict; not a snapshot. Reset-during-batch can corrupt worker view. | **FIX** | Concurrency bug. Rewrite: workers receive plain data (sample values + per-sensor parameters), not live detector objects. |
| 22 | `constants.py` + `app_config ref_value` — `ref_value` UI knob has NO runtime effect (hardcoded 100.0 in `avg_type_b.py`, `avg_type_d.py`). | **FIX** | Config surface lie. Rewrite: tolerance formula uses per-sensor `ref_value` from `detection_config.params`; default 100 but editable and actually read at runtime. |
| 23 | `web_server.py:1286` — `update_mqtt_config` updates web_server globals but not all `services.*` mirrors; blueprints see stale values. | **FIX** | Confined to the dual-state-containers problem — gone in rewrite. |
| 24 | `event_detector.py:669-734` — BREAK events are outside the A/B/C/D priority system; D cannot preempt a pending BREAK. | **PRESERVE** | This is the documented BREAK semantic per `docs/events/` specs. Mode transitions are a separate concept from detection events. Keep. |
| 25 | `mqtt_database.py:2099-2101` — `_pending_break_queue` uses list + `pop(0)` (O(n)) instead of deque. | **FIX** | Trivial. Rewrite uses `collections.deque`. |
| 26 | `event_detector.py:800-805` — Rule 3b (late-arriving worker detection guard) uses strict `<` on TTL. An event exactly at `last_ts + ttl` is dropped. | **FIX** | Boundary bug. Rewrite: `<=` with documented semantics. |
| 27 | `device_detail.html:7256-7271` — O(N×M) rolling CV computation per newly-added row. Quadratic cost. | **FIX** | UI perf bug. Rewrite computes rolling stats in a single pass on the server and ships them in the SSE/WebSocket payload. |
| 28 | `src/mqtt/client.py:74` — single-device `_stm32_anchor` dict; breaks with multiple MQTT devices. | **FIX** | Architectural limit. Rewrite: per-device anchor keyed by `DeviceId`. |

### Low (L)

| # | Finding | Decision | Rationale |
|---|---|---|---|
| 29 | `device_detail.html:6297` — global `console.log/warn/error` silenced in prod. Breaks dev-tools debugging. | **FIX** | Rewrite's UI uses a proper logging library with runtime-toggle levels; never silences the browser console. |
| 30 | `src/utils/event_logger.py:35` — `ENABLED=False`, entire log rotation/reconfigure machinery is dead. | **FIX** | Delete. Rewrite uses stdlib `logging.handlers.RotatingFileHandler` wired at app startup. |
| 31 | `worker_manager.py:194-226` — 4/5-tuple legacy branches are dead. | **FIX** | Trivial cleanup. Typed dataclass `WorkerTask` in rewrite — no length-polymorphism. |
| 32 | `web_server.py:318-349` — `publish_sensor_data_mqtt` never called. | **FIX** | Delete. Per-sensor telemetry is not a product requirement. |
| 33 | `device_manager.py:171-192` — `ProtocolManager` CAN/Modbus artifact, fully unused. | **FIX** | Delete the whole module. |
| 34 | `event_detector.py:1191-1201` — `start_snapshot_writer_thread` / `stop_snapshot_writer_thread` are no-op stubs. | **FIX** | Delete. |
| 35 | `web_server.py:997` — `get_device()` returns `None` (dead stub). | **FIX** | Delete. |
| 36 | `event_config.html:3144` — Post-bulk-save second POST triggers double-reload and race. | **FIX** | UI rewrite: single atomic config save via one endpoint; idempotent. |
| 37 | `avg_type_d.py:299` — `avg_t4_buffer` maxlen smaller than per-second advancement loop can traverse after long idle. Silent data loss of per-second averages. | **FIX** | Spec violation. Rewrite: buffer sized by `T5_seconds + safety_margin`, computed from config not hardcoded. |
| 38 | `event_detector.py:1062-1069` — manual `drop_count` + `deque(maxlen)` double-bookkeeping. | **FIX** | Rewrite: single counter, bounded queue with proper metric (`hermes_ingest_drops_total{reason}`). |
| 39 | `event_detector.py:1253` — `_a_debounce_start = {}` full clear on any config reload. | **FIX** | Rewrite: only clear per-sensor debounce state for sensors whose Type A config actually changed. |
| 40 | `device_detail.html:4338-4344` — `Math.min(...largeArray)` — call-stack overflow risk. | **FIX** | UI rewrite uses `reduce()`; no spread over unbounded arrays. |

## Behaviours that are PRESERVED

Only one audit finding is explicitly preserved (#24 — BREAK's position outside the priority system) because it is a spec-level design choice. Everything else is a fix.

Behaviours NOT in the audit that the rewrite must still preserve (call these out explicitly):

- **Timestamp anchor algorithm** (§4 of `INGESTION_PIPELINE.md`): first-sample-anchor + 5-second drift re-anchor is the behavioural contract the hardware firmware targets. Reproduce exactly.
- **Sensor offset direction**: `corrected = raw - offset`. Preserve the sign. Confirmed in `web_server.py:218`.
- **Hold-last-value for missing sensors** in `LiveDataHub` (§7.2 of `INGESTION_PIPELINE.md`): chose `src/app/live_data.py`'s "hold last" semantics over `web_server.py`'s `None`-insertion. `None` insertion was the production behaviour but is a UI-damaging bug; hold-last is what the refactor was trying to become. Fix is explicit and flagged.
- **Debounce "original crossing timestamp"** rule (per `EVENT_DETECTION_CONTRACT.md` §3.4, §4.3, §5.3, §6.3): events fire stamped at the first crossing, not the fire moment. Preserve.
- **TTL two-phase semantics**: TTL wait + 9s post-window hold. Preserve per `EVENT_DETECTION_CONTRACT.md` §8.
- **Priority hierarchy** D > C > B > A with BREAK outside. Preserve.
- **18-second event window** (9 s pre + 9 s post trigger). Preserve, but parameterize (`event_pre_window_s`, `event_post_window_s`).
- **MQTT topic shapes** (`stm32/adc` inbound, `stm32/events/<device>/<sensor>/<TYPE>` outbound). Preserve exactly so existing hardware firmware and any external subscribers continue working.

## Behaviours requiring FIX+FLAG (user-visible changes)

Any user who upgrades from v1 to the rewrite will see differences in these four areas. They need migration notes:

1. **Event rows separate by type** (was: one row with multiple `'Yes'` flags; becomes: one row per event type). Affects any external consumer reading the `events` table directly. Breaks the v1 500 ms coalescing behaviour.
2. **Tight C/D threshold validation** (was: silently bumped `upper = lower + 0.01`; becomes: 422 error). Users who saved nonsensical configs via the API will now get validation failures.
3. **Config fields that actually do what they say** (`ref_value`, per-sensor `enabled`, per-sensor TTL, `stm32_device_name`, `total_sensors` — §5 of `CONFIG_CATALOG.md`). A user who had "wrong" settings survived only because the field was broken; in v2 the field works and their setting takes effect.
4. **Per-sensor enabled flag respected** — sensors that the DB marked `enabled=False` but were still being detected will stop producing events in v2. Users must audit their per-sensor configs before upgrade.

These four are documented in the release's migration guide with a pre-upgrade audit script (`hermesctl audit-config`) that flags rows affected by each change.

## Numerical summary

| Decision | Count |
|---|---|
| FIX | 36 |
| FIX+FLAG | 3 (findings #5, #18, plus #14 flagged in the FIX+FLAG section) |
| PRESERVE | 1 (finding #24 — BREAK priority) |
| Total | 40 |

Of the fixes, **5 are Critical**, **11 are High**, **12 are Medium**, **12 are Low**. Only #24 is preserved.

## Per-phase fix scheduling

Not all fixes land at once. Proposed sequencing against the migration plan:

- **Phase 0** (hygiene, week 1): fix #1, #2, #3, #13, #32, #35 in-place on the legacy branch (these are real bugs users hit today; minimal-risk patches).
- **Phase 1** (repo reshape): fix #6, #7, #23 by collapsing entry points. Fix #30, #33, #34 by deletion.
- **Phase 2** (storage): fix #5, #17, #18, #22, #37 as part of the new schema. Fix #15 follows from separate-rows.
- **Phase 3** (ingest/API split): fix #8, #28, #38 in new ingest. Fix #4, #21 in new detector. Fix #9, #10, #39 in detection rewrite.
- **Phase 4** (frontend): fix #11, #12, #16, #19, #27, #29, #36, #40 as part of SPA rewrite.
- **Phase 5** (packaging): no new fixes; validates prior work.
- **Phase 6** (cutover): release notes document all FIX+FLAG entries; pre-upgrade audit tool ships.

## Verification

Each FIX must be defended by:

1. A unit or integration test that would have failed under the old behaviour and passes under the new.
2. A golden-traffic regression run (see `GOLDEN_TRAFFIC_PLAN.md`) showing either unchanged output or an explicitly-listed, expected difference.

No fix lands without both artefacts.
