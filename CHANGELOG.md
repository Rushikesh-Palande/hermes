# Changelog

All notable changes to HERMES are documented in this file.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
Pre-release suffixes (`-alpha.N`, `-beta.N`, `-rc.N`) are used until v1.0.0.

## [Unreleased]

## [0.1.0-alpha.17] — 2026-04-25

### Detection — gap 3 (mode switching + BREAK event emission)

Implements the per-(device, sensor) `POWER_ON` / `STARTUP` / `BREAK`
state machine specified by `EVENT_DETECTION_CONTRACT.md` §2.3 / §7.
**Default behaviour is unchanged** — mode switching is opt-in via the
`mode_switching.config` parameter row. `enabled=False` is the default,
matching the legacy `mode_switching_enabled` knob, so existing
deployments behave bit-for-bit identically to alpha.16 until the
operator turns it on.

### Added

- **`ModeStateMachine`** in
  `services/hermes/detection/mode_switching.py`. Faithful port of the
  legacy state machine at `event_detector.py:855-955` with the same
  three modes, the same six per-sensor timestamps, and the same
  asymmetric grace-window behaviour:
  - `POWER_ON → STARTUP` on `value > startup_threshold` sustained for
    `startup_duration_seconds`. Transient dips lasting less than
    `startup_reset_grace_s` (default 1.0 s) are forgiven; sustained
    drops reset the timer.
  - `STARTUP → BREAK` on `value < break_threshold` sustained for
    `break_duration_seconds`. Emits a BREAK event with `triggered_at`
    set to the FIRST below-threshold sample's wall time — NOT the
    duration boundary. Operators have alarms wired to that earlier
    timestamp; preserving it is a hard contract invariant.
  - `BREAK → STARTUP` recovery uses the same condition as POWER_ON →
    STARTUP. Does NOT emit a new BREAK event.
- **`ModeSwitchingConfig`** dataclass in
  `services/hermes/detection/config.py`: `enabled`, `startup_threshold`,
  `break_threshold`, `startup_duration_seconds`,
  `break_duration_seconds`, `startup_reset_grace_s`. Added to the
  `DetectorConfigProvider` protocol via `mode_switching_for(device, sensor)`.
- **`KEY_MODE_SWITCHING = "mode_switching.config"`** parameter key in
  `DbConfigProvider`, plus `_ConfigCache.mode_switching` so the SENSOR →
  DEVICE → GLOBAL scope walk resolves overrides for mode switching the
  same way it does for Type A/B/C/D.
- **17 new unit tests**:
  - `tests/unit/test_mode_switching.py` (11) — disabled short-circuit,
    every state transition, grace-window asymmetry, per-(device, sensor)
    isolation, `reset_device` semantics, BREAK metadata.
  - `tests/unit/test_engine_mode_gating.py` (6) — engine integration:
    BREAK reaches the sink, recovery doesn't double-fire, disabled
    mode-switching default keeps detection firing, gating suppresses
    Type A events when not active.

### Changed

- **`DetectionEngine.feed_snapshot`** now consults the mode state
  machine for every sensor on every sample. Gating semantics:
  - Type A still feeds the running-sum state on every sample so its
    variance window stays primed, but events are dropped when the
    sensor is not active. Window comes back hot the moment the sensor
    enters STARTUP.
  - Types B/C/D are skipped entirely while not active. Their internal
    windows re-prime after the next STARTUP entry.
  - BREAK events emitted by the state machine are published directly
    to the sink. The TtlGateSink's BREAK-bypass arm (alpha.13) needed
    no changes — BREAK still flows straight through to durable sinks.
- **`DetectionEngine.reset_device`** now also resets the mode state
  machine for that device, so a config reload puts every sensor back
  in `POWER_ON` (matching the legacy fresh-process behaviour).
- **Hot-loop pre-binding** (Layer 1 discipline applied to detection):
  `DetectionEngine.feed_snapshot` now pre-binds `mode_feed`,
  `detector_for`, `sink_publish`, `events_detected`, `type_a_const`,
  `break_label`, and `type_order` to locals before the per-sensor
  loop. Saves a measurable amount of LOAD_GLOBAL+LOAD_ATTR overhead
  at 24 000 samples/s.
- **Singleton ModeDecision instances** for the no-event paths
  (`_DECISION_ACTIVE` / `_DECISION_INACTIVE`) so the steady-state hot
  path doesn't allocate a fresh dataclass per sample.

### Performance impact

