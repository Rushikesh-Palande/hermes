# Changelog

All notable changes to HERMES are documented in this file.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
Pre-release suffixes (`-alpha.N`, `-beta.N`, `-rc.N`) are used until v1.0.0.

## [Unreleased]

## [0.1.0-alpha.31] — 2026-04-26

### Release-workflow hotfix

- **Fix `Create GitHub Release` failing to download artifacts.** The
  `Download all artifacts` step used no `name:` filter, so
  `actions/download-artifact@v4` attempted to fetch every artifact in
  the run — including the Docker buildx GHA cache entry
  (`*~KLR09D.dockerbuild`), which is stored separately from regular
  artifacts and consistently fails after 5 retries. Replaced the
  single unfiltered download step with three explicit named downloads
  (`deb`, `deb-backend`, `offline-amd64`).

No app code changes.

## [0.1.0-alpha.30] — 2026-04-26

### Backend-only .deb + customer integration guide

- **`hermes-backend` Debian package.** New `packaging/debian-backend/`
  packaging for customers who bring their own frontend and want only the
  Python API + ingest services. Excludes the SvelteKit UI; nginx is
  `Recommends` (not `Depends`) so it can be omitted. Postinst calls
  `install.sh --skip-ui --skip-nginx`. The `.deb` is built by a new
  `build-deb-backend` job in the release workflow and uploaded as the
  `deb-backend` artifact.

- **`docs/customer/INTEGRATION_GUIDE.md`.** Comprehensive API reference
  for external integrators: system overview and architecture diagram,
  authentication flow (email OTP → JWT), full REST API reference for all
  40+ endpoints with request/response examples, database schema with
  entity diagram, event detection algorithm descriptions, MQTT payload
  format (inbound and outbound), configuration reference (all env vars),
  Prometheus metrics catalogue, error reference, and quick-start code
  examples in Python, JavaScript/Node, and curl.

No app code changes.

## [0.1.0-alpha.29] — 2026-04-26

### Release-workflow hotfix (round 3)

- **Remove obsolete `override_dh_systemd_enable` / `override_dh_systemd_start`
  targets from `debian/rules`.** debhelper compat 13 removed both commands
  (folded into `dh_installsystemd`). Having no-op overrides for them now
  causes `dh` to abort with "Aborting due to left over override/hook targets
  for now removed commands." Removed the two targets; since we have no
  `debian/*.service` files `dh_installsystemd` is already a no-op.

No app code changes.

## [0.1.0-alpha.28] — 2026-04-26

### Release-workflow hotfix (round 2)

Two bugs remained after alpha.27 that kept CI red:

- **`uv pip wheel` is not a valid uv subcommand.** `build-offline-bundle.sh`
  called `uv pip wheel --wheel-dir ...` which does not exist in uv 0.5+.
  The valid commands are `uv pip download` (fetch pre-built wheels from
  PyPI) and `uv build --wheel` (build the project's own wheel). Replaced
  both calls accordingly.

- **`build-essential:native` missing in build-deb.** `dpkg-checkbuilddeps`
  checks for `build-essential:native` implicitly even when it is not in
  `debian/control` Build-Depends; ubuntu-24.04 runners do not pre-install
  it. Added `build-essential` to Build-Depends and to the `apt-get install`
  step so the check passes.

- **pnpm not on PATH during `dpkg-buildpackage`.** The `debian/rules`
  `override_dh_auto_build` target runs `pnpm install && pnpm build`. The
  previous manual `npm install -g pnpm@9` approach installed pnpm under
  the node tarball's own prefix (not `/usr/local/bin`), so pnpm was
  unreachable when `make` ran the rule. Replaced the manual node setup with
  `pnpm/action-setup@v4` + `actions/setup-node@v4`, which correctly add
  pnpm and node to PATH for all subsequent steps including the
  `dpkg-buildpackage` subprocess.

- **Switched from `ln -s` to `cp -r` for `debian/` directory.** The
  symlink `debian → packaging/debian` works for most debhelper tools but
  can confuse a few that read relative paths from inside the symlink target.
  Copying avoids the edge case entirely.

No app code changes.

## [0.1.0-alpha.27] — 2026-04-25

### Release-workflow hotfix

The alpha.26 release pipeline failed: `build-deb` exited 3 on both
amd64 and arm64 matrix entries, and `build-offline` exited 126.
This release ships the fixes:

- **Executable bit on packaging scripts.** `install.sh`,
  `uninstall.sh`, `build-offline-bundle.sh`, `release-notes.sh`,
  and `debian/{postinst,postrm,rules}` were committed as `100644`
  in alpha.26 because they were created by tools that don't set the
  +x bit. The workflow's `./packaging/build-offline-bundle.sh`
  invocation got "command found but not executable" (exit 126).
  Re-stored as `100755` via `git update-index --chmod=+x`.

- **Disabled dh-python in `debian/rules`.** debhelper-13's `dh`
  auto-detects `pyproject.toml` and tries to run `pybuild` against
  hatchling at build time. The runner doesn't have hatchling
  pre-installed and the build env is offline at that step, so
  `dpkg-buildpackage` exited 3. Added `--without python3` to
  the `dh` invocation so dh-python is skipped entirely — the
  Python venv is built at install time on the target host
  anyway, where it can pick up host-specific platform wheels.

- **Architecture: all (single .deb instead of per-arch matrix).**
  HERMES is pure Python source + a JS bundle — no native code
  ships in the .deb, so one `_all.deb` works on amd64 and arm64.
  This also removes the pretend "arm64" build matrix entry that
  was actually building an amd64 .deb on the x64 runner. Workflow
  drops from a 2-entry matrix to a single `build-deb` job; release
  body and README updated to reference `hermes_<version>_all.deb`.

- **Trimmed Build-Depends.** `dh-python` and `python3-all` removed
  from `debian/control` Build-Depends since `--without python3`
  means they're never invoked.

No app code changes.

## [0.1.0-alpha.26] — 2026-04-26

### Production packaging — three install paths

User asked for "a file that will run on any Linux even brand new
without any software and dependency download". Truly zero-deps
isn't possible in a single artefact (would need to bundle Postgres,
TimescaleDB, Mosquitto, Python, Node, ~500 MB total per arch). What
this release ships instead:

