# TESTING.md — test tier strategy

> **Audience:** anyone writing or running tests. Documents the four
> test tiers, when to use each, the conventions every tier follows,
> and the rules for what NOT to test (or test wrong).
>
> **Companion docs:**
> - [`DEVELOPMENT.md`](./DEVELOPMENT.md) — how to set up the environment
> - [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) — branch + PR conventions
> - [`tests/golden/README.md`](../../tests/golden/README.md) — golden harness specifics
> - [`../contracts/GOLDEN_TRAFFIC_PLAN.md`](../contracts/GOLDEN_TRAFFIC_PLAN.md) — the parity-vs-legacy spec

---

## Table of contents

1. [Four tiers in one table](#1-four-tiers-in-one-table)
2. [Unit tier — `tests/unit/`](#2-unit-tier)
3. [Integration tier — `tests/integration/`](#3-integration-tier)
4. [Bench tier — `tests/bench/`](#4-bench-tier)
5. [Golden tier — `tests/golden/`](#5-golden-tier)
6. [Test-writing conventions](#6-test-writing-conventions)
7. [Mocking philosophy](#7-mocking-philosophy)
8. [CI matrix](#8-ci-matrix)
9. [Coverage expectations](#9-coverage-expectations)
10. [How to run a single test](#10-how-to-run-a-single-test)

---

## 1. Four tiers in one table

```
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│   tests/unit/         tests/integration/     tests/bench/   tests/golden/│
│   ────────────        ──────────────────    ──────────────  ─────────────│
│   • no I/O            • real Postgres       • throughput    • replay     │
│   • no DB             • via docker-compose  • assertion-    • corpus →   │
│   • no MQTT           • marker: db          •  based perf   •  detection │
│   • no async loop                                            •  → baseline│
│                       • marker: db          • marker: bench • marker:    │
│   marker: (none)                            •  golden                    │
│                                                                          │
│   ~200 tests          ~133 tests            1 test          3 tests      │
│   ~5 s                ~120 s                ~1 s            ~1 s         │
│                                                                          │
│   "does this pure     "does this           "did we make    "did the    │
│    function           SQL/REST round-       throughput     output       │
│    behave?"           trip work?"           regress?"      drift?"      │
└──────────────────────────────────────────────────────────────────────┘
```

`pytest -m '<expr>'` selects tiers. Default `pytest tests/unit -q` is
the fast loop most people run on every save.

---

## 2. Unit tier

`tests/unit/`. Marker: none (default tier).

### Rules

- **No I/O at all.** No network, no disk (other than reading test
  fixtures), no clocks. If you call `time.time()` in a test, you're
  doing it wrong — pass a `ts` argument and assert against a known value.
- **No async loop unless the SUT requires it.** Pytest-asyncio is
  configured (`asyncio_mode = auto` in `pyproject.toml`); use
  `async def test_...` for code under test that's async (the
  detection engine is sync, the writers are async).
- **One assertion focus per test.** Multiple `assert` statements are
  fine when they all assert facets of the same outcome ("event was
  emitted AND its triggered_at is X AND its metadata has Y"). Don't
  bundle unrelated checks.
- **Test the contract, not the implementation.** The detector's CV%
  formula doesn't matter to the test; the FIRE / NO-FIRE decision and
  the `metadata` shape do.

### What lives here

| File pattern | Tests |
|--------------|-------|
| `test_type_a.py`, `test_type_b.py`, ... | Each detector's fire/no-fire matrix |
| `test_sliding_window.py` | `IncrementalSlidingWindow` math |
| `test_ttl_gate.py` | All four TTL rules + BREAK bypass + flush |
| `test_mode_switching.py` | Every state transition + grace windows |
| `test_engine_mode_gating.py` | Engine + state machine integration (no DB) |
| `test_consume_shard.py` | Shard math + live_only behaviour |
| `test_shard_config.py` | Settings validator |
| `test_db_config_scope_walk.py` | Scope resolution (cache injected directly, no DB) |
| `test_clock.py` | Anchor + drift + re-anchor |
| `test_offsets.py` | OffsetCache.apply |
| `test_parser.py` | STM32 payload → dict |
| `test_window_buffer.py` | EventWindowBuffer slice |
| `test_live_data.py` | LiveDataHub push + since |
| `test_encoding.py` | Window encode/decode round-trip |
| `test_metrics*.py` | Counter/gauge/histogram observers |
| `test_health.py` | `/api/health` |
| `test_secret_box.py` | Fernet round-trip + tampering |
| `test_session_samples_writer.py` | Hot-path resolution + drop semantics (no DB) |
| `test_modbus_config.py` | ModbusConfig validation |
| `test_auth_helpers.py` | OTP hashing + JWT issue/verify |
| `test_mqtt_event_sink.py` | Outbound MQTT shape |
| `test_detection_engine.py` | Engine fan-out without DB |
| `test_config.py` | Settings env-var loading |

Roughly 200 tests, ~5 seconds. Run them on every save — they're
your fast feedback loop.

### Anti-patterns to avoid

- **Mocking the database.** If the test needs a DB, it goes in the
  integration tier. Don't `unittest.mock.patch("services.hermes.db.engine.async_session")`.
- **Sleeping.** `time.sleep(...)` is a smell. The detection clock is
  passed in via `ts`; the writers use mock clocks; even SSE tests
  feed events explicitly.
- **Real network calls.** Pytest doesn't run on the production network.
  No `requests.get(...)`, no `paho.connect(...)`.

---

## 3. Integration tier

`tests/integration/`. Marker: `db`.

### Rules

- **Real Postgres.** A Timescale 2.17 instance, brought up via
  `docker compose -f docker-compose.dev.yml up -d postgres`.
- **TRUNCATE between tests, not DROP SCHEMA.** See
  `tests/integration/conftest.py` docstring — TimescaleDB extension
  state can't survive a schema drop in the same backend session. The
  fixture applies migrations once per session, then `TRUNCATE TABLE
  ... RESTART IDENTITY CASCADE` between tests.
- **Real HTTP via `httpx.AsyncClient(transport=ASGITransport(app))`** —
  no real socket, but the full FastAPI request lifecycle.
- **Test the contract, not the implementation.** Assert on response
  bodies + DB rows, not on which SQL queries ran.

### What lives here

```
tests/integration/
├── conftest.py                  schema reset + api_client fixture
├── test_auth_*.py               OTP + JWT round-trips
├── test_config_*.py             /api/config CRUD + scope walk
├── test_devices_api.py
├── test_event_persistence.py    end-to-end MQTT → events row
├── test_events_api.py
├── test_events_export.py
├── test_health_db.py            /api/health/ready against real DB
├── test_jwt_round_trip.py
├── test_migrations.py           every migration applies + every table exists
├── test_modbus_poller.py        pymodbus simulator + ModbusManager
├── test_mqtt_brokers_api.py
├── test_offsets_api.py
├── test_packages_api.py
├── test_session_samples_writer.py
├── test_sessions_api.py
└── test_system_tunables_api.py
```

Roughly 133 tests, ~120 seconds. Run before pushing.

### Bringing the dev DB up

The CI workflow brings up Postgres as a service container with these
exact env vars:

```yaml
POSTGRES_USER: hermes_migrate
POSTGRES_PASSWORD: test
POSTGRES_DB: hermes_test
```

To match locally:

```bash
docker run -d --name hermes-postgres -p 5432:5432 \
    -e POSTGRES_USER=hermes_migrate \
    -e POSTGRES_PASSWORD=test \
    -e POSTGRES_DB=hermes_test \
    timescale/timescaledb:2.17.2-pg16

export DATABASE_URL=postgresql+asyncpg://hermes_migrate:test@localhost:5432/hermes_test
export MIGRATE_DATABASE_URL=postgresql://hermes_migrate:test@localhost:5432/hermes_test
uv run pytest -m db -q
```

If port 5432 is taken, see [`DEVELOPMENT.md`](./DEVELOPMENT.md) §6
for the alt-port workaround (and the WSLENV trick if you're on WSL).

---

## 4. Bench tier

`tests/bench/test_throughput.py`. Marker: `bench`.

### Rules

- **One test per release**. Adding more makes the bench noisier
  without adding signal.
- **Pre-fill the queue, then drain.** No real broker. Synthetic 2000
  messages fed straight into the asyncio queue, then `_consume` drains
  to completion.
- **Assert two things**:
  1. Wall-clock budget — `DRAIN_BUDGET_SECONDS` (currently 6.0 s on
     CI runners).
  2. No silent drops — `MSGS_RECEIVED_TOTAL` ticked exactly the input
     count.
- **Print msgs/s + samples/s** to stdout so PR reviewers can eyeball
  the regression. CI captures these in the build log.

### Why it's not flaky

- Pre-filled queue means consumer doesn't wait on the network.
- Detection engine is enabled with disabled detectors (no fires).
- Single asyncio loop, no real I/O.
- The 6-second budget has 4× headroom over local laptop runs.

### How to inspect a regression

```bash
uv run pytest -m bench -s
```

Note the `[bench] drained 2000 msgs in 0.119s (16746 msg/s, 200949 samples/s)`
line. Compare to the historical numbers in
[`../../README.md`](../../README.md) §Performance:

```
alpha.12 → alpha.14   2× from Layer 1 (orjson + locals + drop debug log)
alpha.17              ~18% drop from mode-switching gating (acceptable cost)
alpha.20              no change (writer is fast no-op when no recording)
alpha.21              no change (Modbus path doesn't touch _consume)
```

Sustained <10 000 msg/s on a clean machine = something regressed.
Bisect with `git bisect run pytest -m bench`.

---

## 5. Golden tier

`tests/golden/`. Marker: `golden`.

### What it does

Replays a recorded (or synthetic) MQTT trace through the rewrite's
detection pipeline deterministically and asserts the resulting event
list matches a saved baseline.

```
corpus.ndjson ──► harness.replay() ──► [CapturedEvent, ...]
                                              │
                                              ▼
                                        compare to
                                              │
                                              ▼
                                       baseline.events.ndjson
                                       (saved sort-keyed JSON-per-line)
```

### Rules

- **Deterministic.** No real-time clock, no network, no sleep.
  `recv_ts` from each frame in the corpus drives the harness clock.
  A 24h trace runs in seconds.
- **Strict comparison.** `actual == expected` per row, including
  metadata shape. Any drift fails loudly.
- **Re-bless with intent.** `HERMES_GOLDEN_UPDATE=1 pytest -m golden`
  overwrites baselines. Only do this after a deliberate behaviour
  change documented in `docs/contracts/BUG_DECISION_LOG.md`. Every
  blessed update should be paired with a CHANGELOG entry.

### What's there today

Three synthetic corpora (kB-sized, plain text in git):

| Corpus | Scenario | Events expected |
|--------|----------|-----------------|
| `type_a_high_variance.ndjson` | sensor 1 develops square-wave high CV%. | Multiple Type A fires on sensor 1 only |
| `mode_break.ndjson` | sensor 5 sustains above-startup, then drops below break_threshold | Exactly one BREAK with `triggered_at = first below-threshold sample` |
| `stable_sine.ndjson` | low-amplitude sine on all 12 sensors | Zero events (smoke baseline) |

### What's NOT there yet

- Real production-hardware MQTT captures (~30 MB compressed each per
  `GOLDEN_TRAFFIC_PLAN.md` §1.2). Will land in `tests/golden/captures/`
  via Git LFS when production-hardware capture window is scheduled.
- Comparison vs. the legacy `observed.sqlite` — the contract defines
  the diff shape; we'll bolt it on top of the existing replay engine.
- Outbound MQTT publish capture — the harness collects events but not
  the parallel `MqttEventSink` publishes. Tracked follow-up.

### Running

```bash
# Default — assert vs baselines
uv run pytest -m golden -q

# Re-bless after a deliberate behaviour change
HERMES_GOLDEN_UPDATE=1 uv run pytest -m golden -q
git diff tests/golden/baselines/   # eyeball before commit
```

---

## 6. Test-writing conventions

### Naming

```python
def test_<unit>_<scenario>_<expected_outcome>():
    ...

# Good
def test_ttl_gate_drops_lower_priority_when_higher_armed(): ...
def test_session_start_returns_409_on_partial_unique_conflict(): ...

# Bad
def test_ttl_gate_works(): ...      # vague
def test_session_creation(): ...     # which scenario?
```

### File layout

Roughly mirror the source file. `services/hermes/detection/ttl_gate.py`
→ `tests/unit/test_ttl_gate.py`.

### Helpers

Per-file `_seed_X()` helpers are encouraged. Don't create a giant
`tests/helpers.py` — keep helpers next to the tests that use them.
Example pattern:

```python
async def _seed_recording_global_session() -> uuid.UUID:
    """Create a default package + GLOBAL session with recording on."""
    async with async_session() as s:
        ...
```

### Async tests

```python
@pytest.mark.db
@pytest.mark.asyncio
async def test_x(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/something")
    assert resp.status_code == 200
```

`pytest-asyncio` is on auto mode (`asyncio_mode = auto` in
`pyproject.toml`), so `@pytest.mark.asyncio` is technically optional
but explicit-is-better.

### Fixtures

Top-level `tests/conftest.py` provides `api_client` and the schema-
reset fixture. Keep new fixtures in the file that uses them unless
they're shared across tiers.

---

## 7. Mocking philosophy

> **Tests that mock the database, MQTT broker, or filesystem will be
> rejected unless there is a concrete reason.**

Excerpt from `CONTRIBUTING.md` §5 — and we mean it. The legacy system
was full of `mock.patch("psycopg2.connect")` style tests, and they
caught zero of the real bugs (which were always cross-component
integration issues). The rewrite's stance:

- **Pure functions** (parsers, formatters, single detectors) → unit
  test with no mocking.
- **DB-touching code** → integration test against a real Postgres.
- **MQTT publishing** → integration test against a real Mosquitto
  (when needed; today most MQTT tests use a recording sink in unit).
- **Modbus polling** → in-process pymodbus async server (real TCP,
  real Modbus protocol, no mocks).
- **External services** (SMTP, future Redis) → tested through their
  side effects (an OTP gets logged with the body shape we expect).

When you do need a mock — e.g. for a side-effect-free I/O check —
prefer a test double that implements the protocol over `mock.patch`:

```python
class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[DetectedEvent] = []

    def publish(self, event: DetectedEvent) -> None:
        self.events.append(event)
```

This survives type-checker scrutiny and the tested code thinks it's
a real `EventSink`.

---

## 8. CI matrix

`.github/workflows/ci.yml`. Three parallel jobs:

```
┌────────────────────────────────────────────────────────────┐
│ python                                                     │
│   ubuntu-24.04                                             │
│   service: timescale/timescaledb:2.17.2-pg16                │
│   ─                                                        │
│   • uv sync --extra dev                                    │
│   • ruff check services tests                              │
│   • ruff format --check services tests                     │
│   • mypy services/hermes                                   │
│   • pytest -m 'not db and not mqtt and not bench' (covers  │
│       unit + golden) with --cov                            │
│   • pytest -m db                                           │
│   • pytest -m bench -s                                     │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ ui                                                         │
│   ubuntu-24.04                                             │
│   • pnpm install --frozen-lockfile                         │
│   • svelte-check                                           │
│   • tsc --noEmit                                           │
│   • prettier / eslint                                      │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│ build                                                      │
│   • uv build (Python wheel)                                │
│   • pnpm build (UI bundle)                                 │
└────────────────────────────────────────────────────────────┘
```

Failing any one step fails the whole run. Cancellation on superseding
runs (`concurrency.cancel-in-progress: true`) so superseded PRs don't
burn CI time.

---

## 9. Coverage expectations

`pyproject.toml` configures `--cov=services/hermes --cov-report=xml`
when running the unit + golden marker subset. The expected coverage
threshold for changed files in a PR is **≥ 90%** (per
`CONTRIBUTING.md` §3 PR checklist).

Files with intentionally low coverage (e.g. `services/hermes/api/__main__.py`,
which is just a uvicorn launcher): mark with `# pragma: no cover` on
the unreachable lines so the threshold reflects what's actually
testable.

---

## 10. How to run a single test

By name:

```bash
uv run pytest tests/unit/test_ttl_gate.py::test_lower_priority_blocked_when_higher_armed -q
```

By keyword:

```bash
uv run pytest -k "ttl_gate and lower_priority" -q
```

By marker (and exclude others):

```bash
uv run pytest -m 'db and not slow' -q
```

By directory:

```bash
uv run pytest tests/golden -q
```

With verbose output (full assertion diffs):

```bash
uv run pytest tests/unit/test_session_samples_writer.py -v
```

With pdb on failure:

```bash
uv run pytest tests/unit/test_x.py --pdb
```

With print output (don't capture stdout):

```bash
uv run pytest tests/bench -s
```