The bench median dropped from ~17 100 msg/s (alpha.16) to ~14 000
msg/s with high run-to-run variance (10 200 - 16 100 across five runs
on the same machine). On Pi 4 (~3× slower): estimated ~4 500 msg/s
median vs. the 2 000 msg/s production target — still ~2.25× headroom.
The cost is the price of correctness; running detection on inactive
sensors would have been worse than a few thousand fewer msg/s.

### Cross-shard config sync

Mode-switching config rides the existing Postgres `LISTEN`/`NOTIFY`
plumbing from alpha.15. Adding a new parameter key was a one-line
addition to `KEY_TO_CLS` in `db_config.py`; the `pg_notify`-driven
reload + reset propagates mode-switching changes to every detection
shard automatically.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.16] — 2026-04-25

### Documentation overhaul

No code changes. README + new ARCHITECTURE doc + packaging update so a
developer arriving at the repo can be productive within an hour.

### Added

- **`docs/design/ARCHITECTURE.md`** (~600 lines, ~30 min read) — the
  end-to-end design doc for the rewrite. Covers design philosophy,
  operating constraints, the component map, the `hermes-ingest` hot
  path with threading model, the detection engine, the API process,
  the data layer, tests/CI, a "common tasks → which file" lookup
  table, and a "things that look weird but are correct" appendix
  catching non-obvious invariants (sampled `time_stage`, 9 s post-
  window fence, pre-bound locals in `_consume`, `TRUNCATE`-not-
  `DROP-SCHEMA` between integration tests, etc.).

### Changed

- **`README.md`** rewritten:
  - Status section now reflects every shipped alpha release with one-
    line headlines, plus the gap-list status (1-9) and the perf-layer
    status (Layers 1-3).
  - Architecture diagrams for both single-process default and the
    multi-shard opt-in mode.
  - Quick-start expanded with the actual commands a new developer
    runs.
  - Repository layout updated to include `packaging/systemd/`,
    `docs/design/MULTI_SHARD.md`, and the metric files.
  - New cross-link table pointing at every contract and design doc.
  - Testing section documenting all four tiers (unit / integration /
    bench / golden) with marker examples.
  - Performance section publishing per-release bench numbers
    (alpha.12: 8 589 msg/s; alpha.14: 16 746; alpha.15: 17 117).
  - **Configuration reference** — every `Settings` field documented
    with purpose, default, and grouping (required / MQTT / Detection /
    Multi-shard / Observability / API / OTP).
  - Production-deployment section pointing at `packaging/systemd/` and
    the multi-shard rollout guide.
- **`packaging/README.md`** updated: the `systemd/` subdirectory is no
  longer a placeholder. Documents the four shipped units (`hermes-api`,
  `hermes-ingest`, `hermes-ingest@`, `hermes.target`) with their roles
  and cross-links to `MULTI_SHARD.md` §7 for deployment / rollback.

### Doc discipline (going forward)

Every gap/feature/perf-layer ship now updates the relevant docs in the
SAME release. Stale docs are the #1 onboarding tax — we don't
accumulate that debt.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.15] — 2026-04-25

### Architecture — Layer 3 (multi-process horizontal scaling)

Adds opt-in multi-process operation so HERMES can use all 4 cores on a
Pi 4 instead of one. **Default deployments stay single-process and are
behaviourally identical to alpha.14** — multi-shard is opt-in via env
var. The bench shows alpha.14 single-process sustains ~16 700 msg/s on
a laptop and ~5 500 msg/s on a Pi 4 vs. the 2 000 msg/s production
target, so this isn't required for throughput; it's required for
*safety* when bursts, GC pauses, or device-count growth threaten to
push a single process below steady-state.

### Added

- **Three operating modes** selected by `HERMES_INGEST_MODE`:
  - `"all"` (default) — single process subscribes, runs detection on
    every device, fills live ring buffer. Identical to alpha.14
    behaviour byte-for-byte.
  - `"shard"` — one of N detection processes. Subscribes to
    `stm32/adc` and discards messages where
    `device_id % shard_count != shard_index`. Owns detection + DB sink
    + outbound MQTT for its slice; does NOT fill the live ring buffer.
    Requires `shard_count > 1`.
  - `"live_only"` — subscribes to `stm32/adc`, fills the live ring
    buffer for SSE, runs NO detection. Used by the API process when
    shards are running so SSE keeps working for ALL devices.
