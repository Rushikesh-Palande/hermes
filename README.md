# HERMES

**High-frequency industrial sensor monitoring, event detection, and operator dashboard.**

[![License: Proprietary](https://img.shields.io/badge/license-Proprietary-red.svg)](./LICENSE)
[![Status: Pre-Alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)](./CHANGELOG.md)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Node: 20+](https://img.shields.io/badge/node-20%2B-green.svg)](https://nodejs.org/)

HERMES ingests 12-channel ADC telemetry from STM32 hardware over MQTT at
~100 Hz per sensor, runs four parallel event-detection algorithms (A/B/C/D
+ BREAK mode transition), persists events with ±9 s context windows, and
presents live + historical data through a SvelteKit dashboard. Built for
continuous unattended operation on a Raspberry Pi 4 in industrial
environments.

> **Production target:** 20 devices × 12 sensors × 100 Hz = 2 000 MQTT
> messages/s = 24 000 sensor readings/s = 96 000 detector updates/s on
> a Pi 4 with 2 GB RAM and 4 cores.
>
> **Bench (alpha.15):** ~16 700 msg/s on a developer laptop (~5 500 msg/s
> estimated on Pi 4) — comfortably above target with 2.7× headroom on Pi 4.

---

## Table of contents

- [Status & roadmap](#status--roadmap)
- [Architecture at a glance](#architecture-at-a-glance)
- [Quick start (development)](#quick-start-development)
- [Repository layout](#repository-layout)
- [Key documents](#key-documents)
- [Development workflow](#development-workflow)
- [Testing](#testing)
- [Performance](#performance)
- [Configuration reference](#configuration-reference)
- [Production deployment](#production-deployment)
- [Security](#security)
- [License](#license)

---

## Status & roadmap

This is a **ground-up rewrite** of the legacy HERMES dashboard
(SQLite + Flask). The new system uses FastAPI + SQLAlchemy async +
asyncpg + TimescaleDB + SvelteKit 5. Behaviour parity with the legacy
detection engine is enforced by golden-traffic regression tests.

### Released so far

| Version              | Headline                                                              |
| -------------------- | --------------------------------------------------------------------- |
| `v0.1.0-alpha.23`    | Golden-traffic harness — deterministic replay + synthetic corpora     |
| `v0.1.0-alpha.22`    | System-tunables read-only dashboard at `/settings`                    |
| `v0.1.0-alpha.21`    | Modbus TCP support — async poller + DB-backed device discovery        |
| `v0.1.0-alpha.20`    | `session_samples` continuous writer (asyncpg COPY, opt-in per session) |
| `v0.1.0-alpha.19`    | Sessions + Packages API + UI (lifecycle, audit log, package cloning)  |
| `v0.1.0-alpha.18`    | MQTT broker config UI + Fernet at-rest password encryption            |
| `v0.1.0-alpha.17`    | Mode switching (POWER_ON / STARTUP / BREAK) + BREAK event emission    |
| `v0.1.0-alpha.16`    | Documentation overhaul: detailed README + ARCHITECTURE.md             |
| `v0.1.0-alpha.15`    | Layer 3 multi-process shard mode + Postgres LISTEN/NOTIFY config sync |
| `v0.1.0-alpha.14`    | Layer 1 micro-opts: orjson, log discipline, hot-path locals (~2× msg/s) |
| `v0.1.0-alpha.13`    | TTL gate dedupes + prioritises events before durable sinks             |
| `v0.1.0-alpha.12`    | Prometheus metrics + throughput benchmark                              |
| `v0.1.0-alpha.11`    | Outbound MQTT event publish + multiplex sink                           |
| earlier              | Foundation, schema, auth, ingest, detection types A/B/C/D, sessions    |

See [`CHANGELOG.md`](./CHANGELOG.md) for the full per-release detail.

### Gap work in flight

The legacy contracts in [`docs/contracts/`](./docs/contracts/) define
nine areas where the rewrite must reach parity. Status:

| Gap | Topic                                          | Status                                   |
| --- | ---------------------------------------------- | ---------------------------------------- |
| 1   | Outbound MQTT event publish                    | ✅ Shipped (alpha.11)                    |
| 2   | TTL gate (5 s dedup + priority + BREAK bypass) | ✅ Shipped (alpha.13)                    |
| 3   | Mode switching (POWER_ON/STARTUP/BREAK)        | ✅ Shipped (alpha.17)                    |
| 4   | MQTT broker config UI + Fernet at-rest crypto  | ✅ Shipped (alpha.18)                    |
| 5   | Sessions UI (start/stop/attach package)        | ✅ Shipped (alpha.19)                    |
| 6   | Continuous-sample writer (`session_samples`)   | ✅ Shipped (alpha.20)                    |
| 7   | Modbus TCP support                             | ✅ Shipped (alpha.21)                    |
| 8   | System-tunables UI                             | ✅ Shipped (alpha.22, read-only)         |
| 9   | Golden-traffic harness                         | ✅ Shipped (alpha.23, synthetic corpora) |

Layers 1–3 of the perf plan are complete:

| Layer   | Topic                                       | Shipped in | Next     |
| ------- | ------------------------------------------- | ---------- | -------- |
| Layer 2 | Prometheus metrics + throughput bench       | alpha.12   | —        |
| Layer 1 | orjson + log discipline + hot-path locals   | alpha.14   | —        |
| Layer 3 | Multi-process shard + LISTEN/NOTIFY sync    | alpha.15   | —        |

---

## Architecture at a glance

### Single-process (default deployment)

```
┌─────────────┐     MQTT     ┌──────────────────┐    ┌────────────────┐
│   STM32     │ ───────────> │  hermes-ingest   │ ─> │                │
│  ~100 Hz    │ stm32/adc    │  • parse + clock │    │  PostgreSQL    │
│ 12 sensors  │              │  • offsets       │    │  + TimescaleDB │
│ × 20 devs   │              │  • detection     │    │                │
└─────────────┘              │  • TTL gate      │    └────────┬───────┘
                             │  • DB sink       │             │
                             │  • MQTT outbound │             │
                             │  • live ring buf │             │
                             └────────┬─────────┘             │
                                      │ stm32/events/...      │
                                      ▼                       │
                             ┌──────────────────┐             │
                             │   hermes-api     │ <───────────┘
                             │  (FastAPI + SSE) │
                             └────────┬─────────┘
                                      │ HTTPS + SSE
                                      ▼
                             ┌──────────────────┐
                             │   SvelteKit UI   │
                             │   (uPlot)        │
                             └──────────────────┘
```

### Multi-shard (opt-in for safety / scaling)

Same Postgres, same MQTT, same UI — four detection processes split
devices by `device_id % 4`. The API runs in `live_only` mode and
keeps SSE working for ALL devices. Operator threshold edits propagate
to every shard via Postgres `LISTEN`/`NOTIFY`. See
[`docs/design/MULTI_SHARD.md`](./docs/design/MULTI_SHARD.md) for the
full topology, rollout procedure, and rollback steps.

### Data model

`packages` (immutable once used) → `sessions` (global + local
overrides) → `events` (one row per trigger, ±9 s window in
`event_windows`). Append-only migrations in `migrations/`. See
[`docs/design/DATABASE_REDESIGN.md`](./docs/design/DATABASE_REDESIGN.md)
and [`docs/contracts/DATABASE_CONTRACT.md`](./docs/contracts/DATABASE_CONTRACT.md).

### Behaviour parity

Detection output is byte-identical to the legacy system except where
[`docs/contracts/BUG_DECISION_LOG.md`](./docs/contracts/BUG_DECISION_LOG.md)
explicitly records a divergence with rationale. Enforced by the golden
traffic harness ([`docs/contracts/GOLDEN_TRAFFIC_PLAN.md`](./docs/contracts/GOLDEN_TRAFFIC_PLAN.md)).

---

## Quick start (development)

### Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- Python 3.11+ with [`uv`](https://docs.astral.sh/uv/)
- Node.js 20+ with `pnpm` (`corepack enable`)

### Bring up the stack

```bash
git clone git@github.com:Rushikesh-Palande/hermes.git
cd hermes

# 1. Copy environment template and edit for your local setup
cp .env.example .env
# fill in HERMES_JWT_SECRET, SMTP_PASS, etc.

# 2. Start Postgres+Timescale, Mosquitto, Redis
docker compose -f docker-compose.dev.yml up -d

# 3. Apply migrations
./scripts/db-migrate.sh

# 4. Python services
uv sync --extra dev
uv run hermes-api        # FastAPI server on :8080
uv run hermes-ingest     # MQTT ingest in another terminal

# 5. UI
cd ui
pnpm install
pnpm dev                 # Vite on :5173
```

Open `http://localhost:5173`. The dev login bypass is on by default in
`HERMES_DEV_MODE=1`; the OTP/JWT flow lands in the next phase.

### Useful one-liners

```bash
# Run only fast unit tests (no DB needed)
uv run pytest tests/unit -q

# Run the throughput benchmark (asserts 2 000 msg/s drains under budget)
uv run pytest -m bench -s

# Run integration tests against the docker Postgres
uv run pytest -m db

# Lint / format / type-check
uv run ruff check services tests
uv run ruff format services tests
uv run mypy services
```

---

## Repository layout

```
.
├── docs/
│   ├── contracts/             — behaviour contracts frozen from legacy
│   │   ├── API_CONTRACT.md           HTTP surface area
│   │   ├── BUG_DECISION_LOG.md       intentional divergences from legacy
│   │   ├── CONFIG_CATALOG.md         every config knob in the legacy code
│   │   ├── DATABASE_CONTRACT.md      legacy SQLite schema + invariants
│   │   ├── EVENT_DETECTION_CONTRACT.md  the four detector algorithms
│   │   ├── GOLDEN_TRAFFIC_PLAN.md    parity-test methodology
│   │   ├── HARDWARE_INTERFACE.md     STM32 wire format + topics
│   │   ├── INGESTION_PIPELINE.md     legacy MQTT consumer behaviour
│   │   └── WORKER_PROTOCOL.md        legacy detector worker queues
│   ├── design/                — rewrite design decisions
│   │   ├── DATABASE_REDESIGN.md      Timescale schema for the rewrite
│   │   └── MULTI_SHARD.md            Layer 3 horizontal scaling guide
│   └── reference/             — per-file legacy reference library
├── migrations/                — PostgreSQL SQL migrations (append-only)
├── services/hermes/           — Python package (api + ingest + shared)
│   ├── api/                          FastAPI + routes + lifespan
│   ├── auth/                         JWT + OTP
│   ├── db/                           SQLAlchemy models + engine
│   ├── detection/                    detector algorithms + config + sinks
│   ├── ingest/                       MQTT consumer + clock + offsets
│   ├── config.py                     pydantic Settings
│   ├── logging.py                    structlog wiring
│   └── metrics.py                    Prometheus counters/gauges/histograms
├── ui/                        — SvelteKit application
├── tests/
│   ├── unit/                         fast, deterministic, no I/O
│   ├── integration/                  real Postgres via docker-compose
│   ├── bench/                        throughput / latency benchmarks
│   └── golden/                       legacy-parity diff (planned)
├── packaging/                 — production deployment artefacts
│   ├── debian/                       .deb metadata (Phase 9)
│   ├── nginx/                        TLS reverse-proxy config (Phase 9)
│   └── systemd/                      service units, including
│       ├── hermes-ingest.service       single-process default
│       ├── hermes-ingest@.service      shard template (Layer 3)
│       ├── hermes-api.service          FastAPI server
│       └── hermes.target               aggregate
├── scripts/                   — dev + ops shell scripts
├── config/                    — default config files
└── .github/                   — CI, issue templates, dependabot
```

---

## Key documents

When in doubt, follow the cross-links — most files have rationale near
the top.

### Rewrite guides (`docs/guides/`) — extreme detail with diagrams

| If you want to ...                                       | Read                                                                              |
| -------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Walk a sensor reading from STM32 to UI to event row      | [`docs/guides/WORKFLOW.md`](./docs/guides/WORKFLOW.md)                            |
| Map every Python module + responsibility                 | [`docs/guides/BACKEND.md`](./docs/guides/BACKEND.md)                              |
| Map every SvelteKit page + behaviour                     | [`docs/guides/UI.md`](./docs/guides/UI.md)                                        |
| Understand Type A/B/C/D + BREAK + mode switching         | [`docs/guides/EVENTS.md`](./docs/guides/EVENTS.md)                                |
| Reference every env var + DB-backed setting              | [`docs/guides/CONFIGURATION.md`](./docs/guides/CONFIGURATION.md)                  |
| Reference every Prometheus metric                        | [`docs/guides/METRICS.md`](./docs/guides/METRICS.md)                              |
| Set up a local dev environment                           | [`docs/guides/DEVELOPMENT.md`](./docs/guides/DEVELOPMENT.md)                      |
| Run / write tests across all four tiers                  | [`docs/guides/TESTING.md`](./docs/guides/TESTING.md)                              |

### Rewrite design + reference (`docs/design/`)

| If you want to ...                                       | Read                                                                              |
| -------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Get productive on the rewrite in 30 minutes              | [`docs/design/ARCHITECTURE.md`](./docs/design/ARCHITECTURE.md)                    |
| See every table, column, index, constraint               | [`docs/design/DATABASE_SCHEMA.md`](./docs/design/DATABASE_SCHEMA.md)              |
| See every REST endpoint with request/response shapes     | [`docs/design/REST_API.md`](./docs/design/REST_API.md)                            |
| See the rewrite's data model rationale                   | [`docs/design/DATABASE_REDESIGN.md`](./docs/design/DATABASE_REDESIGN.md)          |
| Deploy multi-process for scaling/safety                  | [`docs/design/MULTI_SHARD.md`](./docs/design/MULTI_SHARD.md)                      |

### Frozen legacy contracts (`docs/contracts/`) — what the OLD system did

| If you want to ...                                       | Read                                                                              |
| -------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Understand the legacy system before changing it          | [`docs/contracts/`](./docs/contracts/) (whole directory)                          |
| Know whether a divergence from legacy is allowed         | [`docs/contracts/BUG_DECISION_LOG.md`](./docs/contracts/BUG_DECISION_LOG.md)      |
| See the legacy HTTP API spec                             | [`docs/contracts/API_CONTRACT.md`](./docs/contracts/API_CONTRACT.md)              |
| See the legacy detector spec                             | [`docs/contracts/EVENT_DETECTION_CONTRACT.md`](./docs/contracts/EVENT_DETECTION_CONTRACT.md) |
| See the wire format for STM32 telemetry                  | [`docs/contracts/HARDWARE_INTERFACE.md`](./docs/contracts/HARDWARE_INTERFACE.md)  |
| See the legacy DB invariants                             | [`docs/contracts/DATABASE_CONTRACT.md`](./docs/contracts/DATABASE_CONTRACT.md)    |
| Know how parity vs legacy will be validated              | [`docs/contracts/GOLDEN_TRAFFIC_PLAN.md`](./docs/contracts/GOLDEN_TRAFFIC_PLAN.md) |

### Other

| If you want to ...                                       | Read                                                                              |
| -------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Branch / commit / release conventions                    | [`CONTRIBUTING.md`](./CONTRIBUTING.md)                                            |
| Inspect the per-release changelog                        | [`CHANGELOG.md`](./CHANGELOG.md)                                                  |
| All deployment configuration knobs (one-screen summary)  | [Configuration reference](#configuration-reference) (below)                       |

---

## Development workflow

- **Branching:** [`main`](https://github.com/Rushikesh-Palande/hermes/tree/main)
  is protected. Feature branches off `develop`, merged via PR with
  `--no-ff`. Releases merge `develop → main` with `--no-ff` and tag.
  See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for full rules.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `perf:`, `docs:`, `chore:`, `test:`, `refactor:`,
  `ci:`, `build:`).
- **Pre-merge gate:** `ruff check + format`, `mypy services`,
  `pytest tests/unit -q`. Integration suite runs in CI on every PR.
- **CHANGELOG discipline:** every release bumps the version in
  `pyproject.toml`, lands a fully-detailed entry in `CHANGELOG.md`,
  and is committed as a `chore(release):` commit on `develop` before
  the merge to `main`.
- **Doc discipline:** every gap/feature/perf-layer ship updates the
  relevant docs in the SAME release. Stale docs are the #1 onboarding
  tax — we don't accumulate that debt.
- **Release tags:** annotated tags on `main` named `v<major>.<minor>.<patch>[-prerelease]`
  (e.g. `v0.1.0-alpha.15`). GitHub Releases generated from CHANGELOG.

---

## Testing

| Layer        | Path                  | Marker     | What it covers                                                       |
| ------------ | --------------------- | ---------- | -------------------------------------------------------------------- |
| Unit         | `tests/unit/`         | (default)  | Fast (< 1 s), deterministic, no I/O. ~146 tests.                     |
| Integration  | `tests/integration/`  | `db`       | Real Postgres via docker-compose. Schema, API endpoints, persistence. |
| Bench        | `tests/bench/`        | `bench`    | Throughput. Drains 2 000 synthetic MQTT msgs and asserts a budget.   |
| Golden       | `tests/golden/`       | `golden`   | Deterministic corpus replay through the detection engine; baselines pinned per scenario. Re-bless via `HERMES_GOLDEN_UPDATE=1`. |
| E2E          | `tests/e2e/`          | (planned)  | Playwright on the built UI. Phase 5+.                                |

```bash
# Default test run — unit only
uv run pytest tests/unit -q

# Bench only (assert no perf regression)
uv run pytest -m bench -s

# Integration suite
docker compose -f docker-compose.dev.yml up -d postgres
DATABASE_URL=postgresql+asyncpg://hermes_migrate:test@localhost:5432/hermes_test \
MIGRATE_DATABASE_URL=postgresql://hermes_migrate:test@localhost:5432/hermes_test \
  uv run pytest -m db -q

# Everything
uv run pytest -q
```

Tests that mock the database, MQTT broker, or filesystem are rejected
unless there's a concrete reason — see
[`CONTRIBUTING.md`](./CONTRIBUTING.md) §5.

---

## Performance

The throughput bench at
[`tests/bench/test_throughput.py`](./tests/bench/test_throughput.py)
is the source of truth.

| Release            | msg/s on laptop | est. msg/s on Pi 4 | Headroom over 2 000 target |
| ------------------ | --------------- | ------------------ | -------------------------- |
| `alpha.12`         | 8 589           | ~3 000             | 1.5×                       |
| `alpha.14` (L1)    | 16 746          | ~5 500             | 2.7×                       |
| `alpha.15` (L1+L3) | 17 117          | ~5 500 / shard     | 2.7× per shard, 4× cores   |

Layer 3 doesn't increase per-shard throughput — its purpose is to use
all 4 cores so the **total system** capacity scales with
`shard_count × per-shard`. Bursts that stalled a single process now
spread across cores.

Prometheus metrics are exposed at `GET /api/metrics` (text format).
Counters/gauges/histograms are listed at the top of
[`services/hermes/metrics.py`](./services/hermes/metrics.py).

---

## Configuration reference

All deployment configuration is via environment variables, validated
by `pydantic-settings` in
[`services/hermes/config.py`](./services/hermes/config.py). A
misconfigured deployment fails fast at process start rather than two
hours into a soak.

### Required

| Variable                | Purpose                                                                |
| ----------------------- | ---------------------------------------------------------------------- |
| `DATABASE_URL`          | asyncpg URL, e.g. `postgresql+asyncpg://hermes_app:pw@host:5432/hermes` |
| `MIGRATE_DATABASE_URL`  | psycopg URL with DDL privileges (separate role from app)               |
| `HERMES_JWT_SECRET`     | HMAC key for JWT signing. ≥ 32 bytes. Rotating invalidates sessions.   |

### MQTT

| Variable                | Default       | Purpose                                                  |
| ----------------------- | ------------- | -------------------------------------------------------- |
| `MQTT_HOST`             | `localhost`   | Broker host                                              |
| `MQTT_PORT`             | `1883`        | Broker port                                              |
| `MQTT_USERNAME`         | `""`          | Optional broker auth                                     |
| `MQTT_PASSWORD`         | `""`          | Optional broker auth                                     |
| `MQTT_TOPIC_ADC`        | `stm32/adc`   | Inbound topic for STM32 ADC messages                     |
| `MQTT_TOPIC_EVENTS_PREFIX` | `stm32/events` | Outbound prefix; full topic is `<prefix>/<dev>/<sid>/<TYPE>` |

### Detection

| Variable                | Default | Purpose                                                                  |
| ----------------------- | ------- | ------------------------------------------------------------------------ |
| `EVENT_TTL_SECONDS`     | `5.0`   | TTL gate dedup window. Within this window same-type events merge,        |
|                         |         | lower-priority types are blocked, BREAK bypasses.                        |
| `LIVE_BUFFER_MAX_SAMPLES` | `2000` | Per-device ring buffer depth. At 100 Hz this is ~20 s of history.        |
| `MQTT_DRIFT_THRESHOLD_S`  | `5.0`  | Re-anchor STM32 clock if computed wall time diverges from receive time   |
|                         |         | by more than this.                                                       |

Detection thresholds (Type A/B/C/D plus mode switching) are NOT
controlled by env vars — they're written via `PUT /api/config/...` and
stored as `parameters` rows scoped GLOBAL / DEVICE / SENSOR. Mode
switching is `enabled=False` by default so existing deployments behave
identically to alpha.16.

### Multi-shard (Layer 3, see `docs/design/MULTI_SHARD.md`)

| Variable                | Default     | Purpose                                                                |
| ----------------------- | ----------- | ---------------------------------------------------------------------- |
| `HERMES_INGEST_MODE`    | `all`       | One of `all` (default single-process), `shard` (one of N detection),   |
|                         |             | or `live_only` (API process keeping live ring buffer warm).            |
| `HERMES_SHARD_COUNT`    | `1`         | Number of detection shards. Must be > 1 when mode = `shard`.            |
| `HERMES_SHARD_INDEX`    | `0`         | This process's shard index. Must satisfy `0 ≤ index < shard_count`.    |

### Observability

| Variable                | Default | Purpose                                                            |
| ----------------------- | ------- | ------------------------------------------------------------------ |
| `HERMES_LOG_FORMAT`     | `json`  | `json` for production aggregators; `console` for human-readable dev. |
| `HERMES_METRICS_ENABLED` | `true` | Prometheus exposition on the API process.                          |
| `HERMES_METRICS_PORT`   | `9090`  | (Reserved for a future dedicated /metrics listener.)                |

### API server

| Variable                  | Default     | Purpose                                              |
| ------------------------- | ----------- | ---------------------------------------------------- |
| `HERMES_API_HOST`         | `0.0.0.0`   | Bind address                                         |
| `HERMES_API_PORT`         | `8080`      | Bind port                                            |
| `HERMES_API_WORKERS`      | `1`         | uvicorn worker count                                 |
| `HERMES_API_LOG_LEVEL`    | `info`      | `debug` / `info` / `warning` / `error`               |
| `HERMES_JWT_EXPIRY_SECONDS` | `3600`    | JWT lifetime                                         |
| `HERMES_DEV_MODE`         | `false`     | `true` enables the auth bypass (Phase 1 dev shim).   |

### OTP / email

| Variable                | Default                             | Purpose                       |
| ----------------------- | ----------------------------------- | ----------------------------- |
| `SMTP_HOST`             | `smtp.gmail.com`                    | OTP delivery                  |
| `SMTP_PORT`             | `587`                               |                               |
| `SMTP_USER`             | `""`                                |                               |
| `SMTP_PASS`             | `""`                                |                               |
| `SMTP_FROM`             | `""`                                |                               |
| `OTP_EXPIRY_SECONDS`    | `300`                               |                               |
| `OTP_MAX_ATTEMPTS`      | `5`                                 |                               |
| `OTP_RESEND_COOLDOWN_SECONDS` | `60`                          |                               |
| `OTP_MAX_PER_HOUR`      | `5`                                 |                               |
| `ALLOWED_EMAILS_PATH`   | `./config/allowed_emails.txt`        | Operator allowlist            |

---

## Production deployment

Production targets a Raspberry Pi 4 with TimescaleDB, Mosquitto, and
nginx all on the same host. Services run under systemd, not Docker
(lower memory overhead and better systemd integration on Pi).

The systemd unit files in [`packaging/systemd/`](./packaging/systemd/)
ship in the .deb. The default install is single-process; multi-shard
is one env-file change away. See
[`docs/design/MULTI_SHARD.md`](./docs/design/MULTI_SHARD.md) §7 for
the step-by-step deployment and rollback procedures.

The .deb itself, the nginx config, and the logrotate rules land in
Phase 9 — see [`packaging/README.md`](./packaging/README.md).

---

## Security

Found a vulnerability? Do **NOT** open a public issue. Follow
[`SECURITY.md`](./SECURITY.md).

The auth model:

- Login uses email + 6-digit OTP delivered by SMTP.
- Sessions issue a JWT (HS256, 32+ byte secret, 1 h default expiry).
- All API routes except `/api/auth/*`, `/api/health`, and `/api/metrics`
  require the JWT.
- Rotating `HERMES_JWT_SECRET` invalidates every active session — by
  design.

Until the auth flow is fully wired, `HERMES_DEV_MODE=1` enables a
bypass that mints a stub user. **Do not set this in production.**

---

## License

Proprietary — EmbedSquare. See [`LICENSE`](./LICENSE). Do not redistribute.
