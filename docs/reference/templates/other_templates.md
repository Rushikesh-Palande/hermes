# Template Reference — Other Templates

Covers: `dashboard.html`, `device_config.html`, `sensor_offsets.html`, `index.html`, `login.html`.

These are the "support" templates around the main device-detail view. Each page is small enough to document per-file; a cross-cutting section at the end captures shared patterns.

---

## 1. dashboard.html (839 lines)

**Purpose**
Grid of device cards with live status polling. Main entry point after login. Shows device count, individual device status (running/stopped), and provides quick access to per-device detail.

**Layout**
Fixed left sidebar (70 px) + main container with header + responsive device grid (`auto-fill: minmax(280px, 1fr)`) + footer space. Toast notification area (fixed top-right).

**UI inventory**
- `deviceCountBadge` — device count (line 630)
- `devicesGrid` — device-card container (line 642)
- Card links → `/device-config/{device_id}` (line 712)
- `btnAddDevice` — opens create-device modal (line 631, 790)
- Modal inputs: `newDeviceName` (line 656), `newDeviceTopic` optional (line 660)
- `btnCreateDevice` (line 664, 796) / `btnCancelModal` (line 663, 791)
- `toast` (line 670)
- Sidebar: Dashboard (active, 593), Device Config, Event Config, Offset Config, Logout (609–611)

**Endpoints**

| URL | Method | Payload | Response |
|-----|--------|---------|----------|
| `/api/devices` | GET | — | `{success, devices: [{device_id, device_name, topic, is_active}]}` |
| `/api/devices/{device_id}/status` | GET | — | `{running: bool}` |
| `/api/devices` | POST | `{device_name, topic: null \| string}` | `{success, error?}` |

**Polling**
`loadDevices()` every 500 ms (line 835). Each call fetches list + per-device status in parallel (line 760–767).
Load on 20 devices → 1 list request + 20 status requests every 500 ms ≈ **42 req/s sustained**.

**Authentication**
No visible `@login_required`; protection is presumed middleware. Logout via sidebar `<a href="/logout">` (line 609).

**Event listeners & quirks**
- `openModal()` — shows, autofocuses name input (779–784)
- `closeModal()` — hides modal (786–788)
- Backdrop click closes modal (792–794)
- Enter key submits create (826–831)
- `showToast()` — auto-dismiss after 3.5 s (677–683)
- Status check uses both `device.running === true` **and** `device.is_active == 1` (line 709) — redundant source-of-truth

**Function index**

| Fn | Line | Purpose |
|----|------|---------|
| `showToast(msg, isError?)` | 677 | Toast notification |
| `formatTopic(topic)` | 685 | Topic or "No Topic" placeholder |
| `renderDevices(list)` | 690 | Grid render + empty state |
| `appendAddCard(grid)` | 736 | Trailing "add" card |
| `escapeHtml(s)` | 744 | HTML entity escape |
| `loadDevices()` | 753 | Fetch devices + status |
| `openModal()` | 779 | Show create modal |
| `closeModal()` | 786 | Hide modal |
| Event listeners | 790–831 | Modal + form + Enter key |

---

## 2. device_config.html (841 lines)

**Purpose**
List / create / rename / delete MQTT devices. Alternative to the dashboard's card view, with inline CRUD actions.

**Layout**
Fixed left sidebar (70 px) + container with page header + device grid (`auto-fill: minmax(280px, 1fr)`) + three modal overlays (create, rename, delete).

**UI inventory**
- `device-grid` (581)
- Create modal: `create-name` (592), `create-error` (594)
- Header button `btnAddDevice` "＋ New Device" (578)
- Rename modal: `rename-input` (608), `rename-error` (610)
- Delete modal: `delete-modal` (619), `delete-device-name` (622)
- Sidebar: Device Config (active, 536), Event Config (543), Offset Config (550), Exit (557)

**Endpoints**