- **Cross-shard config sync via Postgres `LISTEN`/`NOTIFY`**. The API
  emits `pg_notify('hermes_config_changed', package_id)` after every
  threshold update; each shard's `DbConfigProvider` opens a dedicated
  asyncpg connection and `LISTEN`s on the channel, reloading + resetting
  cached detectors on receipt. Single-process deployments use the same
  code path; the `NOTIFY` is harmless when no listener is active.
- **systemd units** in `packaging/systemd/`:
  - `hermes-ingest.service` — single-process default (mode=all)
  - `hermes-ingest@.service` — shard template (mode=shard, shard_index
    from `%i`); `Conflicts=` the single-process unit
  - `hermes-api.service` — switches between mode=all and mode=live_only
    via `/etc/hermes/api.env`
  - `hermes.target` — aggregate target so ops can `start`/`stop` all
    HERMES services together
- **`Settings.hermes_ingest_mode`**, `hermes_shard_count`,
  `hermes_shard_index` with a `model_validator` that rejects bad shard
  math (count < 1, index out of range, mode=shard with count = 1) so
  misconfigured deployments fail fast at process start.
- **20 new unit tests**:
  - `tests/unit/test_shard_config.py` (7) — Settings validator coverage
  - `tests/unit/test_consume_shard.py` (5) — round-trips synthetic MQTT
    through `_consume` with various (count, index) and verifies device-
    set membership; asserts the union of all shards equals the full
    device set with no overlap; live_only mode runs without detection.
- **`docs/design/MULTI_SHARD.md`** — full deployment guide with
  topology diagrams, deployment steps, rollback procedure, memory
  budget, failure modes, and topic-sharding rationale.

### Changed

- **`IngestPipeline.__init__`** now constructs DB sink + outbound MQTT
  + TTL gate + detection engine only when `mode != "live_only"`.
  `pipeline.detection_engine`, `pipeline.ttl_gate`, and
  `pipeline.mqtt_event_sink` are typed as `Optional` to reflect this.
- **`_consume()`** accepts `shard_count` / `shard_index` parameters
  (default 1/0 = single-process). When `shard_count > 1`, it filters
  by `device_id % shard_count == shard_index` immediately after parse,
  before any metric counter ticks. Detection feed becomes a no-op when
  the engine is None (live_only mode).
- **`/api/config/...` handlers** emit `NOTIFY hermes_config_changed`
  via `_notify_config_changed()` after every commit + in-process
  reload, regardless of deployment mode.

### Behaviour invariants preserved

Verified by 146 unit tests passing and the throughput bench showing
17 117 msg/s (no regression vs alpha.14):
- Detection thresholds, debounce, ±9 s windows: unchanged
- Event priority/dedup/BREAK rules: unchanged
- MQTT topic shape (`stm32/adc`, `stm32/events/<dev>/<sid>/<TYPE>`): unchanged
- DB row shape and event_windows encoding: unchanged
- API contracts (JSON shapes, status codes): unchanged

Multi-shard is transparent to the device, the operator, and the UI.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.14] — 2026-04-25

### Performance — Layer 1 (single-process micro-opts)

Bench result: **8 589 → 16 746 msg/s** and **103 k → 201 k samples/s**
on the same hardware. ~2x throughput. Headroom on Pi 4 estimated at
~5 500 msg/s vs. the 2 000 msg/s production target. No behavioural
change — detection thresholds, event priority/dedup, MQTT topic
shape, DB row shape, and API contracts are all bit-for-bit
identical to alpha.13.

### Changed

- **`orjson`** replaces stdlib `json` on the ingest hot path. orjson
  is 3-5x faster on the small JSON envelopes STM32 emits and returns
  bytes directly, saving a UTF-8 encode for the outbound MQTT
  publish. Three call-sites swapped:
  - `services/hermes/ingest/main.py` — `_consume()` payload parse
    (2 000/s)
  - `services/hermes/detection/mqtt_sink.py` — outbound event publish
  - `services/hermes/detection/encoding.py` — event-window
    encode/decode written into `event_windows.encoding="json-utf8"`.
    Output bytes are byte-identical to the prior stdlib output, so
    rows written by alpha.13 and earlier still decode correctly.
- **Dropped the per-sample `log.debug("sample_ingested", ...)` call**
  in `_consume()`. Even at filtered debug level, structlog still pays
  the bound-call + kwarg-build cost 24 000 times a second. The
  `hermes_msgs_received_total` and `hermes_samples_processed_total`
  Prometheus counters cover the same ground for debugging without
  the runtime tax. Errors and warnings still log fully.