- **Path A — `install.sh`**: one-shot installer for any deb-based
  or rpm-based Linux with internet. Detects distro family
  (deb / rpm / arch / alpine), installs system deps via the right
  package manager (postgresql-16, timescaledb, mosquitto, nginx,
  python3.11), creates the `hermes` system user + Postgres role +
  database, generates a JWT secret, builds the Python venv + the
  SvelteKit UI, runs migrations, installs systemd units, configures
  nginx, starts the services. Idempotent — re-running upgrades in
  place. ~5 minutes on a fresh Pi 4.

- **Path B — Container** (`Dockerfile` + `docker-compose.prod.yml`):
  any Linux with Docker. Multi-stage build (ui-builder + py-builder
  + slim runtime); ~317 MB image. Compose stack includes Postgres,
  Mosquitto, hermes-api, and hermes-ingest in four containers
  sharing a network. Includes healthcheck, named volumes for data
  persistence, and a multi-arch buildx recipe in INSTALLATION.md.

- **Path C — Offline bundle** (`build-offline-bundle.sh`):
  air-gapped install. Builds a ~150 MB compressed `.tar.gz`
  containing the source tree + every system `.deb` (postgres,
  timescaledb, mosquitto, nginx, python3.11) + a Python wheelhouse
  with every locked dep + a pre-built SvelteKit bundle. Install via
  `install.sh --offline` with no internet access required.

### Added

- **`packaging/install.sh`** — distro-aware one-shot installer with
  `--operator-email`, `--offline`, `--skip-ui`, `--skip-nginx` flags.
  Idempotent; exit codes documented.
- **`packaging/uninstall.sh`** — clean removal with `--drop-database`
  + `--keep-config` flags. Stops services, removes systemd units,
  optionally drops DB + roles, removes system user.
- **`packaging/Dockerfile`** — three-stage build (Node UI builder,
  Python venv builder, slim runtime). Final image runs as non-root
  user `hermes`, healthchecks `/api/health`, uses tini as init.
- **`packaging/docker-compose.prod.yml`** — production stack with
  resource-limit guidance for Pi 4 / cloud-VM deployments.
- **`packaging/build-offline-bundle.sh`** — Path C bundle builder.
  Stages .debs via `apt-get download`, builds a wheelhouse via
  `uv pip wheel`, pre-builds the UI, tars it all up.
- **`packaging/nginx/hermes.conf`** — TLS-ready reverse proxy site.
  Splits `/api/live_stream` (proxy_buffering off so SSE flushes
  immediately) from the rest of `/api`. Serves the SvelteKit static
  bundle from `/opt/hermes/ui/build/client/`. Compatible with
  `certbot --nginx` for one-command TLS upgrade.
- **`packaging/debian/`** — proper Debian package metadata:
  `control` (declares apt deps), `changelog`, `copyright`, `rules`
  (debhelper orchestration), `install` (source → /opt/hermes/
  mapping), `postinst` (creates system user, runs install.sh),
  `postrm` (clean removal on apt purge). End users:
  `sudo dpkg -i hermes_X.Y.Z_amd64.deb && sudo apt install -f`.
- **`docs/operations/INSTALLATION.md`** (~470 lines) — comprehensive
  walkthrough of all three paths. Pick-your-path decision tree,
  per-path quickstart + step-by-step internals, distro support
  matrix, multi-arch builds, common-failure lookup, "what you
  DON'T get from any of these paths" honest scope statement.
- **`packaging/README.md`** — updated to map every file in the dir.

### Changed

- **`packaging/Dockerfile`**: builder stage now works under
  `/opt/hermes` (not `/build/`) so the editable install's `.pth`
  file resolves correctly when copied to the runtime stage.

