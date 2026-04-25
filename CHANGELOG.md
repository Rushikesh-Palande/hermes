# Changelog

All notable changes to HERMES are documented in this file.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
Pre-release suffixes (`-alpha.N`, `-beta.N`, `-rc.N`) are used until v1.0.0.

## [Unreleased]

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

[Unreleased]:     https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.5...HEAD
[0.1.0-alpha.5]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.4...v0.1.0-alpha.5
[0.1.0-alpha.4]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.3...v0.1.0-alpha.4
[0.1.0-alpha.3]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.2...v0.1.0-alpha.3
[0.1.0-alpha.2]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.1.0-alpha.1...v0.1.0-alpha.2
[0.1.0-alpha.1]:  https://github.com/Rushikesh-Palande/hermes/compare/v0.0.1...v0.1.0-alpha.1
[0.0.1]:          https://github.com/Rushikesh-Palande/hermes/releases/tag/v0.0.1