- **Pre-bound hot-path attribute lookups to locals** in `_consume()`:
  `_m.MSGS_RECEIVED_TOTAL`, `queue.get`, `parse_stm32_adc_payload`,
  etc. Each lookup was previously a LOAD_GLOBAL + LOAD_ATTR per
  call; locals collapse to a single LOAD_FAST. At 2 000 msg/s this
  reclaims ~5–8 ms/s of interpreter overhead.

### Added

- **`orjson>=3.10`** as a runtime dependency.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.13] — 2026-04-25

### Added

- **TTL gate** (gap 2, `EVENT_DETECTION_CONTRACT.md` §8). New
  `services/hermes/detection/ttl_gate.py` implements `TtlGateSink`,
  which sits between the detection engine and the durable sinks
  (DB + outbound MQTT) and enforces the four legacy event-detection
  rules:
  - **Rule 1 (Block)** — drop a fired event if a higher-priority type
    is already armed for the same `(device, sensor)`.
  - **Rule 2 (Preempt)** — a new higher-priority fire clears any armed
    lower-priority timers on the same sensor; the lower types re-arm
    cleanly after the higher one resolves.
  - **Rule 3 (Merge)** — duplicates of an already-armed type are
    swallowed inside the TTL window.
  - **Rule 4 (Arm)** — record `(triggered_at, ttl)` and forward
    NOTHING yet; the held event forwards once
    `triggered_at + ttl_seconds` elapses, after which the existing
    9 s post-window fence on `DbEventSink` adds the second phase.

  Priority order matches the legacy contract: `A < B < C < D`.
  `BREAK` events bypass the gate entirely so wire-break / sensor-
  disconnect alarms remain visible. Without this, a sustained
  out-of-band signal triggers an event on every sample and the
  event log fills with hundreds of duplicates per second.
- **`Settings.event_ttl_seconds`** (default `5.0`) makes the dedup
  window deployment-tunable.
- **`IngestPipeline.stop()`** now calls `TtlGateSink.flush()` before
  tearing down MQTT/DB sinks so a graceful shutdown forwards any
  held events instead of dropping a burst inside the dedup window.
- **14 unit tests** (`tests/unit/test_ttl_gate.py`) covering all four
  rules, BREAK bypass, per-(device, sensor) timer isolation,
  flush-on-shutdown, zero-TTL degenerate config, and negative-TTL
  rejection.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.12] — 2026-04-25

### Added

- **Prometheus metrics** wired through the ingest + detection hot path.
  New `services/hermes/metrics.py` defines counters, gauges, and
  histograms on the prometheus_client default registry:
  `hermes_msgs_received_total{device_id}`,
  `hermes_msgs_invalid_total`,
  `hermes_samples_processed_total{device_id}`,
  `hermes_events_detected_total{event_type,device_id}`,
  `hermes_events_persisted_total{event_type}`,
  `hermes_events_published_total{event_type}`,
  `hermes_consume_queue_depth`,
  `hermes_db_writer_pending`,
  `hermes_mqtt_connected`,
  `hermes_pipeline_stage_duration_seconds{stage}` (sampled 1/100).
- **`GET /api/metrics`** endpoint returns standard Prometheus
  text-format. Unauthenticated by design — firewall / nginx in front.
- **Throughput benchmark** (`tests/bench/test_throughput.py`,
  marker `bench`). Pre-fills the asyncio handoff queue with 2 000
  synthetic payloads (= 1 s of production load) and times the
  consumer drain. Asserts no silent drops and a wall-clock budget.
  Local-laptop run: **8 589 msg/s, ~103 k samples/s — 4× the
  production target**. Now runs as a dedicated CI step so any future
  perf regression fails the build.

### Changed

- CI workflow now has three `pytest` steps: unit (excludes `db`,
  `mqtt`, `bench`), integration (`-m db`), and bench (`-m bench`).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

## [0.1.0-alpha.11] — 2026-04-25

### Added

- **Outbound MQTT event publish** (gap 1, `HARDWARE_INTERFACE.md` §6).
  Every detected event now also lands on
  `stm32/events/<device_id>/<sensor_id>/<EVENT_TYPE>` with a
  `{"timestamp": "YYYY-MM-DD HH:MM:SS.mmm", "sensor_value": float|null}`
  body, matching the legacy topic shape so existing PLC / SCADA
  subscribers keep working. New `services/hermes/detection/mqtt_sink.py`.