| URL | Method | Payload | Response |
|-----|--------|---------|----------|
| `/api/devices` | GET | — | `{devices: [...]}` **or** raw `[...]` (line 685 handles both shapes) |
| `/api/devices` | POST | `{device_name, topic: "stm32/adc"}` | `{success, error?}` |
| `/api/devices/{id}` | PUT | `{device_name}` | `{success, error?}` |
| `/api/devices/{id}` | DELETE | — | `200 OK` or `{error}` |

**Polling**
`loadDevices()` every 500 ms (line 838). Single fetch per tick.

**Authentication**
Logout → `handleLogout(event)` (819–826) with JS `confirm()`, then `POST /api/auth/logout`.

**Event listeners & quirks**
- `_renameId`, `_deleteId` store modal-in-flight state (632–633)
- **Hardcoded topic:** `"stm32/adc"` injected on every create (line 668)
- Escape helpers: `escHtml`, `escHtmlAttr` (807–817)
- Backdrop click closes modal (645–649)
- Enter key submits modal (829–834)
- Status badge: `is_active=1` → "Active", else "Inactive" (715–718)

**Function index**

| Fn | Line | Purpose |
|----|------|---------|
| `openModal(id)` | 636 | Show modal by id |
| `closeModal(id)` | 640 | Hide modal by id |
| `openCreateModal()` | 652 | Show create modal |
| `createDevice()` | 659 | POST create |
| `loadDevices()` | 680 | Fetch + render |
| `renderCards(devices)` | 692 | Grid render |
| `openRenameModal(id, current)` | 751 | Rename flow start |
| `submitRename()` | 759 | PUT rename |
| `openDeleteModal(id, name)` | 780 | Delete flow start |
| `submitDelete()` | 786 | DELETE device |
| `showError(el, msg)` | 802 | Modal error display |
| `escHtml(s)` | 807 | HTML escape |
| `escHtmlAttr(s)` | 815 | HTML attr quote escape |
| `handleLogout(event)` | 819 | Confirm + POST /api/auth/logout |

---

## 3. sensor_offsets.html (583 lines)

**Purpose**
Configure static per-sensor offset values per device. Formula: `adjusted = raw − offset`. Offsets applied at ingestion time (before detection).

**Layout**
Fixed left sidebar + main container with page header, device selector, bulk-apply section, 12-row offset table, empty state, alert area.

**UI inventory**
- `deviceSelect` — device dropdown (327)
- `bulkSection` — bulk-apply panel (333, hidden until device selected)
- `bulkValue` — bulk offset input (339)
- Table `<tbody>` — 12 rows, one per sensor (367)
  - `input_{1..12}` — per-sensor inputs (458)
  - `status_{1..12}` — ACTIVE / NONE badge (464)
- `saveBtn` (350), `mainAlert` (321)
- Sidebar: Dashboard (258), Event Config, System Config, Offset Config (active, 280), App Config, Exit

**Endpoints**

| URL | Method | Payload | Response |
|-----|--------|---------|----------|
| `/api/devices` | GET | — | `{devices: [{device_id, device_name}]}` |
| `/api/offsets/{deviceId}` | GET | — | `{success, offsets: {sensor_id: float}}` |
| `/api/offsets/{deviceId}` | POST | `{offsets: {1: v, 2: v, ...}}` | `{success, error?}` |
| `/api/offsets/{deviceId}/bulk` | POST | `{value: float}` | `{success, error?}` |
| `/api/offsets/{deviceId}/reset` | POST | — | `{success, error?}` |

**Polling**
No polling. Offsets load on device-selector change (415–427).

**Authentication**
`doLogout()` (573–579): POST `/api/auth/logout` → redirect `/login`.

**Event listeners & quirks**
- Inputs render to 4 decimal places (459)
- "Dirty" flag added when value changes vs original (477)
- Status: ACTIVE if offset ≠ 0.0 else NONE (479–486)
- Negative offsets allowed (319)
- Bulk apply writes same value to all 12 sensors (527–545)
- Reset requires JS `confirm()` (550)
- Info box states formula explicitly: `adjusted = raw − offset` (314)

