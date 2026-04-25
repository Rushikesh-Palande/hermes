# DEVELOPMENT.md — local dev environment

> **Audience:** a new developer who just cloned the repo and wants to
> run HERMES end-to-end on their machine. Walks the prerequisites,
> the docker compose dev stack, the four moving pieces (Postgres,
> MQTT, FastAPI, SvelteKit), and the daily-driver workflow.
>
> **Companion docs:**
> - [`TESTING.md`](./TESTING.md) — how to run + write tests
> - [`BACKEND.md`](./BACKEND.md) — Python module map
> - [`UI.md`](./UI.md) — frontend deep dive
> - [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) — branching + commit conventions

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [One-time setup](#2-one-time-setup)
3. [Daily run loop](#3-daily-run-loop)
4. [Project layout cheat sheet](#4-project-layout-cheat-sheet)
5. [Useful commands](#5-useful-commands)
6. [Debugging — common pitfalls](#6-debugging--common-pitfalls)
7. [IDE setup notes](#7-ide-setup-notes)
8. [Where to look when stuck](#8-where-to-look-when-stuck)

---

## 1. Prerequisites

| Tool | Version | Why |
|------|---------|-----|
| **Python 3.11+** | 3.11 or later | Runtime. 3.12 works. 3.13 untested. |
| **uv** | 0.5+ | Dependency manager + virtualenv. Replaces pip / poetry / pyenv combo |
| **Node 20+** | 20 LTS | UI build / dev server |
| **pnpm** | 9+ | Node package manager. Run `corepack enable` after Node install |
| **Docker** | Docker Desktop or Engine + Compose v2 | Postgres + Mosquitto + Redis dev stack |
| **Git** | 2.40+ | Branch / merge flow |
| **(Optional) `psql`** | 16+ | For running migrations from your shell |
| **(Optional) `mosquitto-clients`** | any | `mosquitto_pub` / `mosquitto_sub` for manual MQTT testing |

**Operating system**: Linux native, macOS, or Windows-via-WSL2 all
work. Windows-native (no WSL) is untested. The maintainer's daily
driver is WSL2 with Windows Docker Desktop.

---

## 2. One-time setup

```bash
# 1. Clone
git clone git@github.com:Rushikesh-Palande/hermes.git
cd hermes

# 2. Copy the env template and edit
cp .env.example .env
# fill in HERMES_JWT_SECRET (any 32+ char string for dev)
# leave SMTP_USER blank — OTPs will print to the console

# 3. Bring up the dev stack (Postgres + TimescaleDB + Mosquitto + Redis)
docker compose -f docker-compose.dev.yml up -d

# 4. Apply migrations
./scripts/db-migrate.sh
# requires psql; alternative: docker compose exec postgres psql -U hermes_migrate -d hermes -f /migrations/0001_init_extensions.sql
# (and so on for every file)

# 5. Python deps + venv
uv sync --extra dev
# creates .venv/, installs everything from uv.lock + dev extras

# 6. UI deps
cd ui
pnpm install
cd ..
```

After step 6 you're ready to run.

### What's in the docker compose stack?

`docker-compose.dev.yml` brings up:

```
hermes-postgres   timescale/timescaledb:2.17.2-pg16  port 5432
hermes-mosquitto  eclipse-mosquitto:2.0.20           port 1883, 9001
hermes-redis      redis:7.4-alpine                   port 6379  (reserved for future use)
```

Volumes are named (`hermes-dev_pgdata` etc.) so data persists across
`down`/`up` cycles. Wipe with:

```bash
docker compose -f docker-compose.dev.yml down -v
```

### Optional: a tiny synthetic MQTT publisher

There isn't one shipped (the legacy repo had `mqtt_test_publisher.py`
which we haven't ported). To smoke-test the pipeline:

```bash
mosquitto_pub -h localhost -p 1883 -t stm32/adc -m \
  '{"device_id": 1, "ts": 1000, "adc1": [50,50,50,50,50,50], "adc2": [50,50,50,50,50,50]}'
```

The live chart on `/devices/1` will paint a flat line at 50 from
this. For continuous data, wrap it in a shell loop or use the bench
harness (`tests/bench/test_throughput.py`) which seeds a queue
directly.

---

## 3. Daily run loop

Three terminal windows is the smoothest workflow:

```
┌──────────────── Terminal 1 ────────────────┐
│ docker compose -f docker-compose.dev.yml up│
│ (Postgres + Mosquitto + Redis, foreground) │
└─────────────────────────────────────────────┘

┌──────────────── Terminal 2 ────────────────┐
│ uv run hermes-api                           │
│ (FastAPI on :8080, auto-reloads on edit    │
│  if you launch with --reload)               │
└─────────────────────────────────────────────┘

┌──────────────── Terminal 3 ────────────────┐
│ uv run hermes-ingest                        │
│ (MQTT consumer + detection workers)        │
└─────────────────────────────────────────────┘

┌──────────────── Terminal 4 (UI) ────────────┐
│ cd ui                                       │
│ pnpm dev                                    │
│ (Vite on :5173, HMR enabled)               │
└─────────────────────────────────────────────┘
```

Open `http://localhost:5173`. The Vite dev server proxies `/api/*` to
`http://localhost:8080`, so same-origin holds.

In dev mode (`HERMES_DEV_MODE=1` in `.env`), the auth guard is
bypassed — you don't need to go through the OTP flow on every reload.

### Editing flow

- **Python edit**: kill `hermes-api` / `hermes-ingest`, re-run.
  Or use `--reload` on uvicorn for the API (set in `Settings`?
  no — pass `uvicorn services.hermes.api.main:create_app --reload`
  manually). Ingest doesn't have a reload mode; just restart.
- **UI edit**: HMR picks up the change instantly. Full reload only
  needed for tsconfig / Vite config changes.
- **Migration edit**: never edit a past migration. Add a new file
  `0NNN_<slug>.sql` and re-run `./scripts/db-migrate.sh` (idempotent).
- **Model edit**: SQLAlchemy follows the SQL. After adding a column
  via migration, mirror it in `services/hermes/db/models.py`.

### Sign-in flow (when `HERMES_DEV_MODE` is unset)

1. Hit `http://localhost:5173/login`.
2. Enter your email (must be in `config/allowed_emails.txt`).
3. The OTP is logged to the `hermes-api` terminal because
   `SMTP_USER` is blank in dev. Copy the 6 digits.
4. Enter them in the UI; you're in.

---

## 4. Project layout cheat sheet

```
hermes/
├── README.md                ← start here
├── CHANGELOG.md             ← per-release detail
├── CONTRIBUTING.md          ← branch + commit conventions
├── pyproject.toml           ← Python deps + tool config
├── uv.lock                  ← pinned versions
├── docker-compose.dev.yml   ← Postgres + Mosquitto + Redis dev stack
├── docs/
│   ├── guides/              ← THIS doc + WORKFLOW + BACKEND + UI + EVENTS + ...
│   ├── design/              ← rewrite design + DATABASE_SCHEMA + REST_API
│   ├── contracts/           ← FROZEN legacy contracts (do not modify)
│   └── reference/           ← FROZEN legacy reference library
├── migrations/              ← SQL, applied in lex order by db-migrate.sh
├── scripts/                 ← dev shell scripts (db-migrate, dev-up, dev-down)
├── packaging/               ← systemd units + .deb metadata (Phase 9)
├── services/hermes/         ← Python package
│   ├── api/                 ← FastAPI app
│   ├── auth/                ← OTP + JWT + Fernet
│   ├── db/                  ← engine + models
│   ├── detection/           ← detector pipeline
│   ├── ingest/              ← MQTT consumer + Modbus poller
│   ├── config.py            ← Settings (every env var)
│   ├── logging.py
│   └── metrics.py
├── ui/                      ← SvelteKit (TypeScript + Tailwind + uPlot)
│   └── src/
│       ├── lib/             ← shared API client + types + LiveChart
│       └── routes/          ← one folder per page
├── tests/
│   ├── unit/                ← fast, no I/O
│   ├── integration/         ← real Postgres
│   ├── bench/               ← throughput regression
│   └── golden/              ← deterministic replay vs baseline
└── .github/
    └── workflows/ci.yml     ← lint + typecheck + 4 pytest tiers
```

---

## 5. Useful commands

### Python

```bash
# Install deps + dev extras
uv sync --extra dev

# Run a one-off Python with the venv
uv run python -c "import hermes; print(hermes.__version__)"

# Lint + format check (CI runs these)
uv run ruff check services tests
uv run ruff format --check services tests

# Apply auto-fixes
uv run ruff check services tests --fix
uv run ruff format services tests

# Type-check
uv run mypy services

# Unit tests (fast, no I/O)
uv run pytest tests/unit -q

# Everything except slow (db, mqtt, bench)
uv run pytest -m 'not db and not mqtt and not bench' -q

# Bench
uv run pytest -m bench -s

# Integration (needs the dev Postgres up)
uv run pytest -m db -q

# Golden tier
uv run pytest -m golden -q

# Re-bless golden baselines (only after an intentional behaviour change)
HERMES_GOLDEN_UPDATE=1 uv run pytest -m golden -q
```

### UI

```bash
cd ui

# Dev server with HMR
pnpm dev

# Type-check + svelte-check (CI runs this)
pnpm check

# Production build (Node adapter — `node build`)
pnpm build

# Refresh node_modules (rare; pnpm 9 is reliable)
rm -rf node_modules
pnpm install
```

### Database

```bash
# Apply migrations
./scripts/db-migrate.sh

# Wipe + re-migrate (DANGER — destroys data)
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d postgres
./scripts/db-migrate.sh

# Open a shell
docker exec -it hermes-postgres psql -U hermes_migrate -d hermes
```

### MQTT

```bash
# Subscribe to all events
mosquitto_sub -h localhost -p 1883 -t 'stm32/events/#' -v

# Inject a single ADC frame
mosquitto_pub -h localhost -p 1883 -t stm32/adc -m \
  '{"device_id":1,"ts":1000,"adc1":[10,20,30,40,50,60],"adc2":[70,80,90,100,110,120]}'
```

### Git

```bash
# Branch off develop for a feature
git checkout develop && git pull
git checkout -b feature/<phase>-<slug>

# Branch off develop for a fix
git checkout develop && git pull
git checkout -b fix/<slug>

# Release (manual, see CONTRIBUTING.md §7)
# usually: merge feature → develop, bump pyproject + CHANGELOG,
# merge develop → main, tag v0.1.0-alpha.X
```

---

## 6. Debugging — common pitfalls

### "Port 5432 is already in use"

Another Postgres is bound. Either:
- Stop it (`brew services stop postgresql` / `systemctl stop postgresql`).
- Or rebind the dev one to 5433: edit `docker-compose.dev.yml` →
  `ports: ["5433:5432"]`, update `DATABASE_URL` and
  `MIGRATE_DATABASE_URL` to use `:5433`.

### "psql not found" when running db-migrate.sh

Install `postgresql-client` (apt/brew) — the script needs `psql` in
PATH. Alternative: `docker exec hermes-postgres psql -U hermes_migrate -d hermes -f /migrations/0001_init_extensions.sql` (one per migration file).

### Tests can't connect on Windows-via-WSL

If you're running pytest from WSL bash and the venv is at
`./.venv/Scripts/pytest.exe` (Windows-side install), env vars don't
propagate to the Windows .exe by default. Use:

```bash
export DATABASE_URL=...
export MIGRATE_DATABASE_URL=...
export WSLENV="DATABASE_URL:MIGRATE_DATABASE_URL"
./.venv/Scripts/pytest.exe -m db
```

`WSLENV` is the Microsoft-recommended forwarding mechanism.

### "VSCode shows 'Cannot find module' but `tsc` is clean"

The TS server in VSCode caches stale module resolution. Open the
command palette, run "TypeScript: Restart TS Server". This is also
true for newly-added `$lib/types.ts` exports.

### "JSON decode error in golden tests"

If you see `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`
on a `tests/golden/corpora/*.ndjson` file, it's almost certainly Git
LFS-tracked but you don't have LFS installed. The synthetic corpora
shouldn't go to LFS (only `tests/golden/captures/**` does). Fix:
`git lfs pull` or check `.gitattributes` for accidental wildcards.

### "Live chart doesn't paint anything"

Check the EventSource connection in browser devtools (Network →
type=eventsource). If it's connected but `samples: []` every tick,
no data is hitting the broker. Inject an MQTT message manually
(see §5 above) and confirm `_consume` log lines appear in the
`hermes-ingest` terminal.

### "Detection isn't firing"

Quick checklist:
1. Is the type's `enabled = true`? Check via `GET /api/config/type_a`.
2. Is the window primed? Detector waits for
   `init_fill_ratio × T × expected_sample_rate_hz` samples.
3. Is mode switching gating? Check sensor mode (currently no UI;
   `Settings.mode_switching` info via `/api/system-tunables`).
4. Is the threshold actually being crossed? Inspect via the live
   chart or the `events` table.

[`EVENTS.md`](./EVENTS.md) §11 has a full debugging matrix.

---

## 7. IDE setup notes

### VSCode

Install:
- **Python** (Microsoft) — Pylance + venv detection
- **Ruff** (Astral Software) — runs on save
- **Svelte for VS Code** (Svelte) — syntax + intellisense
- **Tailwind CSS IntelliSense** (Tailwind Labs)

`.vscode/settings.json` (recommended; not committed):

```json
{
    "python.analysis.typeCheckingMode": "strict",
    "editor.formatOnSave": true,
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff"
    },
    "[svelte]": {
        "editor.defaultFormatter": "svelte.svelte-vscode"
    },
    "[typescript]": {
        "editor.defaultFormatter": "svelte.svelte-vscode"
    }
}
```

### PyCharm

The default Python interpreter wants `.venv/bin/python` (Linux/macOS)
or `.venv\Scripts\python.exe` (Windows). Mark `services/` and `tests/`
as "Sources Root". The "Show Whitespace" + "Show Indent Guides"
settings are useful for SQL alignment.

---

## 8. Where to look when stuck

| Situation | Read |
|-----------|------|
| Don't know what something does | grep the docstring; every file has a top-of-file rationale |
| API returns 422 / 409 / 500 | `services/hermes/api/routes/<resource>.py`; FastAPI's response includes the `detail` |
| Detection is wrong | [`EVENTS.md`](./EVENTS.md) + the legacy contract `docs/contracts/EVENT_DETECTION_CONTRACT.md` |
| Schema is wrong | `migrations/00*.sql` is the source of truth; `db/models.py` follows |
| UI bug | `pnpm check` first; surfaces drift between Python and TypeScript types |
| Performance regression | `tests/bench/test_throughput.py` + the historical numbers in [`README.md`](../../README.md) §Performance |
| Behaviour drift suspicion | `tests/golden/` harness + baselines |
| Operations question | (planned) `docs/operations/` — for now, `packaging/systemd/` + `packaging/README.md` |
| Architectural question | [`../design/ARCHITECTURE.md`](../design/ARCHITECTURE.md) |

When all else fails: ask in a Discussion (per
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §8).