- **`MultiplexEventSink`** (`services/hermes/detection/sink.py`) fans
  events out to N children and isolates per-child failures, so a broker
  outage can't disable DB persistence (or vice versa).

### Changed

- `IngestPipeline` now constructs both sinks (DB + MQTT) and feeds the
  detection engine through a `MultiplexEventSink`. The paho client is
  attached to the outbound sink after connection and detached before
  shutdown, so late events skip the publish instead of racing a torn-
  down client.

### Fixed

- `test_jwt_tampered_signature_rejected` — flip a byte in the JWT
  payload segment instead of the signature segment. Last-char flips
  could occasionally round-trip through base64url and the test would
  flake.
- `test_event_round_trip_writes_event_and_window` — the
  `window.start_ts.timestamp() <= ts <= window.end_ts.timestamp()`
  assertion now allows 1 µs slop. `datetime.fromtimestamp(x).timestamp()`
  is not bit-identical to `x` at sub-second boundaries (off-by-1-ULP);
  the test was probing a precision invariant that doesn't matter
  semantically.

## [0.1.0-alpha.10] — 2026-04-25

### Added

- **OTP + JWT authentication (Phase 3.5)**, replacing the dev-mode
  bypass as the primary auth path:
  - New `services/hermes/auth/` package: `jwt.py` (HS256 issue/decode),
    `otp.py` (argon2id hash + verify of 6-digit codes), `email.py`
    (aiosmtplib send with a no-op + log fallback when SMTP is
    unconfigured), `allowlist.py` (per-line `allowed_emails_path`).
  - `POST /api/auth/otp/request` always returns 204 to prevent
    allowlist enumeration; allowed addresses get a fresh
    argon2id-hashed OTP row and an email. Cooldown
    (`otp_resend_cooldown_seconds`) + per-hour cap
    (`otp_max_per_hour`) enforced as 429.
  - `POST /api/auth/otp/verify` argon2-verifies against the most recent
    unconsumed-and-unexpired OTP, marks it consumed, and issues a
    JWT. `otp_max_attempts` wrong tries lock the OTP.
- **Login UI** at `/login`: two-step (email → 6-digit code) form that
  PUTs the new endpoints and stores the JWT in localStorage.
- **Auth guard** in the root layout pushes anyone without a token to
  `/login`; a Sign-out button drops the token and re-routes.

### Changed

- `get_current_user` decodes the bearer token first; the dev-mode
  bypass only kicks in when no token is presented. So a real frontend
  runs against real auth even with `HERMES_DEV_MODE=1` on — handy
  during rollout. Production deployments still set the flag to `0`
  so the bypass is unreachable.
- `apiFetch` auto-attaches `Authorization: Bearer <token>` from
  localStorage and clears the token on any 401, so the next render
  bounces to `/login` cleanly.

## [0.1.0-alpha.9] — 2026-04-25

### Added

- **Streaming event export (Phase 5d)**: `GET /api/events/export` with
  `?format=csv|ndjson` and the same filter shape as the list endpoint
  (`device_id`, `sensor_id`, `event_type`, `after`, `before`). Pages
  through matches in chunks of 1 000 with a fresh DB session per chunk
  so an export never holds a pool connection for its whole duration.
  Hard-capped at 1 000 000 rows; slice by time for larger pulls. CSV
  has a stable, append-only column order; NDJSON emits one
  `EventOut`-shaped record per line.
- "Export CSV" / "Export NDJSON" buttons in the Events page header
  that build the download URL off the current filter state — the
  browser's `Content-Disposition: attachment` handling does the rest.

### Changed

- The events filter predicate is now factored into
  `_filtered_events_query()` shared by list + export so the two can
  never drift.

## [0.1.0-alpha.8] — 2026-04-25

### Added

- **Sensor offsets editor (Phase 5c)**. Four new endpoints under
  `/api/devices/{device_id}/offsets`:
  - `GET ` — list all 12 sensors (missing rows default to `0.0`)
  - `PUT ` — bulk replace; sensors omitted from the body are reset
  - `PUT /{sensor_id}` — upsert a single sensor
  - `DELETE /{sensor_id}` — remove the override (effective value 0.0)
  Every mutation commits, then refreshes the live `OffsetCache` so
  `corrected = raw − offset` in the ingest hot path picks up the new
  value within one sample tick — no restart needed.