**Function index**

| Fn | Line | Purpose |
|----|------|---------|
| `loadDevices()` | 394 | Populate device selector |
| `onDeviceChange()` | 415 | Device-select change handler |
| `loadOffsets(deviceId)` | 429 | Fetch offsets |
| `renderTable()` | 447 | 12-row render |
| `onInputChange(sid)` | 473 | Dirty + status update |
| `saveAll()` | 490 | POST all offsets |
| `applyBulk()` | 527 | POST bulk value |
| `resetAll()` | 548 | POST reset to 0.0 |
| `showAlert(msg, type)` | 564 | Alert + auto-dismiss |
| `doLogout()` | 573 | POST logout + redirect |

---

## 4. index.html (604 lines)

**Purpose**
Static marketing / landing page. No backend interaction. Shown pre-login at `/`.

**Layout**
No sidebar. Full-width: topbar (brand + Sign In) → hero → features grid → event-type grid → business-impact + user-manual (side-by-side) → footer.

**UI inventory**
- Topbar: brand logo (439), Sign In button (447)
- Hero (451–487): title, 4 tags (Real-time Streaming, Event Intelligence A–D, 12 Sensors, Operator-Ready UI), stats block
- Hardcoded stats (469–484): `12 sensors / 20 devices / 4 event engines / 18 s capture`
- Features (489–508): 4 cards (control, clarity, analytics, alerts)
- Event types (511–530): Type A variance, Type B deviation, Type C range, Type D stability
- Business impact + manual (533–597): 4 impact steps + 4 flow steps

**Endpoints**
None. Static HTML.

**Polling**
None.

**Authentication**
Unauthenticated. Sign In → `/login` (447, 462).

**Event listeners & quirks**
- Animated gradient background (25–27, 146–154)
- All stats are marketing copy, not live values
- No forms, no inputs, no JS logic

**Function index**
None — pure HTML/CSS with Font Awesome icons.

---

## 5. login.html (444 lines)

**Purpose**
Two-step OTP authentication: (1) request OTP by email, (2) verify 6-digit OTP. **No password login.**

**Layout**
Full-screen centered login container with animated background. The same form reveals the OTP input on first submission; a Resend button appears after step 1.

**UI inventory**
- `email` (316) — required, autofocus, `autocomplete="email"`
- `otp` (326) — hidden initially, `pattern="[0-9]{6}"`, `autocomplete="one-time-code"`
- `login-btn` (330–332) — "Send OTP" → "Verify OTP"
- `resend-btn` (333) — hidden initially
- `error-message` (307) — auto-dismissing alert
- `login-form` wrapper (309)

**Endpoints**

| URL | Method | Payload | Response |
|-----|--------|---------|----------|
| `/api/auth/otp/request` | POST | `{email}` | `{success, error?}` |
| `/api/auth/otp/verify` | POST | `{email, otp}` | `{success, error?}` |

**Polling**
None.

**Authentication**
This page **is** the login flow. On verify success (line 403) → redirect `/device-config` (line 406).

**Flow states**

1. **Initial** (`otpRequested=false`)
   - Email input visible, OTP hidden.
   - Button: "Send OTP".
   - Submit → POST `/api/auth/otp/request`.
   - On success → advance to state 2.

2. **OTP requested** (`otpRequested=true`)
   - OTP input visible; email retained (no focus).
   - Button: "Verify OTP"; resend button visible.
   - Submit → POST `/api/auth/otp/verify`.
   - On success → redirect `/device-config`.

3. **Error handling**
   - Errors trigger alert (392, 411, 437).
   - Alert auto-dismisses after 5 s (351–353).
   - Button re-enabled on error (390, 409, 416).

**Event listeners & quirks**
- Resend disabled for 2 s after click (439)
- Loading state: button disabled + spinner + "Working..." (369)
- OTP input: exactly 6 digits enforced client-side (326)
- On OTP-requested success, focus moves to OTP input (388)