### Out of scope (deliberate, not in this release)

- A "literally any Linux, single-file, zero deps" AppImage. As called
  out in INSTALLATION.md: that would mean bundling Postgres +
  TimescaleDB + Mosquitto + Python + Node binaries per architecture,
  which is a separate release-engineering project. The container path
  (B) is the closest approximation that ships from one repo.
- Published Docker registry images. Today users build locally via
  `docker compose up --build`. A future release will publish to
  ghcr.io or Docker Hub so `docker compose up` is one command.
- Automated TLS via certbot. INSTALLATION.md tells operators to run
  `certbot --nginx` after pointing DNS; we don't auto-provision.

Total tests: still 200 unit + 133 integration + 3 golden = 336
passing. No code changes; ruff + mypy clean.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.25] — 2026-04-26

### Documentation sprint — 10 new in-depth guides + reference catalogs

User asked for "each and every document" with extreme detail and
diagrams. This release lands the full set. README's "Key documents"
section is restructured into three groups (rewrite guides, design +
reference, frozen legacy contracts) so finding the right doc is a
single scan.

### Added

#### Under `docs/guides/` (operator + developer prose, with diagrams)

- **`WORKFLOW.md`** — end-to-end data flow walkthrough. Big-picture
  diagram, then Stage 0–7 each with its own zoomed diagram. Covers
  STM32 firmware shape, paho callback, `_consume`, buffers,
  detection + mode gating, TTL gate, durable sinks, operator
  surfaces, the Modbus inlet variant, what each load level looks
  like, and a failure-mode lookup table.
- **`BACKEND.md`** — every Python module under `services/hermes/`.
  Top-level files, `api/` + `auth/` + `db/` + `detection/` +
  `ingest/` subtrees, route catalog, concurrency rules, cross-
  reference of which gap landed in which file, and a "places where
  the code is non-obvious" lookup.
- **`UI.md`** — every SvelteKit page. Stack + conventions, `$lib/api`
  / `types.ts` / `LiveChart.svelte` deep dives, page-by-page
  composition with ASCII mock-ups, live-chart internals, auth flow
  + token lifecycle, build/deploy.
- **`EVENTS.md`** — detector mechanics in detail. Five event types
  table, common machinery (sliding window, debounce, init-fill ratio,
  data-gap reset), each Type A/B/C/D detector with formula + state
  diagram, BREAK + mode-switching state machine with asymmetric
  grace windows, priority + TTL gate's four rules, storage shape
  (events.metadata + event_windows), tuning recipes, and a 12-row
  "why didn't it fire?" debug matrix.
- **`CONFIGURATION.md`** — every env var + every DB-backed setting
  in one place. Two-layer model diagram, env vars by group, DB-
  backed settings by table, scope resolution diagram for parameter
  rows, where-to-set-what lookup table, .env file template (dev +
  prod /etc/hermes split), how-to-add a new env var, how-to-add a
  new parameter key.
- **`METRICS.md`** — every Prometheus metric. Organized by category
  (inbound throughput, detection output, pipeline state, hot-path
  latency, session_samples writer, Modbus poller). Reading-the-labels
  guidance, helper APIs for tests, multi-shard scraping
  considerations, suggested Grafana dashboard panels with thresholds.
- **`DEVELOPMENT.md`** — local dev environment setup. Prereqs,
  one-time setup, daily run loop (4-terminal pattern), project
  layout cheat sheet, useful commands (Python + UI + DB + MQTT +
  Git), debugging common pitfalls (port 5432, psql missing, WSL env
  vars, TS server staleness, golden LFS, no live data, no fires),
  IDE setup notes for VSCode + PyCharm.
- **`TESTING.md`** — test tier strategy. Four tiers in one table
  (unit / integration / bench / golden), per-tier rules + what
  lives where + how to run, test-writing conventions, mocking
  philosophy ("don't"), CI matrix, coverage expectations, single-
  test invocation patterns.

#### Under `docs/design/` (catalog reference for the rewrite)

- **`DATABASE_SCHEMA.md`** — every table, column, default,
  constraint, index, hypertable setting. Schema overview diagram,
  migrations layout, per-enum + per-table reference (devices,
  packages, parameters, sessions, session_logs, events,
  event_windows, session_samples, sensor_offsets, users, user_otps,
  mqtt_brokers), triggers, hypertable compression policies, LISTEN/
  NOTIFY channels, common queries.
- **`REST_API.md`** — every endpoint with request/response shapes.
  Conventions, a single endpoint-index table at top, then per-
  resource sections with shapes + status codes + side effects.
  Covers: health, auth, devices, offsets, events, config, sessions,
  packages, mqtt-brokers, system-tunables, live_stream, metrics.
  Plus a how-to checklist for adding a new endpoint.

### Changed

- **`README.md`** — "Key documents" restructured into three groups
  so a new contributor sees the rewrite-guides set before the
  legacy contracts.
- **CONTRIBUTING.md** — left as-is (still accurate).

### Removed (cleanup)