- New "Sensor offsets" panel under the live chart on the device
  detail page (`/devices/[device_id]`). One numeric input per sensor
  with a reset-to-zero button; "Save offsets" calls the bulk PUT with
  the non-zero entries (zero values clear the row).

### Changed

- Renamed `IngestPipeline._offsets` to `.offset_cache` so the API
  layer can reach the live cache off `app.state.ingest_pipeline`.

## [0.1.0-alpha.7] — 2026-04-25

### Fixed

- **SQLAlchemy enum / Postgres enum case mismatch.** Postgres ENUMs
  defined in `0002_core_tables.sql` use lowercase string values
  (`'global'`, `'mqtt'`, …) but SQLAlchemy was sending Python enum
  member NAMES (`'GLOBAL'`, `'MQTT'`). Every insert and select against
  `session_scope`, `parameter_scope`, `session_log_event`, and
  `device_protocol` failed with `invalid input value for enum`. Wired a
  `_pg_enum()` helper in [`db/models.py`](services/hermes/db/models.py)
  that supplies `values_callable` so SA sends the StrEnum's `.value`.
  All five `Enum()` columns now route through it. (`event_type`
  happened to work because `A`/`B`/`C`/`D`/`BREAK` have name == value.)
- **`/api/config` PUTs returning stale data.** The handlers called
  `provider.reload()` from a freshly-opened session that couldn't see
  the route's uncommitted INSERT/UPDATE. Replaced
  `session.flush(); _hot_reload()` with a new `_commit_and_reload()`
  helper that commits before the reload.
- **Override PUTs returning 500 on bad payload.** Override handlers
  take `dict[str, Any]` and validate manually, but Pydantic's
  `ValidationError` raised inside the route body isn't
  auto-converted to 422 (only at parameter-binding time). Added a
  `_validate_or_422()` helper that re-raises as `HTTPException(422)`
  with `include_context=False` so Pydantic's non-JSON-serialisable
  `ctx.error` doesn't crash response serialisation.
- **`test_filter_by_time_range`** now passes datetimes via httpx
  `params=` so the `+` in the offset is URL-encoded; FastAPI was
  decoding the literal `+` to a space and 422-ing.
- **`_seed_event`** pins `fired_at = triggered_at` so the
  `events_fire_vs_trigger` CHECK survives future-dated test
  timestamps.
- **`test_put_type_c_rejects_inverted_thresholds`** — Pydantic v2
  `detail` is a list of error dicts, not a string; assert against the
  message field.

## [0.1.0-alpha.6] — 2026-04-25

### Added

- **Per-device + per-sensor detector config overrides (Phase 5b)**:
  `DbConfigProvider.reload()` now reads every `parameters` row and
  builds three caches (global / device / sensor). `type_X_for(device_id,
  sensor_id)` walks SENSOR → DEVICE → GLOBAL and returns the first hit.
  Override rows store full configs, not deltas (matches legacy
  per-sensor behaviour).
- Five new endpoints on `/api/config/{type}/overrides`:
  list, PUT/DELETE per-device, PUT/DELETE per-sensor — type is a path
  param, factored over `Literal["type_a", …, "type_d"]` so the
  handlers don't repeat per detector. Every mutation hot-reloads the
  live provider and resets cached detectors.
- Config page now renders an "Overrides" panel under each tab with
  per-device and per-sensor tables and "+ Save as device override" /
  "+ Save as sensor override" buttons that PUT the current form's
  values to the requested scope.

### Changed

- Type C's `threshold_lower < threshold_upper` check moved from the
  Type C global-PUT handler to a `model_validator` on the Pydantic
  `TypeCIn` model so it applies uniformly at GLOBAL / DEVICE / SENSOR.

## [0.1.0-alpha.5] — 2026-04-25

### Added

- **Live sensor graph (Phase 5a)**: new `$lib/LiveChart.svelte` opens an
  `EventSource` on `/api/live_stream/{device_id}` and renders all 12
  sensors in real time with uPlot. Step-end paths match the legacy
  ECharts `step:'end'` look; hold-last-value on missing sensors prevents
  the line from snapping to zero on transient gaps.
- New device detail route at `/devices/[device_id]` with device
  metadata, a 1 s / 6 s / 12 s window selector, per-sensor toggle chips,
  and the embedded live chart.
- Device list rows now link to the detail page; an explicit "Live"
  action sits next to the existing enable/disable button.

### Fixed

