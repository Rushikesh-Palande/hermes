# HERMES

**High-frequency industrial sensor monitoring, event detection, and operator dashboard.**

[![License: Proprietary](https://img.shields.io/badge/license-Proprietary-red.svg)](./LICENSE)
[![Status: Pre-Alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)](./CHANGELOG.md)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Node: 20+](https://img.shields.io/badge/node-20%2B-green.svg)](https://nodejs.org/)

HERMES ingests 12-channel ADC telemetry from STM32 hardware over MQTT at ~123 Hz, runs four parallel event-detection algorithms (A/B/C/D + BREAK mode transition), and presents live + historical data through a browser dashboard. Designed for continuous unattended operation on Raspberry Pi in industrial environments.

---

## Status

This is a **ground-up rewrite** of the legacy HERMES dashboard. Development is tracked in 11 phases; see [`docs/design/`](./docs/design/) and [`CHANGELOG.md`](./CHANGELOG.md).

| Phase | Scope | State |
|-------|-------|-------|
| 0.5 | Behaviour contract capture from legacy code | ✅ Complete |
| 1   | Foundation (scaffold, DB migrations, auth, device CRUD) | 🟡 In progress |
| 2   | MQTT ingestion pipeline | Pending |
| 3   | Detection engine — Type A + golden harness | Pending |
| 4   | Detection engine — Types B/C/D + mode switching | Pending |
| 5   | Frontend foundation (SvelteKit, layout, auth) | Pending |
| 6   | Live device dashboard (uPlot chart, SSE) | Pending |
| 7   | Packages + Sessions UI | Pending |
| 8   | Event history, detail window, CSV export | Pending |
| 9   | Ops: `.deb` packaging, systemd, nginx, backup | Pending |
| 10  | Legacy migration + full golden diff | Pending |
| 11  | UAT, polish, bug bash | Pending |

---

## Architecture

```
┌─────────────┐     MQTT     ┌──────────────┐    ┌────────────┐
│   STM32     │ ───────────> │ hermes-ingest│ ─> │            │
│  ~123 Hz    │ stm32/adc    │ (consumer +  │    │ PostgreSQL │
│  12 sensors │              │  detection)  │    │ +Timescale │
└─────────────┘              └──────────────┘    │            │
                                                 └─────┬──────┘
                             ┌──────────────┐          │
                             │   hermes-api │ <────────┘
                             │  (FastAPI +  │
                             │   SSE live)  │
                             └──────┬───────┘
                                    │ HTTPS + SSE
                             ┌──────▼───────┐
                             │ SvelteKit UI │
                             │   (uPlot)    │
                             └──────────────┘
```

- **Data model:** packages (immutable once used) → sessions (global + local overrides) → events (one row per trigger). See [`docs/design/DATABASE_REDESIGN.md`](./docs/design/DATABASE_REDESIGN.md).
- **Behaviour parity:** detection output is byte-identical to the legacy system except where [`docs/contracts/BUG_DECISION_LOG.md`](./docs/contracts/BUG_DECISION_LOG.md) explicitly allows divergence. Enforced by [`docs/contracts/GOLDEN_TRAFFIC_PLAN.md`](./docs/contracts/GOLDEN_TRAFFIC_PLAN.md).

---

## Quick start (development)

**Prerequisites**

- Docker Desktop (or Docker Engine + Compose v2)
- Python 3.11+ with [`uv`](https://docs.astral.sh/uv/) installed
- Node.js 20+ with `pnpm` (`corepack enable`)

**Bring up the stack**

```bash
git clone git@github.com:Rushikesh-Palande/hermes.git
cd hermes

# 1. Copy environment template and edit for your local setup
cp .env.example .env
# … fill in SMTP_PASS, DATABASE_URL, etc.

# 2. Start Postgres+Timescale and Mosquitto
docker compose -f docker-compose.dev.yml up -d

# 3. Run migrations
./scripts/db-migrate.sh

# 4. Python services
uv sync
uv run hermes-api        # FastAPI server on :8080
uv run hermes-ingest     # MQTT ingest in another terminal

# 5. UI
cd ui
pnpm install
pnpm dev                 # Vite on :5173
```

Open `http://localhost:5173`.

---

## Repository layout

```
.
├── docs/
│   ├── contracts/           — Behaviour contracts frozen from legacy code
│   ├── reference/           — Per-file legacy reference library
│   └── design/              — Design decisions (database, APIs, auth)
├── migrations/              — PostgreSQL SQL migrations
├── services/hermes/         — Python package (api + ingest + shared)
├── ui/                      — SvelteKit application
├── tests/                   — Pytest suite + golden traffic harness
├── packaging/               — .deb + systemd units (Phase 9)
├── scripts/                 — Dev + ops shell scripts
├── config/                  — Default config files
└── .github/                 — CI, issue templates, dependabot
```

---

## Development workflow

- **Branching:** [`main`](https://github.com/Rushikesh-Palande/hermes/tree/main) is protected. Features branch from `develop` (e.g. `feature/phase-2-ingestion`). See [`CONTRIBUTING.md`](./CONTRIBUTING.md).
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `ci:`, `chore:`, `refactor:`, `test:`, `build:`).
- **Every PR must:** pass CI (ruff, mypy, pytest ≥ 90 % coverage, Playwright E2E, Lighthouse), carry a [`BUG_DECISION_LOG.md`](./docs/contracts/BUG_DECISION_LOG.md) reference if it diverges from legacy, and include a short reviewer test plan.
- **Releases:** tagged on `main` as `v<major>.<minor>.<patch>[-prerelease]`. GitHub Releases are generated from [`CHANGELOG.md`](./CHANGELOG.md) ([Keep a Changelog](https://keepachangelog.com/) format, [SemVer](https://semver.org/)).

---

## Security

Found a vulnerability? Do NOT open a public issue. Follow [`SECURITY.md`](./SECURITY.md).

---

## License

Proprietary — EmbedSquare. See [`LICENSE`](./LICENSE). Do not redistribute.