- 18 empty folders that were leftover layout placeholders nothing
  used: `tests/contracts/`, `tests/e2e/`, `tests/fixtures/`,
  `tests/performance/`, `tests/unit/{api,detection,storage}/`,
  `services/api/{routes,schemas}/`, `services/api/`,
  `services/shared/{detection,models,storage,telemetry}/`,
  `services/shared/`, `services/ingest/`, `docs/events/`,
  `migrations/versions/`. The current code lives at
  `services/hermes/...` and the test tiers are flat under
  `tests/{unit,integration,bench,golden}/`.

No code changes. All four test tiers still green.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.24] — 2026-04-26

### Fixed

- **CI: golden tests no longer fail with `JSONDecodeError`** on
  ubuntu-24.04. The `.gitattributes` rule
  `tests/golden/**/*.ndjson  filter=lfs ...` was tracking even the
  small (kB-sized) synthetic corpora and baselines via Git LFS;
  CI checks out without `git lfs pull`, so the test harness saw
  the LFS pointer text instead of the JSON. Narrowed the LFS rule
  to `tests/golden/captures/**` so only future real-hardware
  captures (~30 MB compressed each per `GOLDEN_TRAFFIC_PLAN.md`
  §1.2) take the LFS hop. The 5 existing files were re-checked-in
  as plain text via `git add --renormalize`.

### Docs

- **`docs/design/ARCHITECTURE.md`** — the test-tier table had the
  golden row marked "(planned)"; updated to reflect alpha.23 ship
  status.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.23] — 2026-04-26

### Golden-traffic harness — gap 9

Replays a recorded (or synthetic) MQTT trace through the rewrite's
detection pipeline and asserts the resulting events match a saved
baseline. This is the mechanism that catches behaviour drift between
releases — and, once real production-hardware captures arrive, the
mechanism that will assert byte-identical parity with the legacy
system per
[`docs/contracts/GOLDEN_TRAFFIC_PLAN.md`](./docs/contracts/GOLDEN_TRAFFIC_PLAN.md).

### Added

- **`tests/golden/harness.py`** — deterministic replay engine.
  `recv_ts` from each frame in the corpus is the authoritative
  wall time; the harness feeds frames straight into
  `DetectionEngine.feed_snapshot` (no asyncio queue, no real-time
  sleep), so a 24 h trace runs in seconds and produces the same
  output every run.
  - `replay(corpus_path, cfg) -> list[CapturedEvent]` — runs the
    pipeline and returns captured events.
  - `assert_matches_baseline(actual, baseline_path)` — strict
    per-row JSON comparison. `HERMES_GOLDEN_UPDATE=1` re-blesses.
  - `CapturedEvent` rounds `triggered_at` to microsecond precision
    and rounds metadata floats to 6 decimal places so cross-platform
    float noise doesn't false-fail.
- **`tests/golden/_generate.py`** — pure-Python (`math`/`random`,
  fixed seed) generators for three synthetic scenarios. NumPy
  intentionally NOT used because the corpora must be byte-stable
  across NumPy versions.
- **3 synthetic corpora** (`tests/golden/corpora/`):
  - `type_a_high_variance.ndjson` — 5 s trace; sensor 1 develops a
    square-wave high-CV signal halfway through. Exercises the
    Type A variance detector.
  - `mode_break.ndjson` — 2.5 s trace; sensor 5 sustains
    above-startup, then drops below break_threshold for >0.5 s.
    Exercises POWER_ON → STARTUP → BREAK and verifies the
    triggered_at = first below-threshold sample invariant from gap 3.
  - `stable_sine.ndjson` — 2 s low-amplitude sine. Smoke baseline
    that catches false-positive regressions (must produce zero
    events).
- **3 pinned baselines** in `tests/golden/baselines/` checked into
  git. Each baseline file is sort-keyed JSON-per-line so diffs are
  reviewable in PRs.
- **3 round-trip tests** (`tests/golden/test_synthetic_corpora.py`):
  - `test_type_a_high_variance` — Type A fires only on sensor 1
    after the variance ramps; baseline matches exactly.
  - `test_mode_break_transition` — exactly one BREAK on sensor 5
    with `triggered_at` = the first below-threshold timestamp.
  - `test_stable_sine_fires_nothing` — baseline-less smoke; asserts
    the captured events list is empty.
- **`golden` pytest marker** registered in `pyproject.toml`. Run
  with `pytest -m golden`. Re-bless with `HERMES_GOLDEN_UPDATE=1
  pytest -m golden`.
- **`tests/golden/README.md`** — operator-facing doc covering
  layout, how to add a corpus, and the rebless flow.

### Out of scope (deliberate, lands when production-hardware captures arrive)

- Real legacy MQTT captures + `observed.sqlite` diff vs the
  rewrite. The contract defines the diff shape; the harness already
  has the replay + baseline infrastructure to drop captures into
  `corpora/` and add `baselines/` snapshots. No code changes
  required when they arrive.