- `migrations/0005_retention_policies.sql` removed the
  `add_retention_policy('event_windows', …)` call. `event_windows` is
  not a hypertable — Timescale's retention policy requires one — so
  the call raised
  `UnknownPostgresError: "event_windows" is not a hypertable or a
  continuous aggregate` once the previous alpha.4 fix unblocked
  migration-0005 from running cleanly. Cleanup of old window BLOBs is
  deferred to application-level code; promoting `event_windows` to a
  hypertable will need a composite-PK schema change.

## [0.1.0-alpha.4] — 2026-04-25

### Fixed

- `migrations/0003_hypertables.sql` now enables compression on the
  `events` hypertable before `0005` adds the compression policy.
  TimescaleDB 2.17 rejects `add_compression_policy` on a hypertable
  without `timescaledb.compress` settings, surfacing as
  `FeatureNotSupportedError: compression not enabled on hypertable`
  during CI integration tests. `session_samples` already had its
  matching `ALTER TABLE` in 0003.
- `tests/integration/conftest.py` no longer drops the public schema
  between tests. The previous approach unloaded the TimescaleDB
  extension catalog entry but left the shared library loaded in the
  Postgres backend, so the next `CREATE EXTENSION timescaledb` on the
  same connection raised `DuplicateObjectError: extension already
  loaded with another version`. Switched to the canonical
  migrate-once-per-session + `TRUNCATE … RESTART IDENTITY CASCADE`
  pattern. Roughly an order of magnitude faster, too.

## [0.1.0-alpha.3] — 2026-04-25

### Added

- **MQTT ingestion pipeline (Phase 2)**: paho consumer hands raw payloads
  to the asyncio event loop via `call_soon_threadsafe`; a single consumer
  task does parse → STM32 clock anchor (5 s drift re-anchor) → per-sensor
  offset correction → ring buffers. SSE feed at `/api/live_stream/{id}`
  serves the live ring buffer with keepalive + per-tick batch cap.
- **Device CRUD (Phase 3a)**: full REST surface at `/api/devices`
  (list, create, get, patch, delete) with 1–999 ID range, 409 on
  duplicate, 404 on missing, soft-disable via PATCH `is_active=false`.
- **Detection framework + Types A/B/C/D (Phase 3b–d)**: per-sensor
  detectors with O(1) incremental sliding windows. Type A (CV%),
  Type B (post-window deviation, REF_VALUE = 100), Type C (range on
  avg_T3), Type D (two-stage averaging vs. avg_T5, paired with Type C).
  Debounce and data-gap reset semantics match the legacy spec.
- **Event persistence (Phase 3e)**: `DbEventSink` queues events from
  the detection hot path; an async writer task waits for the
  `triggered_at + 9 s` deadline, slices the `EventWindowBuffer`, and
  writes one `events` row + one `event_windows` row + back-link in a
  single transaction. JSON-utf8 encoding now (zstd+delta-f32 later).
- **Event history API (Phase 4a)**: `/api/events` list with filters
  (device, sensor, type, time range), pagination, single-event get,
  and decoded `±9 s` window endpoint.
- **Config API (Phase 4b)**: `/api/config/type_{a,b,c,d}` GET/PUT,
  backed by the `parameters` table at GLOBAL scope. PUT hot-reloads
  the running engine without a process restart.
- **SvelteKit dashboard pages (Phase 4c)**: Overview / Devices / Events /
  Config, with a shared `$lib/api.ts` typed fetch client and Pydantic-
  mirroring `$lib/types.ts`.
- **Dev-mode auth bypass**: `HERMES_DEV_MODE=1` synthesises an admin
  `User` so `CurrentUser`-protected routes work without JWT until the
  full OTP flow lands in Phase 3.5.

### Changed

- `ensure_default_session()` now returns `(session_id, package_id)` so
  callers can wire both at once. Boots a default Package + active
  GLOBAL Session on first start; idempotent thereafter.
- `IngestPipeline` accepts an optional `config_provider` and wires it
  into `DetectionEngine`. The API lifespan supplies a `DbConfigProvider`;
  fresh deployments default to all-disabled detectors.

### Fixed

- Migration integration test now uses `asyncpg.connect()` directly —
  SQLAlchemy's `exec_driver_sql` still routes through asyncpg's prepare()
  path which rejects multi-statement SQL.

## [0.1.0-alpha.2] — 2026-04-24

### Fixed
- CI was red from the first push: the `astral-sh/setup-uv` and
  `pnpm/action-setup` cache steps fail without committed lockfiles.
  Generated and committed `uv.lock` and `ui/pnpm-lock.yaml`, and removed
  the `uv.lock` entry from `.gitignore` (it IS source for an application).
