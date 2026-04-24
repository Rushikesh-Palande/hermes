# HERMES Reference Library

This directory contains per-file reference documents for the **legacy** HERMES sensor-dashboard codebase at `/home/embed/hammer/`. It complements `docs/contracts/`, which holds cross-cutting behaviour contracts.

The reference docs enable the rewrite to proceed without the old repo open: every user-visible behaviour, every endpoint, every quirk has a citation to the original file and line.

Reference documents are frozen snapshots of the legacy system (as of 2026-04-23). Do **not** update them to track the rewrite. When you need to ask "how did the old thing do X?", read these.

## How to use this library

1. **Starting a rewrite module?** Read the relevant reference doc end-to-end before designing the new module.
2. **Unsure what old behaviour to preserve?** Contracts in `docs/contracts/` define the invariants; reference docs describe the original implementation that produced them.
3. **Need to justify a divergence?** Log the decision in `docs/contracts/BUG_DECISION_LOG.md` (FIX / FIX+FLAG / PRESERVE) and cite the reference doc section in the rationale.

## Directory layout

```
reference/
├── README.md                          ← this file
├── templates/                         ← Jinja2 templates (user-facing UI)
│   ├── device_detail.md               (the main dashboard — ECharts + uPlot + SSE)
│   ├── event_config.md                (A / B / C / D / BREAK / mode-switching config)
│   ├── config_templates.md            (app_config + system_config + ttl_config)
│   └── other_templates.md             (dashboard, device_config, sensor_offsets, index, login)
├── static/
│   └── static_assets.md               (JS libs, test pages, style.css)
├── legacy/
│   └── modbus.md                      (Modbus TCP legacy subsystem)
├── tests/
│   └── tests_catalog.md               (all 64 pytest files, invariants, gaps)
├── scripts/
│   └── root_scripts.md                (46 root-level .py scripts, web_server.py index)
└── ops/
    └── ops_files.md                   (run.sh, wsgi.py, install.sh, Dockerfile, .env, docs/*.md)
```

## Document index

### Frontend — Jinja2 templates

| File | Lines | What it covers |
|------|-------|----------------|
| [`templates/device_detail.md`](templates/device_detail.md) | 580 | Main device dashboard: 12-sensor ECharts + uPlot, SSE streaming, 15 API endpoints, 100+ JS functions, zoom/pan, CSV export, live table, anchor-point logic. |
| [`templates/event_config.md`](templates/event_config.md) | 1 050 | A/B/C/D detection-config UI with per-sensor grids, 25 endpoints, 60+ JS functions, save-flow ordering rules, BREAK + mode-switching semantics. |
| [`templates/config_templates.md`](templates/config_templates.md) | 1 210 | Universal app-config editor (90+ keys, 18 categories), auto-restart scheduler, TTL panel. WHERE_USED map + redundancy/deletion candidates. |
| [`templates/other_templates.md`](templates/other_templates.md) | 340 | Dashboard list + CRUD, sensor offsets (`adjusted = raw − offset`), static landing page, OTP-only login flow. |

### Frontend — static assets

| File | Lines | What it covers |
|------|-------|----------------|
| [`static/static_assets.md`](static/static_assets.md) | 400 | `app.js` production core, `uplot_event_graph.js` (12-sensor chart with zero-order-hold resampling), `style.css` palette, Chart.js prototype, pattern-visualiser, MQTT test pages. Null-check defects called out. |

### Legacy subsystems

| File | Lines | What it covers |
|------|-------|----------------|
| [`legacy/modbus.md`](legacy/modbus.md) | 466 | `src/modbus/modbus_tcp_device.py`, slave simulator, polling loop, drift compensation, integration with `EventDetector.add_sensor_data()`. Migration verdict: PRESERVE (low priority). |

### Tests

| File | Lines | What it covers |
|------|-------|----------------|
| [`tests/tests_catalog.md`](tests/tests_catalog.md) | 548 | All 64 test files, 12 subsystems, **70+ locked invariants** with file:line citations. Gap analysis + acceptance criteria for the rewrite. |

### Scripts

| File | Lines | What it covers |
|------|-------|----------------|
| [`scripts/root_scripts.md`](scripts/root_scripts.md) | 1 075 | 46 root-level Python scripts. Full `web_server.py` (3 999-line) line-range index + 60+ `@app.route` decorators. KEEP / DROP / UNCERTAIN matrix. |

### Ops / deployment

| File | Lines | What it covers |
|------|-------|----------------|
| [`ops/ops_files.md`](ops/ops_files.md) | 1 378 | 12 ops files (run.sh, stop.sh, install.sh, wsgi.py, Dockerfile, requirements.txt, .env, emails.txt, pytest.ini, …) + every `docs/*.md`. **Flags exposed Gmail app password in `.env`.** Systemd unit template included. |

## Contracts (sibling directory)

The `docs/contracts/` directory complements these reference docs with cross-cutting behaviour contracts:

- `HARDWARE_INTERFACE.md` — STM32 MQTT payload schema, timestamp anchoring, sensor offsets.
- `INGESTION_PIPELINE.md` — end-to-end data flow from broker to DB.
- `EVENT_DETECTION_CONTRACT.md` — A/B/C/D state machines, TTL two-phase, priority, debounce, BREAK.
- `DATABASE_CONTRACT.md` — schema, migrations, 500 ms dedup, concurrency.
- `API_CONTRACT.md` — every endpoint, request/response shapes, dead endpoints, SSE.
- `CONFIG_CATALOG.md` — all ~100 `app_config` keys + env vars.
- `WORKER_PROTOCOL.md` — 6 worker threads, task-tuple formats.
- `BUG_DECISION_LOG.md` — 40 findings, FIX / FIX+FLAG / PRESERVE classification.
- `GOLDEN_TRAFFIC_PLAN.md` — capture + replay harness for byte-identical behavioural regression.

## Things to remember when reading these docs

1. **Reference docs are frozen** — they describe the old system at a point in time. The rewrite lives in `src/` at the new repo root; do not conflate.
2. **Numbers may decay** — line numbers are frozen. If the legacy repo moves, use the file paths and feature descriptions, not raw line numbers.
3. **"Production" vs "prototype"** — many static-asset files are labelled prototype/demo; only `app.js`, `uplot_event_graph.js`, and `style.css` are production-critical.
4. **Two MQTT paths** — the active ingestion is `mqtt_data_consumer()` inside `web_server.py`, NOT `src/mqtt/client.py`. This is called out repeatedly because it is a common trap.
5. **Secrets leak** — `.env` in the legacy repo contains live SMTP credentials; rotate before shipping the rewrite.
6. **Polling cadence is aggressive** — dashboard.html polls every 500 ms; for 20 devices that is ~42 req/s. The rewrite should use SSE/WebSocket or ≥2 s polling.

## When to update these docs

Never, unless you discover a misread or missing invariant in the legacy system. If you discover something: add a new section, do not rewrite history. These docs' value comes from being the authoritative record of the system we are replacing.