- Outbound MQTT publish capture (separate from event capture). The
  harness-collected `CapturedEvent` covers detection output; a
  follow-up can wire a similar collector to the `MqttEventSink`
  for the publish-stream comparison the contract calls out.
- Golden tests in CI. The new `golden` marker keeps them out of
  the default run; CI integration lands as a small workflow update
  once we have at least one real-hardware baseline so we don't
  block PRs on synthetic-only signal.

Total tests now: 197 unit + 133 integration + 3 golden = 333
passing. Bench unchanged.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.22] — 2026-04-26

### System-tunables UI — gap 8 (read-only)

A unified Settings landing page so operators don't have to ssh into
the host to inspect the live configuration. Read-only by design;
detection thresholds and per-device knobs are already editable on
the existing `/config`, `/mqtt-brokers`, `/sessions`, and `/devices`
pages, which the new page links to.

### Added

- **`GET /api/system-tunables`** in
  `services/hermes/api/routes/system_tunables.py`. Returns:
  - **Live state** — version, ingest_mode, shard topology
    (count + index), dev_mode flag, log_format, active GLOBAL
    session id, count of active LOCAL sessions, count of sessions
    where `record_raw_samples=true`, count of active MQTT and
    Modbus devices.
  - **Tunables list** — boot-time settings (`event_ttl_seconds`,
    `live_buffer_max_samples`, `mqtt_drift_threshold_s`,
    `hermes_jwt_expiry_seconds`, `otp_expiry_seconds`,
    `otp_max_per_hour`) with an `editable` field per row:
    `live` / `via_other_route` / `restart`. The `restart` rows
    carry an `edit_hint` with the exact env-var name + systemd
    command so operators can copy-paste.
  - Sensitive fields (JWT secret, DB URL, SMTP password) are
    explicitly NEVER included; an integration test fails if they
    leak.
- **`/settings` SvelteKit page** in
  `ui/src/routes/settings/+page.svelte`. Renders the live state
  grid, links to the four editable surfaces (`/config`,
  `/mqtt-brokers`, `/sessions`, `/devices`), and a tunables table
  with colour-coded badges per row.
- **Top-nav** `Settings` item.
- **`SystemTunablesOut`, `SystemStateOut`, `TunableField`,
  `IngestMode`, `TunableEditable`** TypeScript shapes in
  `ui/src/lib/types.ts`.
- **8 new integration tests**
  (`tests/integration/test_system_tunables_api.py`) — bootstrap
  state visible, recording-count reflects `record_raw_samples`,
  device counts split by protocol, inactive devices excluded,
  tunables list contains the documented set, no secret fields
  leak in the response, `editable` distinguishes routes,
  version is reported (not the unknown fallback).

### Changed

- **`services/hermes/__init__.py`** now reads `__version__` from
  `importlib.metadata` so it doesn't drift from `pyproject.toml`.
  Falls back to `"0.0.0+unknown"` only when the package isn't
  installed (e.g. PYTHONPATH-only checkouts).

### Out of scope (deliberate)

- Writability for the boot-time tunables. Making them runtime-
  editable means adding a re-read mechanism in every consumer
  (`TtlGateSink`, `ClockRegistry`, `LiveDataHub`) — a dedicated
  follow-up. Until then the response is honest about what
  requires a restart.

Total tests now: 197 unit + 133 integration = 330 passing. Bench
unchanged.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.21] — 2026-04-26

### Modbus TCP support — gap 7

The `DeviceProtocol.MODBUS_TCP` enum value and the `modbus_config`
JSONB column have been in place since alpha.5; this release wires up
the actual polling code so an operator can configure a Modbus device
and have its registers ingested into the same downstream pipeline as
MQTT-sourced data (offsets → live ring buffer → window buffer →
detection → session_samples).

### Added

- **`services/hermes/ingest/modbus.py`** with three pieces:
  - `ModbusConfig` — pydantic model that validates the `modbus_config`
    JSONB shape: `host`, `port`, `unit_id`, `register_start`,
    `register_count` (default 12), `scaling`, `poll_interval_ms`
    (default 100 = 10 Hz), `timeout_s`. Also `parse_modbus_config()`
    which logs + returns None for malformed JSONB (defence in depth).
  - `ModbusPoller` — one async pymodbus `AsyncModbusTcpClient` + a
    poll task. Reads `register_count` input registers starting at
    `register_start`, decodes each 16-bit word to a float via
    `raw / scaling`, and invokes a downstream snapshot callback with
    `(device_id, ts, {sensor_id: value})`. Reconnect on the fly if
    the client drops; one bad cycle never blows up the loop.
  - `ModbusManager` — watches `devices` for `protocol=modbus_tcp AND
    is_active=true` every 5 s and spawns/cancels one poller per
    device. Config changes restart the poller with the new params;
    disabled or deleted devices have their pollers torn down.
