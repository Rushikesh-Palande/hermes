<!--
  README.md — public-facing project README.

  Audience: someone who has just landed on the GitHub page and needs
  to decide, in 30 seconds, whether HERMES is what they're looking
  for. The deeper docs live under docs/; this file links there for
  every topic that needs more than a paragraph.

  Style: keep it polished and product-shaped (badges, features list,
  one canonical install path, one architecture diagram). Internal
  process notes (release cadence, gap-tracker, perf-layer plan) live
  in CONTRIBUTING.md / CHANGELOG.md / docs/, not here.
-->

<div align="center">

# HERMES

**High-frequency industrial sensor monitoring, event detection, and operator dashboard.**

[![License: Proprietary](https://img.shields.io/badge/license-Proprietary-red.svg)](./LICENSE)
[![Status: Pre-Alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)](./CHANGELOG.md)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Node: 20+](https://img.shields.io/badge/node-20%2B-339933.svg?logo=node.js&logoColor=white)](https://nodejs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SvelteKit](https://img.shields.io/badge/SvelteKit-FF3E00.svg?logo=svelte&logoColor=white)](https://kit.svelte.dev/)
[![TimescaleDB](https://img.shields.io/badge/TimescaleDB-FDB515.svg?logo=postgresql&logoColor=white)](https://www.timescale.com/)
[![MQTT](https://img.shields.io/badge/MQTT-660066.svg?logo=eclipsemosquitto&logoColor=white)](https://mqtt.org/)

</div>

---

HERMES is a production-ready industrial monitoring stack: it ingests 12-channel
analogue telemetry from STM32 hardware over MQTT at ~100 Hz per sensor, runs
four parallel event-detection algorithms in real time, persists every event
together with its surrounding ±9 s waveform, and presents live and historical
data through a fast, type-safe operator dashboard.

It is designed for continuous unattended operation on commodity edge hardware
— a Raspberry Pi 4 sits comfortably above its 2 000 msg/s production target
with **2.7× headroom**.

```
20 devices  ×  12 sensors  ×  100 Hz  =  2 000 MQTT msg/s  =  24 000 sensor readings/s
```

---

## Features

- **Real-time ingestion** — `paho-mqtt` callback drains directly into an
  asyncio queue, parsed in batches with `orjson`, hot-path `numpy` math.
- **Four detector algorithms running in parallel** — variance / CV%
  (Type A), tolerance band (Type B), absolute bound (Type C), and a
  two-stage drift detector (Type D) — plus a BREAK state machine that
  classifies sensor disconnects vs. real signal change.
- **±9 s event windows** — every event ships with its 18 s of context
  encoded compactly into a TimescaleDB hypertable.
- **TTL gate** — 5 s dedup, type-priority preemption, BREAK bypass —
  collapses redundant detections before they hit the database.
- **Outbound MQTT republish** — detected events are republished on
  `stm32/events/<device>/<sensor>/<TYPE>` for downstream PLC / SCADA.
- **Live dashboard** — SvelteKit 5 (runes) + uPlot. Sub-100 ms render
  latency for 12-channel streams. Event window viewer, session and
  threshold management, audit log, package versioning.
- **Multi-process scaling** — opt-in shard mode splits devices across
  cores via `device_id % N`. Operator config edits propagate to every
  shard via Postgres `LISTEN`/`NOTIFY`.
- **Modbus TCP support** — for legacy PLCs that don't speak MQTT.
- **OTP-based auth** — email + 6-digit code, JWT (HS256), Fernet
  at-rest encryption for stored secrets, derived from a single
  master key.
- **First-class observability** — Prometheus metrics, structured JSON
  logs, `/api/health` and `/api/metrics` endpoints.
- **Zero-mock test discipline** — unit / integration (real Postgres) /
  bench (real load) / golden (deterministic corpus replay).

---

## Quick start

> See [`docs/operations/INSTALLATION.md`](./docs/operations/INSTALLATION.md)
> for the full install guide covering all three paths.

### Option 1 — Docker (any Linux, fastest)

```bash
docker run -d --name hermes \
    -p 8080:8080 \
    -e DATABASE_URL=postgresql+asyncpg://user:pw@host/hermes \
    -e MIGRATE_DATABASE_URL=postgresql://user:pw@host/hermes \
    -e HERMES_JWT_SECRET="$(openssl rand -base64 48 | tr -d '\n')" \
    ghcr.io/rushikesh-palande/hermes:latest
```

Or bring up the full stack (Postgres + Mosquitto + API + ingest):

```bash
curl -fsSL https://raw.githubusercontent.com/Rushikesh-Palande/hermes/main/packaging/docker-compose.prod.yml \
    -o docker-compose.yml
docker compose up -d
```

### Option 2 — Debian package (Pi 4 / Linux servers)

Download the `.deb` for your architecture from
[Releases](https://github.com/Rushikesh-Palande/hermes/releases/latest):

```bash
sudo dpkg -i hermes_<version>_amd64.deb
sudo apt install -f          # resolve declared dependencies
```

The `postinst` hook automatically creates the `hermes` system user,
sets up Postgres + TimescaleDB, generates a JWT secret, builds the
venv + UI, runs migrations, and starts `hermes-api` + `hermes-ingest`
under systemd.

### Option 3 — Air-gapped install

A pre-built `hermes-<version>-<arch>-offline.tar.gz` ships with every
release. Copy to the target host (USB / SCP), then:

```bash
tar -xzf hermes-<version>-amd64-offline.tar.gz
cd hermes
sudo ./packaging/install.sh --offline --operator-email you@your-org.com
```

The bundle contains the source tree, every system `.deb`, a Python
wheelhouse, and a pre-built UI. No internet access required at install
time.

### Local development

```bash
git clone https://github.com/Rushikesh-Palande/hermes.git
cd hermes

# 1. Bring up Postgres + Mosquitto
docker compose -f docker-compose.dev.yml up -d

# 2. Apply migrations
./scripts/db-migrate.sh

# 3. Backend (two terminals)
uv sync --extra dev
uv run hermes-api          # FastAPI on :8080
uv run hermes-ingest       # MQTT consumer

# 4. Frontend
cd ui && pnpm install && pnpm dev   # SvelteKit on :5173
```

Open <http://localhost:5173>. Full setup walk-through with debugging
notes:
[`docs/guides/DEVELOPMENT.md`](./docs/guides/DEVELOPMENT.md).

---

## Architecture

```
┌─────────────┐     MQTT     ┌──────────────────┐    ┌────────────────┐
│   STM32     │ ───────────> │  hermes-ingest   │ ─> │                │
│  ~100 Hz    │ stm32/adc    │  • parse + clock │    │  PostgreSQL    │
│ 12 sensors  │              │  • offsets       │    │  + TimescaleDB │
│ × 20 devs   │              │  • detection A-D │    │                │
└─────────────┘              │  • TTL gate      │    └────────┬───────┘
                             │  • DB sink       │             │
                             │  • MQTT republish│             │
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

For multi-process shard topology (one detection process per CPU core,
all sharing the same Postgres / MQTT / UI), see
[`docs/design/MULTI_SHARD.md`](./docs/design/MULTI_SHARD.md).

The data model — `packages` (immutable threshold sets) → `sessions`
(global + local overrides) → `events` (one row per trigger, ±9 s window
in `event_windows`) — is documented in
[`docs/design/DATABASE_SCHEMA.md`](./docs/design/DATABASE_SCHEMA.md).

---

## Performance

The throughput benchmark at
[`tests/bench/test_throughput.py`](./tests/bench/test_throughput.py) is the
single source of truth, run on every CI build.

| Hardware                      | Sustained throughput | Headroom over 2 000 msg/s target |
| ----------------------------- | -------------------- | -------------------------------- |
| Developer laptop (single core)| ~16 700 msg/s        | **8.4×**                         |
| Raspberry Pi 4 (single core)  | ~5 500 msg/s         | **2.7×**                         |
| Raspberry Pi 4 (4-core shard) | ~22 000 msg/s        | **11×**                          |

Detection latency on the Pi 4 stays under 50 ms p99 from MQTT receive
to `events` row insert.

---

## Documentation

Every part of the system is documented under [`docs/`](./docs/).
Pick the file that matches your goal:

| If you want to ...                                          | Read |
| ----------------------------------------------------------- | ---- |
| Get productive in 30 minutes                                | [`docs/design/ARCHITECTURE.md`](./docs/design/ARCHITECTURE.md) |
| Trace a sensor reading from STM32 to UI to event row        | [`docs/guides/WORKFLOW.md`](./docs/guides/WORKFLOW.md) |
| Map every Python module + responsibility                    | [`docs/guides/BACKEND.md`](./docs/guides/BACKEND.md) |
| Map every SvelteKit page + behaviour                        | [`docs/guides/UI.md`](./docs/guides/UI.md) |
| Understand Type A / B / C / D + BREAK + mode switching      | [`docs/guides/EVENTS.md`](./docs/guides/EVENTS.md) |
| Reference every table, column, index                        | [`docs/design/DATABASE_SCHEMA.md`](./docs/design/DATABASE_SCHEMA.md) |
| Reference every REST endpoint                               | [`docs/design/REST_API.md`](./docs/design/REST_API.md) |
| Reference every env var + DB-backed setting                 | [`docs/guides/CONFIGURATION.md`](./docs/guides/CONFIGURATION.md) |
| Reference every Prometheus metric                           | [`docs/guides/METRICS.md`](./docs/guides/METRICS.md) |
| Set up a local dev environment                              | [`docs/guides/DEVELOPMENT.md`](./docs/guides/DEVELOPMENT.md) |
| Run / write tests across all four tiers                     | [`docs/guides/TESTING.md`](./docs/guides/TESTING.md) |
| Deploy multi-process for scaling / safety                   | [`docs/design/MULTI_SHARD.md`](./docs/design/MULTI_SHARD.md) |
| Install on a fresh Linux box (deb / rpm / Docker / offline) | [`docs/operations/INSTALLATION.md`](./docs/operations/INSTALLATION.md) |

---

## Tech stack

**Backend** — Python 3.11 · FastAPI · SQLAlchemy 2.x async · asyncpg ·
paho-mqtt · pydantic-settings · structlog · prometheus-client · uv

**Frontend** — SvelteKit 5 (runes) · TypeScript · uPlot · Vite · pnpm

**Data** — PostgreSQL 16 · TimescaleDB · Mosquitto MQTT · Modbus TCP

**Infra** — systemd · nginx · Docker · GitHub Actions · `.deb` packaging

---

## Testing

A four-tier strategy with strict no-mock discipline. Tests that mock
the database, MQTT broker, or filesystem are rejected unless there's
a concrete reason.

```bash
# Default — fast unit tests (no I/O)
uv run pytest tests/unit -q

# Integration suite (real Postgres)
uv run pytest -m db

# Throughput benchmark (asserts no perf regression)
uv run pytest -m bench -s

# Golden parity (deterministic corpus replay)
uv run pytest -m golden
```

Full strategy + how to write tests:
[`docs/guides/TESTING.md`](./docs/guides/TESTING.md).

---

## Project layout

```
.
├── services/hermes/      Python package — api + ingest + detection
├── ui/                   SvelteKit application
├── migrations/           PostgreSQL SQL migrations (append-only)
├── packaging/            Production deployment artefacts
│   ├── install.sh        One-shot installer (Path A)
│   ├── Dockerfile        Multi-stage container build (Path B)
│   ├── build-offline-bundle.sh    Air-gapped tarball builder (Path C)
│   ├── debian/           .deb package metadata
│   ├── systemd/          Service units
│   └── nginx/            Reverse-proxy config
├── tests/                unit / integration / bench / golden
├── scripts/              Dev + ops shell scripts
├── docs/                 Guides, design notes, ops runbooks
└── .github/workflows/    CI + release automation
```

---

## Releases

Releases are cut from the `main` branch and published as GitHub
Releases with the `.deb`, offline tarball, and multi-arch container
image attached. The flow is fully automated by
[`.github/workflows/release.yml`](./.github/workflows/release.yml) —
push an annotated `v*` tag and a polished release page appears within
~10 minutes.

Browse all versions: [Releases](https://github.com/Rushikesh-Palande/hermes/releases).
Per-version detail: [`CHANGELOG.md`](./CHANGELOG.md).

---

## Contributing

We follow Conventional Commits, branch off `develop`, and run a no-mock
test discipline. Setup and contribution rules are in
[`CONTRIBUTING.md`](./CONTRIBUTING.md).

---

## Security

Found a vulnerability? Do **not** open a public issue. Follow
[`SECURITY.md`](./SECURITY.md).

---

## License

Proprietary — © EmbedSquare. See [`LICENSE`](./LICENSE). Distribution
and use are restricted; contact <ops@embedsquare.com> for licensing
inquiries.