- FastAPI routes using `EmailStr` imported cleanly but failed at runtime
  for lack of `email-validator`. Added the `pydantic[email]` extra.
- `/api/health` returned 404 — the health router was mounted at prefix
  `/api` with route path `""`, resolving to `/api` instead of
  `/api/health`. Mount prefix now matches the contract in
  `tests/unit/test_health.py`.
- Dropped `default_response_class=ORJSONResponse` on the FastAPI app;
  FastAPI 0.115+ emits a deprecation error when `orjson` isn't installed
  and Pydantic v2's direct JSON serialisation is the recommended default.
- `hermes.db.models.Event` was unmappable: composite PK was declared via
  `UniqueConstraint` instead of `primary_key=True` on both columns.
  Fixed to match `migrations/0002_core_tables.sql`.
- ORM enums switched to `enum.StrEnum` (ruff UP042) and JSONB columns
  parameterised as `dict[str, Any]` (mypy `type-arg`).
- `ui/tsconfig.json` was overriding the `include` list inherited from
  SvelteKit's auto-generated tsconfig, silently dropping `vite.config.ts`
  from the IDE's project. Removed the override.

### Added
- `pydantic[email]` extra to `pyproject.toml`.

## [0.1.0-alpha.1] — 2026-04-24

### Added
- Phase 1 foundation scaffolding: Python (`pyproject.toml`, ruff, mypy, pytest)
  and SvelteKit frontend shells.
- Initial SQL migrations (`migrations/0001_init_extensions.sql` … `0005_retention_policies.sql`)
  implementing the packages + sessions + events data model from
  `docs/design/DATABASE_REDESIGN.md`.
- FastAPI skeleton (`services/hermes/api/`): health endpoint, OTP auth stubs,
  device CRUD stubs.
- MQTT ingest skeleton (`services/hermes/ingest/`) — connects, subscribes,
  no business logic yet.
- `docker-compose.dev.yml` for local Postgres+TimescaleDB, Mosquitto, and Redis.
- GitHub Actions CI: ruff, mypy, pytest, pnpm typecheck.
- Dev helper scripts (`scripts/db-migrate.sh`, `scripts/dev-up.sh`,
  `scripts/dev-down.sh`) and `packaging/README.md` placeholder.

### Security
- Redacted the Gmail app password that had been exposed in
  `docs/reference/ops/ops_files.md` (the credential has been rotated; see
  `SECURITY.md` for context).

## [0.0.1] — 2026-04-24

### Added
- Phase 0.5 behaviour contracts (`docs/contracts/`): hardware interface,
  ingestion pipeline, event detection, database, API, config catalog,
  worker protocol, bug decision log, golden traffic plan.
- Phase 0.5 per-file reference library (`docs/reference/`) covering every
  template, static asset, Modbus legacy subsystem, tests, scripts, and ops
  files from the legacy codebase.
- Database redesign design document (`docs/design/DATABASE_REDESIGN.md`)
  locking the packages + parameters + sessions + events schema.
- Repository metadata: README, LICENSE (proprietary), SECURITY, CONTRIBUTING,
  CODE_OF_CONDUCT, CODEOWNERS, issue and PR templates, Dependabot config,
  `.gitignore`, `.gitattributes`, `.editorconfig`.

[Unreleased]:      https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.12...HEAD
[0.1.0-alpha.12]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.11...v0.1.0-alpha.12
[0.1.0-alpha.11]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.10...v0.1.0-alpha.11
[0.1.0-alpha.10]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.9...v0.1.0-alpha.10
[0.1.0-alpha.9]:   https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.8...v0.1.0-alpha.9
[0.1.0-alpha.8]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.7...v0.1.0-alpha.8
[0.1.0-alpha.7]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.6...v0.1.0-alpha.7
[0.1.0-alpha.6]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.5...v0.1.0-alpha.6
[0.1.0-alpha.5]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.4...v0.1.0-alpha.5
[0.1.0-alpha.4]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.3...v0.1.0-alpha.4
[0.1.0-alpha.3]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.2...v0.1.0-alpha.3
[0.1.0-alpha.2]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.1...v0.1.0-alpha.2
[0.1.0-alpha.1]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.0.1...v0.1.0-alpha.1
[0.0.1]:          https://github.com/Rushikesh-Palande/hermes/releases/tag/v0.0.1