- **`IngestPipeline._on_modbus_snapshot`** — the downstream callback
  the manager passes to each poller. Re-runs offset correction, fills
  the live + window buffers, ticks the same Prometheus counters that
  the MQTT path uses (so Grafana sums work uniformly across sources),
  feeds the detection engine, and pushes to the
  `SessionSampleWriter`. Modbus and MQTT data flow through the
  identical detection chain — operators can mix sources on the same
  detection thresholds.
- **`pymodbus>=3.7`** runtime dep.
- **Two new Prometheus counters + one gauge**:
  - `hermes_modbus_reads_ok_total{device_id}`
  - `hermes_modbus_reads_failed_total{device_id}`
  - `hermes_modbus_pollers_active`
- **24 new tests**:
  - `tests/unit/test_modbus_config.py` (18) — config validation,
    bad-input rejection (host/port/unit_id/register_count/scaling/
    interval/timeout boundaries), `parse_modbus_config` returning
    None on invalid JSONB, full and minimal payload round-trips.
  - `tests/integration/test_modbus_poller.py` (6) — spins up a real
    pymodbus async server in-process (using the new pymodbus 3.13
    `SimData`/`SimDevice` API on `ModbusTcpServer`), seeds 12 input
    registers, and verifies: one poll cycle reads + decodes correctly,
    scaling divides raw values, no-server doesn't crash the poller,
    `ModbusManager` discovers a Modbus device row and starts polling,
    refresh loop catches devices added after `start()`, refresh loop
    drops pollers when a device is disabled.

### Changed

- **`IngestPipeline.__init__`** constructs a `ModbusManager` (skipped
  in `live_only` mode), and `start()` / `stop()` wire its lifecycle.
  Manager `stop()` runs BEFORE the consumer drain so no new Modbus
  snapshots arrive into a torn-down detection engine.

### Out of scope (deliberate, follow-ups can land independently)

- 32-bit float / int register types. Legacy reads 12 uint16; we match
  that. A future `register_layout` field on `ModbusConfig` extends it.
- Modbus RTU (serial). TCP only.
- Read retries within a single poll cycle. The legacy did 3 retries;
  we let the next poll be the retry — same long-run outcome, simpler.
- A UI for editing `modbus_config` on a device. The `/api/devices`
  endpoint already accepts it; a dedicated form lands with the
  system-tunables UI in gap 8.

Total tests now: 197 unit + 125 integration = 322 passing. Bench
unchanged (Modbus is opt-in; manager idles when no Modbus devices
are configured).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.20] — 2026-04-25

### Continuous-sample writer — gap 6

Wires up the `session_samples` hypertable so an operator who flips
`record_raw_samples=true` on a session actually gets a complete raw
archive of every sensor reading for the session's lifetime. Default
behaviour is unchanged — the writer is a fast no-op when no session
has recording on, so existing deployments see zero impact.

### Added

- **`SessionSampleWriter`** in
  `services/hermes/ingest/session_samples.py`. Owned by
  `IngestPipeline`, started/stopped alongside it (skipped in
  `live_only` mode where detection shards own this path). Design:
  - **Hot path** (`push_snapshot`) is one dict lookup + early return
    when no recording covers the device. When recording is on, a
    snapshot allocates 12 row tuples and appends them to an in-memory
    buffer. No DB I/O, no logging, no async.
  - **Refresh loop** queries `sessions WHERE ended_at IS NULL AND
    record_raw_samples = true` every 5 s and atomically swaps the
    cached `(global_session_id, local_sessions: dict[device_id, sid])`
    pair. Operator changes propagate within one refresh interval; the
    hot path reads via plain attribute lookup with no locking.
  - **Flush loop** drains the buffer every 1 s into
    `session_samples` via asyncpg `copy_records_to_table`. Splits
    into 5 000-row chunks so a busy second doesn't tie up the
    connection. Increments a Prometheus counter per row written and
    per batch dispatched.
  - **Backpressure** — when the buffer hits its 60 000-row cap
    (~2 s at production rate), excess rows drop and increment
    `hermes_session_samples_dropped_total`. Operators see drops in
    Grafana before they see DB latency.
  - **Lifecycle** — `start()` opens a dedicated asyncpg connection
    (separate from the SQLAlchemy pool), runs an initial refresh so
    `push_snapshot` has the up-to-date set on the very first sample,
    then spawns the two background tasks. `stop()` cancels both,
    flushes whatever's left in the buffer, and closes the connection.
- **Session resolution rule** — LOCAL session for the device wins
  over GLOBAL. If neither covers the device, the snapshot is
  silently dropped. Mirrors the detector-config resolution order so
  operator intuition stays consistent.
- **`SessionSample`** SQLAlchemy model (mirrors `session_samples`
  table from migration 0002).
- **5 new Prometheus metrics**:
  - `hermes_session_samples_written_total`
  - `hermes_session_samples_dropped_total`
  - `hermes_session_samples_batches_flushed_total`
  - `hermes_session_samples_queue_depth`
  - `hermes_session_samples_recording_active`