**Function index**

| Fn | Line | Purpose |
|----|------|---------|
| `showError(message)` | 346 | Alert with 5 s auto-dismiss |
| `handleLogin(event)` | 358 | Main 2-step handler |
| Resend listener | 422 | POST OTP re-request |

---

## Cross-cutting observations

### Common chrome
- Fixed 70 px sidebar on every page except `index.html` and `login.html`.
- Sidebar nav: Dashboard / Device Config / Event Config / Offset Config / Logout.
- Active state = blue background; logout = red tint (bottom-pinned).
- Brand logo via `url_for('static', filename='embeds-squre.png')`. Subtitle = page name.
- Typography: Space Grotesk (Google Fonts) on dashboard/device-config.

### Auth guard pattern
- No template-level `@login_required` annotation — guard lives in middleware / the Flask route decorators.
- Login is **OTP-only**; no password field anywhere.
- Logout path: POST `/api/auth/logout` → navigate `/login`.

### Polling patterns

| Page | Interval | Req/tick | Notes |
|------|----------|----------|-------|
| dashboard.html | 500 ms | 1 + N (per-device status) | ~42 req/s at 20 devices |
| device_config.html | 500 ms | 1 | ~2 req/s |
| sensor_offsets.html | none | — | Loads on selector change only |
| index.html | none | — | Static |
| login.html | none | — | Event-driven |

### Deletion / merge candidates for the rewrite
1. **Merge dashboard.html ↔ device_config.html**: Same underlying list; one is read-only, one is CRUD. A single page with a mode toggle (or inline CRUD on the dashboard cards) removes duplicate fetch logic and duplicate polling.
2. **Drop 500 ms polling**: 40+ req/s for an idle dashboard is wasteful on a Pi. SSE (for status) or 2–5 s polling suffices.
3. **Drop hardcoded topic** in `device_config.html:668`: Expose as a field or derive from device type.
4. **Normalize `/api/devices` response**: The dual-shape (`{devices:[...]}` vs raw array) tolerated in `device_config.html:685` is technical debt — pick one shape.
5. **Drop `index.html`** or replace with a redirect to `/login` for authenticated workflows; marketing page has no operational role on a deployed Pi.

### Hardcoded values to extract
| Location | Value | Status |
|----------|-------|--------|
| device_config.html:668 | topic = `"stm32/adc"` | Hardcoded on create |
| sensor_offsets.html (loop) | 12 sensors baked in | Hardcoded count |
| index.html:469–484 | All stats | Marketing copy, not live |
| dashboard.html:709 | `is_active` vs `running` | Dual-source-of-truth |

### Security notes
- HTML escape helpers present in `device_config.html` and `dashboard.html`; not uniformly applied elsewhere.
- No visible CSRF tokens in any form.
- OTP input uses `inputmode="numeric"` + `pattern="[0-9]{6}"` — correct mobile UX.
- `login.html` is the only authentication surface; preserve OTP flow semantics in the rewrite.

### Behavior contracts to preserve
- **Offset formula**: `adjusted = raw − offset` (subtracts; documented in UI at line 314 of sensor_offsets.html).
- **12-sensor assumption**: All per-sensor UIs iterate 1..12; no dynamic count.
- **OTP-only auth**: no password path — rewrite must preserve.
- **Logout**: POST `/api/auth/logout` then navigate; do not use GET logout links that CSRF-bypass.

### Rewrite consolidation plan
1. **Landing** — keep `index.html` as static gateway (or drop entirely).
2. **Auth** — port `login.html` OTP flow; add session/token validation.
3. **Device list** — merge `dashboard.html` + `device_config.html`.
4. **Device detail** — existing `device_detail.html` (see its own reference doc).
5. **Offsets** — keep page, ensure selector pattern matches device list.
6. **Event config / TTL / app config** — see `config_templates.md`.