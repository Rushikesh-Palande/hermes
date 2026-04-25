# UI.md — every SvelteKit page

> **Audience:** anyone working on the frontend, debugging "the UI did
> X but the API said Y", or extending the dashboard with a new page.
> Catalogs every page in `ui/src/routes/`, the shared library helpers,
> and the conventions the rewrite has settled on.
>
> **Companion docs:**
> - [`WORKFLOW.md`](./WORKFLOW.md) — what the data on screen actually represents
> - [`../design/REST_API.md`](../design/REST_API.md) — endpoints the UI calls
> - [`BACKEND.md`](./BACKEND.md) — Python counterpart

---

## Table of contents

1. [Stack and conventions](#1-stack-and-conventions)
2. [Layout file map](#2-layout-file-map)
3. [Shared library `src/lib/`](#3-shared-library-srclib)
4. [Pages](#4-pages)
   1. [`/login`](#41-login)
   2. [`/` (overview)](#42--overview)
   3. [`/devices`](#43-devices)
   4. [`/devices/[device_id]`](#44-devicesdevice_id)
   5. [`/events`](#45-events)
   6. [`/sessions`](#46-sessions)
   7. [`/sessions/[session_id]`](#47-sessionssession_id)
   8. [`/config`](#48-config)
   9. [`/mqtt-brokers`](#49-mqtt-brokers)
   10. [`/settings`](#410-settings)
5. [Live-chart deep dive](#5-live-chart-deep-dive)
6. [Auth flow + token lifecycle](#6-auth-flow--token-lifecycle)
7. [Building, dev-serving, deploying](#7-building-dev-serving-deploying)
8. [Conventions checklist for new pages](#8-conventions-checklist-for-new-pages)

---

## 1. Stack and conventions

| Layer | Choice |
|-------|--------|
| Framework | SvelteKit 5 (runes — `$state`, `$derived`, `$effect`, `$props`) |
| Bundler | Vite (default SvelteKit setup) |
| Adapter | `@sveltejs/adapter-node` (production builds an Express-compatible Node server) |
| Styling | Tailwind 3 |
| Charts | uPlot (NOT Chart.js or ECharts; legacy used both, rewrite picked uPlot for the 1 ms paint budget) |
| HTTP | `fetch` via the `$lib/api` wrapper |
| Live data | Native `EventSource` (SSE), NOT WebSockets |
| Auth state | localStorage-backed JWT, single key |
| Type system | TypeScript strict mode + `noUncheckedIndexedAccess` |
| Linting | `prettier` + `eslint` + `svelte-check` (CI runs all three) |

Conventions:

- **No `any`.** Use `unknown` and narrow.
- **Component files are kebab-case; exports are PascalCase.**
- **Class directives** (`class:foo={cond}`) avoid backslashes in
  Tailwind variant names — Svelte's parser stumbles. Use a
  `class={['fixed-classes', conditionalClasses(...)]}` array instead
  when the variant contains `dark:bg-x/40` (the slash trips the parser).
- **Reactive state**: `$state<T>(initial)` for primitives,
  `$state<T[]>([])` for arrays. NEVER reassign the array reference
  inside `$effect` — Svelte already tracks deep mutations.
- **Async boundaries**: `await` lives inside event handlers and
  `onMount`. Components don't render Promises directly; if you need
  loading state, store it in `$state<{value, loading, error}>`.
- **Error UX**: `ApiError` from `$lib/api` exposes `.status`,
  `.detail`, `.path`. Surface `.detail` to the user (it's the FastAPI
  `detail` field) — never `e.message` directly because that leaks the
  full URL.

---

## 2. Layout file map

```
ui/
├── package.json          name "hermes-ui", deps + scripts
├── pnpm-lock.yaml        frozen deps
├── svelte.config.js      adapter-node + preprocess (Tailwind)
├── tsconfig.json         strict + noUncheckedIndexedAccess
├── vite.config.ts        proxies /api to FastAPI in dev
├── tailwind.config.js
├── postcss.config.js
└── src/
    ├── app.css           Tailwind layer + a few global tweaks
    ├── app.d.ts          SvelteKit type augmentation
    ├── lib/              importable as $lib in any page
    │   ├── api.ts        HTTP client + ApiError + token helpers
    │   ├── index.ts      barrel
    │   ├── types.ts      hand-maintained mirrors of Pydantic shapes
    │   └── LiveChart.svelte  the uPlot wrapper
    └── routes/
        ├── +layout.svelte    header / nav / auth guard / sign-out
        ├── +page.svelte      / — overview / dashboard
        ├── login/+page.svelte
        ├── devices/+page.svelte
        ├── devices/[device_id]/+page.svelte
        ├── events/+page.svelte
        ├── sessions/+page.svelte
        ├── sessions/[session_id]/+page.svelte
        ├── config/+page.svelte
        ├── mqtt-brokers/+page.svelte
        └── settings/+page.svelte
```

The root layout owns the sidebar/header, auth gate, and dark-mode
class. Every page file is self-contained: imports, `<script>`,
`<svelte:head>`, body markup. We don't share modal components yet —
when a second page needs the same modal we'll extract.

---

## 3. Shared library `src/lib/`

### 3.1 `api.ts`

Single typed fetch wrapper. Every backend call goes through `apiFetch`
which:

- Prefixes `/api` so callers pass relative paths (`/api/devices`).
- Sets `Content-Type: application/json` when there's a body.
- Adds `Authorization: Bearer <jwt>` if a token is in localStorage.
- Auto-clears the token on 401 (so the next render redirects to `/login`).
- Raises `ApiError(status, detail, path)` on non-2xx with a parsed
  `detail` field. Pydantic validation errors (array shape) are joined
  with `;` so the UI can render one line.

API surface:

```typescript
export const api = {
  get:   <T>(path, signal?) => apiFetch<T>(path, signal),
  post:  <T>(path, body)    => apiFetch<T>(path, { method: 'POST',  body }),
  put:   <T>(path, body)    => apiFetch<T>(path, { method: 'PUT',   body }),
  patch: <T>(path, body)    => apiFetch<T>(path, { method: 'PATCH', body }),
  del:   <T>(path)          => apiFetch<T>(path, { method: 'DELETE' }),
};

export class ApiError extends Error { status; detail; path; }
export function getStoredToken(): string | null;
export function setStoredToken(token: string): void;
export function clearStoredToken(): void;
```

The `TOKEN_STORAGE_KEY = 'hermes.access_token'` constant is the only
spot that knows the storage shape — a future move to in-memory or
cookie storage only changes this one file.

### 3.2 `types.ts`

Hand-maintained TypeScript mirrors of the Pydantic shapes from
`services/hermes/api/routes/*.py`. Drift is caught by `pnpm check` —
if a route changes a field name, the UI consumer fails to compile.

Currently exports:

```
DeviceProtocol, DeviceOut, DeviceIn
EventType, EventOut, EventWindowOut
TypeAConfig, TypeBConfig, TypeCConfig, TypeDConfig
DetectorTypeName, SensorOverrideOut, OverridesOut
SensorOffsetOut, DeviceOffsetsOut
MqttBrokerOut, MqttBrokerIn, MqttBrokerPatch
PackageOut, PackageIn
SessionScope, SessionLogEvent, SessionOut, SessionStart, SessionStop,
SessionLogOut, CurrentSessionsOut
IngestMode, TunableEditable, TunableField, SystemStateOut, SystemTunablesOut
HealthResponse
```

A future Phase may swap this file for openapi-generated types pulled
from the live `/openapi.json` (FastAPI emits one for free). For now
hand-rolled is faster.

### 3.3 `LiveChart.svelte`

The uPlot wrapper. ~260 LOC; documented in §5. Lives under `src/lib`
because the device-detail page is the only consumer today, but
extracting it now means a second page can plug in trivially.

---

## 4. Pages

### 4.1 `/login`

**Purpose:** OTP request → OTP verify → store JWT → redirect to `next=...`.

**Components:**
- Email input → `POST /api/auth/otp/request` (silent on rate-limit / non-allowlisted email to prevent enumeration)
- 6-digit code input (auto-submit when length === 6) → `POST /api/auth/otp/verify` → store token

**Routing:** root `+layout.svelte` short-circuits the auth guard for
this path so unauthenticated users can reach it without a redirect loop.

**Where the token lands:** `setStoredToken(response.access_token)` in
`$lib/api`, which puts it in `window.localStorage`. The next page load
reads it via `getStoredToken`.

### 4.2 `/` (overview)

**Purpose:** dashboard landing. Shows device cards (count, status)
and quick links to per-device live charts.

**Composition:**

```
┌──────────────────────────────────────────────────────────┐
│  HERMES                                                  │
│  Industrial sensor monitoring                            │
├──────────────────────────────────────────────────────────┤
│  Active session: <link>      Recording: <count>          │
├──────────────────────────────────────────────────────────┤
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                   │
│  │ Device  │  │ Device  │  │ Device  │ ...               │
│  │   1     │  │   2     │  │   3     │                   │
│  │ Active  │  │ Active  │  │ Inactive│                   │
│  │ Last:   │  │ Last:   │  │ —       │                   │
│  │ 0.123s  │  │ 0.234s  │  │         │                   │
│  └─────────┘  └─────────┘  └─────────┘                   │
└──────────────────────────────────────────────────────────┘
```

Each card links to `/devices/<id>`.

### 4.3 `/devices`

**Purpose:** device CRUD. List + add form + soft-disable toggle.

**API hits:**
- `GET /api/devices` on mount
- `POST /api/devices` from the form
- `PATCH /api/devices/{id}` for the active toggle

**Limitations** (tracked follow-ups):
- Form only accepts `device_id`, `name`, `topic` — Modbus
  `protocol="modbus_tcp"` + `modbus_config` editing is not yet
  exposed. Operators can POST raw JSON via curl in the meantime, or
  set up Modbus devices ahead of time via `psql`.
- Delete is intentionally NOT exposed: legacy lesson is operators
  occasionally fat-finger a delete and lose event history. Soft-
  disable preserves the FK.

### 4.4 `/devices/[device_id]`

**Purpose:** the live chart for one device. The bread-and-butter view
operators sit on all day.

**Composition:**

```
┌──────────────────────────────────────────────────────────┐
│ Device 1 — line-A                            [Pause][Edit]│
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ▲                                                       │
│  │  ╲     ╱╲                                             │
│  │   ╲___╱  ╲___    sensor 1                             │
│  │   ─────────╲─    sensor 2                             │
│  │   ── ─ ─ ─ ╲     ...                                  │
│  │              ╲╲╲ sensor 12                            │
│  │ ──────────────────────────────►                       │
│                                  time                    │
├──────────────────────────────────────────────────────────┤
│  Sensor mode badges (planned): POWER_ON / STARTUP / BREAK │
│  Calibration offsets quick-edit (planned)                 │
└──────────────────────────────────────────────────────────┘
```

**Live data path:**

1. Page mounts → opens an `EventSource` against
   `/api/live_stream/{device_id}?interval=0.1&max_samples=500`.
2. Each SSE frame is JSON-parsed; the `samples` array is appended to
   `LiveChart`'s internal buffer.
3. uPlot redraws on the next animation frame.

**Stop / pause:** clicking pause closes the EventSource. Resume
re-opens it; the `last_ts` cursor is reset to `now()` so the chart
shows fresh data, not the gap.

**Disconnect handling:** EventSource automatically reconnects on drop
(uses the server's `retry: 3000` hint). UI shows a "reconnecting"
banner if no frames arrive for >2 s.

### 4.5 `/events`

**Purpose:** event list, filter, expand-row to inspect ±9 s window.

**Composition:**

```
┌──────────────────────────────────────────────────────────┐
│ Events                                                   │
│ Filters: [device_id▾] [sensor▾] [type▾] [from] [to]      │
│ Export: [CSV][NDJSON]                                    │
├──────────────────────────────────────────────────────────┤
│ ID  │ When        │ Type │ Dev │ Sen │ Value │ Metadata │
├─────┼─────────────┼──────┼─────┼─────┼───────┼──────────┤
│ 9876│ 14:30:01.123│  A   │  1  │  5  │ 47.83 │ cv=8.7%  │ ← click row
│       └── window 14:29:52 → 14:30:10  1820 samples       │
│           [Metadata]                                      │
│ 9875│ 14:29:55.001│  C   │  1  │  3  │ ...               │
│ ...                                                       │
├──────────────────────────────────────────────────────────┤
│ [Prev]  rows 1–100  [Next]                               │
└──────────────────────────────────────────────────────────┘
```

**API hits:**
- `GET /api/events?device_id=...&...&limit=100&offset=...` on filter change
- `GET /api/events/{id}/window` lazily on row expand (cached client-side)
- `GET /api/events/export?format=csv` redirected for the export button

**Known gap (tracked):** the row-expand shows window metadata + JSON
dump, but does NOT plot the ±9 s waveform. A "window chart" component
is the natural next addition.

### 4.6 `/sessions`

**Purpose:** session lifecycle dashboard (gap 5, alpha.19).

**Composition:**

```
┌──────────────────────────────────────────────────────────┐
│ Sessions                                                 │
│ Active                                                   │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ scope │ device │ package │ started │ duration │ Stop│ │
│ ├───────┼────────┼─────────┼─────────┼──────────┼─────┤ │
│ │global │   —    │ default │ 09:15   │ 5h 20m   │[Stop]│ │
│ │local  │   3    │ default │ 11:00   │ 3h 35m   │[Stop]│ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ Start session                                            │
│ scope ▾   package ▾   device   notes                     │
│ [Start session]                                          │
│                                                          │
│ Recent (closed)                                          │
│ ID  │ scope │ ... │ ended │ duration │ reason            │
│ ...                                                       │
└──────────────────────────────────────────────────────────┘
```

**API hits:**
- `GET /api/sessions/current` for the active panel
- `GET /api/sessions?active=false&limit=20` for recent
- `GET /api/packages` to populate the form's dropdown
- `POST /api/sessions` to start
- `POST /api/sessions/{id}/stop` to stop (prompts for an optional reason)

The form's scope picker auto-shows/hides the device-id field and
auto-pre-selects the default package.

### 4.7 `/sessions/[session_id]`

**Purpose:** per-session detail with audit log timeline.

```
┌──────────────────────────────────────────────────────────┐
│ ← all sessions                                           │
│ Session detail                                           │
│ <uuid>                                          [Stop]   │
│ scope: global · ACTIVE                                   │
├──────────────────────────────────────────────────────────┤
│ Started:    14:30 - 25 Apr                               │
│ Started by: api                                          │
│ Notes:      shift A start                                │
│ Package:    <uuid> (default)                             │
│ Record raw: yes                                          │
├──────────────────────────────────────────────────────────┤
│ Audit log (asc)                                          │
│  start  14:30  api  {scope: global, package_id: ...}    │
│  stop   18:50  api  {reason: "shift change"}             │
└──────────────────────────────────────────────────────────┘
```

Each log row shows event-type-coloured chips. Stop button is hidden
when `ended_at != null`.

### 4.8 `/config`

**Purpose:** detector threshold editing — Type A/B/C/D + mode switching.

**Composition:**

```
Tabs: [Type A] [Type B] [Type C] [Type D] [Mode switching]
                                                            
Each tab:                                                   
  ┌──────────────────────────────────────────────────────┐
  │ Global config                                        │
  │   enabled [✓]                                        │
  │   T1 [1.0]                                           │
  │   threshold_cv [5.0]                                 │
  │   debounce_seconds [0.0]                             │
  │   ...                                                │
  │   [Save]                                              │
  └──────────────────────────────────────────────────────┘
                                                          
  ┌──────────────────────────────────────────────────────┐
  │ Per-device overrides                                 │
  │   device 1: ... [Edit] [Clear]                       │
  │   device 3: ... [Edit] [Clear]                       │
  │   [+ add override]                                   │
  └──────────────────────────────────────────────────────┘
```

**API hits:**
- `GET /api/config/type_a` (etc.) on tab activation
- `PUT /api/config/type_a` on save
- `GET /api/config/type_a/overrides` for per-device/per-sensor list
- `PUT /api/config/type_a/devices/{id}` for device-scope override
- `PUT /api/config/type_a/devices/{id}/sensors/{sid}` for sensor-scope
- `DELETE` variants to clear overrides

Editing a global value triggers `_commit_and_reload` server-side which
emits `pg_notify('hermes_config_changed', package_id)` so multi-shard
ingest processes pick up the change within their LISTEN cycle.

### 4.9 `/mqtt-brokers`

**Purpose:** broker registry (gap 4, alpha.18).

**Composition:**

```
┌──────────────────────────────────────────────────────────┐
│ MQTT brokers                                             │
│ ⚠ Heads up: flipping the active broker doesn't reconnect │
│   a running hermes-ingest. Restart the service after.    │
├──────────────────────────────────────────────────────────┤
│ Add broker                                               │
│  host [...] port [1883] username [...] password [...]    │
│  ☐ Use TLS    ☐ Set as active                            │
│  [Create broker]                                         │
├──────────────────────────────────────────────────────────┤
│ host       │ port │ auth      │ TLS │ status   │ Actions │
│ broker.eu  │ 1883 │ iot/●●●●●│ no  │ active   │ Stop... │
│ broker.us  │ 1883 │ (anon)    │ no  │ inactive │ Activ...│
└──────────────────────────────────────────────────────────┘
```

**Per-row actions:** Activate / Deactivate, Set password (inline editor), Clear password, Delete.

**Password handling** (mirrors the API contract):
- Password input on the form posts the plaintext.
- Server encrypts via Fernet, stores in `password_enc`.
- The list response carries `has_password: bool`, never the actual value.
- Inline editor on Set Password sends `{"password": "<new>"}`.
- Clear Password sends `{"password": ""}` to drop the stored value.

### 4.10 `/settings`

**Purpose:** read-only system dashboard (gap 8, alpha.22).

**Composition:**

```
┌──────────────────────────────────────────────────────────┐
│ Settings                                       [Refresh] │
├──────────────────────────────────────────────────────────┤
│ System state                                             │
│ Version: 0.1.0a25     Ingest mode: all                   │
│ Shard:   0 of 1       Log format: json                   │
│ Dev mode: off                                            │
│ Active GLOBAL session: 8a3f...                           │
│ Active LOCAL sessions: 0                                 │
│ Recording: 0          (chip "archive on" if >0)          │
│ MQTT devices active: 5                                   │
│ Modbus devices active: 0                                 │
├──────────────────────────────────────────────────────────┤
│ Editable from other pages                                │
│ → Detection thresholds (/config — live edits)            │
│ → MQTT brokers       (/mqtt-brokers — restart needed)   │
│ → Sessions           (/sessions)                         │
│ → Devices            (/devices — incl. modbus_config)    │
├──────────────────────────────────────────────────────────┤
│ Boot-time tunables                                       │
│ Key                       │ Value │ Editability │ Hint   │
│ event_ttl_seconds         │ 5.0   │ restart     │ env... │
│ live_buffer_max_samples   │ 2000  │ restart     │ env... │
│ mqtt_drift_threshold_s    │ 5.0   │ restart     │ env... │
│ ...                                                       │
└──────────────────────────────────────────────────────────┘
```

Read-only by design. Restart-required tunables show the env-var name
+ systemd command in the "Hint" column so an operator can copy-paste.

---

## 5. Live-chart deep dive

`ui/src/lib/LiveChart.svelte`. The chart that handles 12 series ×
thousands of samples at 100 Hz on a Pi-class browser.

### Buffer shape

```typescript
let timestamps: number[];      // index 0 of uPlot data array
let perSensor: number[][];     // indices 1..12 (sensor 1 → perSensor[0])
```

Why parallel arrays not array-of-objects: uPlot expects parallel
arrays for its `data` prop, and it's also more cache-friendly (12 ×
N samples rather than N × 12).

### Append vs. replace

```typescript
function ingest(snapshot: { ts: number; values: Record<string, number> }) {
    timestamps.push(snapshot.ts);
    for (let sid = 1; sid <= 12; sid++) {
        const v = snapshot.values[String(sid)];
        perSensor[sid - 1].push(v ?? NaN);   // NaN = uPlot draws gap
    }
    // Trim to MAX_BUFFER (default 5000 samples ≈ 50 s at 100 Hz)
    if (timestamps.length > MAX_BUFFER) {
        timestamps.shift();
        perSensor.forEach(arr => arr.shift());
    }
    chart?.setData([timestamps, ...perSensor]);
}
```

uPlot's `setData` is the only paint trigger. We don't redraw on every
sample; the SSE feed batches up to 500 samples per frame (~50 ms at
the default 100 ms tick), and `setData` paints once per frame.

### Series styling

12 colours from a Tailwind-aligned palette; survives both light and
dark mode. Stepped line option for sensors that update slowly.

### NaN handling

If a snapshot is missing a sensor (rare — happens when a sensor is
disabled mid-stream), we push NaN. uPlot draws a gap. This matches
the legacy behaviour and prevents the line from "snapping" to zero.

### Performance

Bench numbers from the legacy uPlot evaluation (which we ported):
- 12 series × 5 000 samples: <1 ms per `setData` paint
- Frame budget at 100 Hz tick: 10 ms — comfortable headroom

If the chart starts to feel sluggish on your hardware, the first
suspect is `MAX_BUFFER` (lower it) or the SSE `interval` (raise it).

---

## 6. Auth flow + token lifecycle

```
1. User loads any page (other than /login)
   ├── +layout.svelte mounts
   ├── checks getStoredToken() in onMount + on every route change
   └── if null → goto('/login?next=<current_path>')

2. User opens /login
   ├── enters email
   ├── POST /api/auth/otp/request     (server emails OTP if allowlisted)
   ├── enters 6-digit code
   ├── POST /api/auth/otp/verify      (server returns access_token)
   └── setStoredToken(token); goto(next ?? '/')

3. Subsequent API calls
   ├── apiFetch reads getStoredToken()
   ├── adds Authorization: Bearer header
   └── on 401, clearStoredToken() → next render redirects to /login

4. Sign out (header button)
   ├── clearStoredToken()
   ├── best-effort POST /api/auth/logout
   └── goto('/login')
```

JWT lifetime is `Settings.hermes_jwt_expiry_seconds` (default 1 h).
After expiry, the next API call returns 401 and the user is bounced
to `/login`. There's no refresh-token flow — by design, OTP-based
auth is "log in once a session, stay logged in for the shift".

---

## 7. Building, dev-serving, deploying

### Dev

```bash
cd ui
pnpm install
pnpm dev          # Vite dev server on http://localhost:5173
                  # /api/* proxied to http://localhost:8080
```

In another terminal, run `uv run hermes-api` for the FastAPI side.

### Type check

```bash
pnpm check        # svelte-kit sync && svelte-check --tsconfig ./tsconfig.json
```

CI runs this on every PR. Drift between `types.ts` and the Pydantic
shapes shows up here.

### Production build

```bash
pnpm build
```

Outputs to `.svelte-kit/output/` and `build/`. The `adapter-node`
config produces a Node-compatible Express-style server you can run
with `node build`. Production deployments serve this behind nginx.

### Static-only mode (planned)

Some deployments want a SPA bundle and pure same-origin proxy. This
needs swapping the adapter to `@sveltejs/adapter-static` plus
adjusting page-data fetches to be client-only. Not currently set up;
tracked as a follow-up.

---

## 8. Conventions checklist for new pages

When adding a new page (e.g. `/foo`):

1. `mkdir ui/src/routes/foo` and create `+page.svelte`.
2. Top of `<script lang="ts">`: import `api`, `ApiError` from
   `$lib/api`; types from `$lib/types`.
3. Define `$state<T>(initial)` for every reactive piece of state.
4. `onMount(reload)` for initial fetches.
5. Wrap mutating handlers with try/catch on `ApiError` and surface
   `e.detail`.
6. Add to the nav in `src/routes/+layout.svelte:navItems`.
7. Add the corresponding TypeScript shape to `src/lib/types.ts`
   (mirror the Pydantic model).
8. Add a row to §4 above so new devs find it.
9. Run `pnpm check` and `pnpm build` before PR.

The general rule: a new page is a thin layer that calls REST
endpoints already documented in
[`../design/REST_API.md`](../design/REST_API.md). If you find
yourself wanting to add page-specific logic to the backend, push back
— the API stays generic, the page composes.