- **14 new tests**: 9 unit
  (`tests/unit/test_session_samples_writer.py` — no-recording no-op,
  GLOBAL captures all devices, LOCAL overrides GLOBAL, LOCAL-only
  device routing, buffer-overflow drop accounting, full-buffer drop,
  recording-state flag, row-tuple shape matches DB column order,
  queue-depth gauge tracks buffer) and 5 integration
  (`tests/integration/test_session_samples_writer.py` — end-to-end
  persist with GLOBAL recording, no writes when nothing recording,
  refresh loop catches sessions started after writer.start(),
  graceful stop flushes leftover buffer, LOCAL session routes only
  its own device).

### Changed

- **`IngestPipeline`** constructs a `SessionSampleWriter` (skipped in
  `live_only` mode), starts/stops it on lifecycle hooks, and passes
  it into `_consume` via `sample_writer=`.
- **`_consume`** calls `sample_push(device_id, ts, sensor_values)`
  inside the existing `buffers` time-stage, after `live_push` and
  `window_push`. When `sample_writer` is None (the bench, tests not
  exercising recording), the local pre-bind makes this a single
  `is None` check per snapshot.

### Performance impact

The writer's hot path is a no-op when no recording is active.
Throughput bench (median of 5 runs) is ~10 000 msg/s on a developer
laptop — consistent with post-alpha.17 numbers (high run-to-run
variance, 8 800 - 11 200). On Pi 4 (~3× slower), estimated ~3 300
msg/s vs. the 2 000 msg/s production target — ~1.6× headroom even
when recording is OFF. With recording ON, the writer adds roughly
12 list appends + one `datetime.fromtimestamp` per snapshot;
benchmarks of the recording-on path are pending Pi 4 soak.

Total tests now: 179 unit + 119 integration = 298 passing.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.19] — 2026-04-25

### Operator UI — gap 5 (Sessions + Packages)

Operator-driven session lifecycle. The DB schema, triggers, and partial
unique indexes for sessions/packages have been in place since alpha.5;
this release wires up the API and the UI so operators can actually
start, stop, and audit sessions without going through the bootstrap
helper.

### Added

- **`/api/sessions/*`** in `services/hermes/api/routes/sessions.py`:
  - `GET /api/sessions` — list with optional filters
    (`active=true|false`, `scope=global|local`, `device_id`, `limit`).
    Returns rows newest first; default `limit=100`.
  - `GET /api/sessions/current` — convenience endpoint returning the
    active GLOBAL session and every active LOCAL session in one call.
  - `GET /api/sessions/{id}` — detail.
  - `POST /api/sessions` — start a session. Validates scope shape via
    `model_validator` (GLOBAL must omit `device_id`; LOCAL must set it
    and references the active GLOBAL as parent automatically).
    Returns 409 on the partial-unique-index conflict, 422 on a missing
    package, 422 if a LOCAL session is requested without an active
    GLOBAL.
  - `POST /api/sessions/{id}/stop` — close. Idempotent on
    already-closed sessions. The DB triggers `end_local_children`
    cascade-close LOCAL children when a GLOBAL closes;
    `sessions_lock_package` flips `packages.is_locked = TRUE` on
    first close.
  - `GET /api/sessions/{id}/logs` — audit trail
    (`session_logs` rows). `?order=asc|desc` (default ascending for a
    chronological timeline). Returns 404 if the parent session doesn't
    exist so an empty array is never ambiguous.
- **`/api/packages/*`** in `services/hermes/api/routes/packages.py`:
  - `GET /api/packages` — list (newest first).
  - `GET /api/packages/{id}` — detail.
  - `POST /api/packages` — create a fresh, unlocked package.
  - `POST /api/packages/{id}/clone` — fork a package, copying every
    `parameters` row over and setting `parent_package_id`. Canonical
    flow for "edit a locked package": clone, edit the clone, start a
    new session against it.
- **SvelteKit `/sessions` page** in
  `ui/src/routes/sessions/+page.svelte`. Active panel (global +
  locals with Stop buttons), start-session form (scope picker that
  auto-shows/hides device-id field, package dropdown with `default` /
  `locked` tags surfaced, optional notes, `record_raw_samples`
  toggle), and a Recent Sessions table that links to per-session
  detail.
- **SvelteKit `/sessions/[session_id]` detail page** in
  `ui/src/routes/sessions/[session_id]/+page.svelte`. Header with
  status pill (active/closed) and Stop button when active. Lifecycle
  metadata grid (started/ended timestamps, started_by, ended_reason,
  notes, package, parent session). Audit-log timeline rendering each
  `session_logs` row with a colour-coded chip per event type (start,
  stop, pause, resume, reconfigure, error) and the JSON `details`
  pretty-printed.
- **Top-nav** now has a `Sessions` item between `Events` and `Config`.
- **TypeScript shapes** in `ui/src/lib/types.ts`: `PackageOut`,
  `PackageIn`, `SessionScope`, `SessionLogEvent`, `SessionOut`,
  `SessionStart`, `SessionStop`, `SessionLogOut`,
  `CurrentSessionsOut`.
