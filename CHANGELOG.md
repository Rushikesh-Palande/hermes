# Changelog

All notable changes to HERMES are documented in this file.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
Pre-release suffixes (`-alpha.N`, `-beta.N`, `-rc.N`) are used until v1.0.0.

## [Unreleased]

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

[Unreleased]:      https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.11...HEAD
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