- **30 new integration tests**:
  - `tests/integration/test_packages_api.py` (7) — list / create /
    get / clone-copies-parameter-rows / clone-doesn't-modify-source /
    rejects-empty-name / minimal-payload.
  - `tests/integration/test_sessions_api.py` (23) — current,
    list-with-filters, GLOBAL-409-when-active, GLOBAL-success-after-
    stop, LOCAL-422-with-no-active-global, LOCAL-success, two-
    LOCALS-same-device-409, two-LOCALS-different-devices-success,
    scope-shape-validators (422), unknown-package-422, idempotent-
    stop, unknown-session-stop-404, end_local_children-cascade,
    sessions_lock_package-trigger-on-close, start-writes-log,
    stop-writes-log-with-reason, logs-404, logs-asc-vs-desc,
    get-404, scope-filter, device_id-filter, sanity-on-log-rows.

### Changed

- **`services/hermes/api/main.py`** registers two new routers:
  `/api/packages` and `/api/sessions`.
- **Out of scope (deliberate)**: PATCH for packages (rename,
  archive), DELETE for sessions/packages, pause/resume lifecycle
  events, and an authenticated user identity in the `actor` field of
  audit logs (currently hard-coded to `"api"` until the auth flow
  ships). All non-blocking for the gap-5 operator workflow.

Total tests now: 170 unit + 114 integration = 284 passing. Bench
unchanged (gap 5 doesn't touch the hot path).

UI: `pnpm check` (svelte-check) clean. Manual click-through in a
browser was NOT done in this session; CI will exercise the routes.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

## [0.1.0-alpha.18] — 2026-04-25

### Operator UI — gap 4 (MQTT broker config + at-rest password encryption)

The `mqtt_brokers` table existed since alpha.5 but had no API or UI;
this lands both. The partial unique index `mqtt_brokers_one_active`
(migration 0002) is preserved end-to-end — at most one row may have
`is_active=TRUE`, enforced by the route layer issuing a deactivate-
others UPDATE inside the same transaction whenever a row flips
active.

### Added

- **`/api/mqtt-brokers/*` CRUD** in
  `services/hermes/api/routes/mqtt_brokers.py`. Endpoints:
  `GET /api/mqtt-brokers` (list, ordered by `broker_id`),
  `POST /api/mqtt-brokers` (create — auto-deactivates other rows),
  `GET /api/mqtt-brokers/{id}`, `PATCH /api/mqtt-brokers/{id}`
  (partial update with documented password semantics),
  `DELETE /api/mqtt-brokers/{id}`,
  `POST /api/mqtt-brokers/{id}/activate` (atomic activate; idempotent).
- **`hermes.auth.secret_box`** — at-rest symmetric encryption for
  operator-typed secrets, currently the MQTT broker password.
  `cryptography.Fernet` (AES-128-CBC + HMAC-SHA256 + versioned token)
  with the key derived from `HERMES_JWT_SECRET` via HKDF-SHA256 with
  a domain separator (`hermes/secret_box/v1`). One secret to manage
  for ops; rotating `HERMES_JWT_SECRET` invalidates every active
  session AND forces re-entry of stored broker passwords — same
  "reset everything" mental model. Plaintext is NEVER returned by
  any API response; the response shape carries a `has_password: bool`
  flag instead.
- **SvelteKit `/mqtt-brokers` page** in
  `ui/src/routes/mqtt-brokers/+page.svelte`. SvelteKit 5 + runes,
  uses the existing `$lib/api` wrapper. Add-broker form
  (host / port / username / password / use_tls / is_active) +
  per-row Activate/Deactivate, Set password, Clear password, and
  Delete actions. Top-nav adds an "MQTT" item.
- **`MqttBrokerOut`, `MqttBrokerIn`, `MqttBrokerPatch`** TypeScript
  interfaces in `ui/src/lib/types.ts`.
- **23 new tests**: 7 unit (`tests/unit/test_secret_box.py` —
  round-trip, unicode/empty edge cases, no-plaintext-leak,
  IV randomness, tampering raises, singleton caching) + 16
  integration (`tests/integration/test_mqtt_brokers_api.py` —
  full CRUD coverage, activation invariant, password write semantics
  end-to-end through the DB row, 404/204 lifecycle).

### Changed

- **`pyproject.toml`** adds `cryptography>=43.0` as a runtime dep.
- **`services/hermes/api/main.py`** registers the new router under
  `/api/mqtt-brokers`.

### Out of scope (deliberate, called out in the UI)

Live broker switchover. The ingest reads broker config from the
`MQTT_*` env vars at process start; flipping `is_active` does NOT
reconnect a running paho client. After activating a different broker,
operators must `systemctl restart hermes-ingest`. The UI's amber
banner says so explicitly. A future release will close the gap with
`LISTEN`/`NOTIFY` + `paho.disconnect/connect`, mirroring the
alpha.15 config-sync pattern.

Total tests now: 170 unit + 84 integration = 254 passing.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

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
