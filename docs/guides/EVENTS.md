# EVENTS.md — detector mechanics

> **Audience:** anyone tuning thresholds, reading the legacy contract,
> or debugging "why didn't this fire / why did this fire". Walks the
> four detector types, the BREAK / mode-switching state machine, the
> TTL gate that gates them, and the storage shape every fired event
> takes.
>
> **Companion docs:**
> - [`WORKFLOW.md`](./WORKFLOW.md) — where detection sits in the pipeline
> - [`../contracts/EVENT_DETECTION_CONTRACT.md`](../contracts/EVENT_DETECTION_CONTRACT.md) — frozen legacy spec we match
> - [`CONFIGURATION.md`](./CONFIGURATION.md) — every threshold by name + scope
> - [`../design/DATABASE_SCHEMA.md`](../design/DATABASE_SCHEMA.md) — `events` + `event_windows` columns

---

## Table of contents

1. [Five event types in one table](#1-five-event-types-in-one-table)
2. [Common machinery — sliding window, debounce, gap reset](#2-common-machinery)
3. [Type A — variance / coefficient of variation (CV%)](#3-type-a--cv)
4. [Type B — tolerance band around the rolling mean](#4-type-b--tolerance-band)
5. [Type C — absolute bound on the rolling mean](#5-type-c--absolute-bound)
6. [Type D — band around two-stage averaging](#6-type-d--two-stage)
7. [BREAK + mode switching (POWER_ON / STARTUP / BREAK)](#7-break--modes)
8. [Priority and the TTL gate](#8-priority--ttl-gate)
9. [Storage shape — what an event row looks like](#9-storage-shape)
10. [Tuning recipes](#10-tuning-recipes)
11. [Debugging "why didn't it fire?"](#11-debugging-why-didnt-it-fire)

---

## 1. Five event types in one table

| Type | What it watches | Fires when | Use case |
|------|-----------------|------------|----------|
| **A** | Coefficient of variation over `T1` | `CV%(t) > threshold_cv` | "Signal is suddenly noisy / unstable" |
| **B** | Last sample vs. `T2`-second average | `value < avg − lower_pct%` OR `value > avg + upper_pct%` | "Outlier point against recent baseline" |
| **C** | Mean of `T3`-second window | `avg_T3 < threshold_lower` OR `avg_T3 > threshold_upper` | "Sustained absolute out-of-range" |
| **D** | `avg_T3` vs. band around two-stage `avg_T5` | `avg_T3` outside `avg_T5 ± tolerance_pct%` | "Long-term drift from baseline trend" |
| **BREAK** | Sensor mode transitions | STARTUP → BREAK on sustained drop below `break_threshold` | "Wire-break / sensor disconnect" |

Priority order (only A/B/C/D — BREAK is outside the scale):

```
LOW                                          HIGH
┌──────┐   ┌──────┐   ┌──────┐   ┌──────┐
│  A   │ < │  B   │ < │  C   │ < │  D   │
└──────┘   └──────┘   └──────┘   └──────┘
```

The TTL gate uses this to suppress lower-priority events while a
higher-priority event is armed (§8). BREAK bypasses the gate entirely.

---

## 2. Common machinery

All four detectors share three building blocks:

### 2.1 `IncrementalSlidingWindow`

[`services/hermes/detection/sliding.py`](../../services/hermes/detection/sliding.py).

Maintains:
- `_running_sum` ── Σ x over the active window
- `_running_sum_sq` ── Σ x² over the active window
- `_window_count` ── N (samples currently inside)
- `_window_deque` ── `(ts, value)` pairs for eviction
- `_initialized` ── flips True once `_window_count ≥ init_threshold`

```
push(ts, value):
    # Evict samples that fell off the back end of the window
    while window_deque and window_deque[0].ts < ts - T:
        old_ts, old_val = window_deque.popleft()
        if initialized:
            running_sum    -= old_val
            running_sum_sq -= old_val²
            window_count   -= 1
        # else: warmup mode, see §2.4

    window_deque.append((ts, value))
    running_sum    += value
    running_sum_sq += value²
    window_count   += 1

    if not initialized and window_count >= init_threshold:
        initialized = True
```

Mean = `running_sum / window_count`. Variance (population) =
`max(0, (running_sum_sq − running_sum² / N) / N)` — the `max(0, ...)`
clamps tiny negatives from float drift on near-constant signals.

Per-sample cost: O(1) regardless of window length. This is why a
30-second window at 100 Hz (3000 samples) is just as cheap as a
1-second window.

### 2.2 Init-fill ratio (warmup)

`init_fill_ratio` (default 0.9) × `T × expected_sample_rate_hz` =
`init_threshold`. The detector does not fire until the window is at
least this fraction full.

Why: a half-warmed window has a misleading mean / variance — the first
500 ms of a 1 s window is dominated by whatever value happened to
arrive first. Suppressing fires until 90% full prevents false-positives
on cold start.

**Warmup quirk preserved from legacy** (Type A only): while
`initialized == False`, evictions DO NOT subtract from the running
sums. Once flipped, normal slide semantics. This is deliberate — the
golden-traffic harness asserts byte-equality on this behaviour, and
"fixing" it would diverge from the legacy.

### 2.3 Debounce

```
sample arrives, condition true
  │
  ├── _debounce_start is None → set it to ts; don't fire yet
  │
  └── ts − _debounce_start ≥ debounce_seconds → FIRE
         triggered_at = _debounce_start  (the FIRST crossing, not now!)
         _debounce_start = None  (so the next sustained violation re-arms)

sample arrives, condition false
  │
  └── _debounce_start = None  (silently reset)
```

`triggered_at` being the original crossing time is critical for
operator alarms: a 5-second debounce with a fire at `t=10s` records
the event as triggered at `t=10s − 5s = 5s` so the ±9 s window centres
on the actual disturbance, not the moment we became sure.

Default `debounce_seconds = 0.0` means fire on the first crossing.
Operators tune up to suppress flicker on noisy signals.

### 2.4 Data-gap reset

Hardcoded threshold: 2.0 seconds inter-sample interval. If the gap
between consecutive samples exceeds this, the detector clears its
window state — the old stats are no longer representative because
the device probably rebooted or the broker disconnected.

```
on push(ts, value):
    if last_ts is not None and ts - last_ts > 2.0:
        # Gap reset
        window_deque.clear()
        running_sum = 0
        running_sum_sq = 0
        window_count = 0
        initialized = False
        # Debounce timer also clears
        _debounce_start = None
    last_ts = ts
    # ... continue with normal push logic
```

This applies to all four detectors and the mode state machine.
`session_samples` archive does NOT clear on a gap — a gap in raw
samples is itself useful diagnostic information.

---

## 3. Type A — CV%

[`services/hermes/detection/type_a.py`](../../services/hermes/detection/type_a.py).

```
                    ┌───────────────────────┐
sample(ts, x) ─────►│  push(ts, x)          │
                    │  → window updates     │
                    │  → mean = Σx / N      │
                    │  → var  = E[X²] - mean²│
                    │  → cv%  = √var / mean │
                    └─────────┬─────────────┘
                              │
                              ▼
                    ┌───────────────────────┐
                    │ cv% > threshold_cv ?  │ no ──► return None
                    └─────────┬─────────────┘
                              │ yes
                              ▼
                    ┌───────────────────────┐
                    │ debounce              │ pending ──► return None
                    └─────────┬─────────────┘
                              │ elapsed
                              ▼
                    fire DetectedEvent(
                        event_type=A,
                        triggered_at=_debounce_start,  ← NOT ts
                        metadata={
                            "cv_percent": cv,
                            "average":   mean,
                            "std":       √var,
                            "window_seconds": T1,
                            "n_samples": N,
                        }
                    )
```

### Config (TypeAConfig)

| Field | Default | Meaning |
|-------|---------|---------|
| `enabled` | `False` | Master switch |
| `T1` | `1.0` | Window length in seconds |
| `threshold_cv` | `5.0` | Fire when CV% exceeds this |
| `debounce_seconds` | `0.0` | Sustained-violation hold |
| `init_fill_ratio` | `0.9` | Window must be ≥90% full to fire |
| `expected_sample_rate_hz` | `100.0` | Used to size deque maxlen |

### Reading the metadata

`cv_percent` is the value at the moment of the trigger. `average` is
the window mean — useful for reporting "value spiked vs. baseline
of X". `std` is the population standard deviation. `n_samples` is N.
`window_seconds` is `T1` — handy when comparing events across
re-tuned configs.

### Numerical floor

`|mean|` is clamped to `1e-9` before the CV division so a zero-mean
window doesn't ZeroDivisionError. This means CV% on a centred-on-zero
signal can return absurdly large values; in practice operators don't
configure Type A on signals that cross zero.

---

## 4. Type B — tolerance band

[`services/hermes/detection/type_b.py`](../../services/hermes/detection/type_b.py).

```
                    ┌───────────────────────┐
sample(ts, x) ─────►│  push(ts, x)          │
                    │  → avg_T2 = Σx / N    │
                    └─────────┬─────────────┘
                              │
                              ▼
       lower_bound = avg_T2 - REF_VALUE × lower_threshold_pct / 100
       upper_bound = avg_T2 + REF_VALUE × upper_threshold_pct / 100
                              │
                              ▼
                    ┌───────────────────────┐
                    │ x < lower_bound       │ no  ──► return None
                    │  OR x > upper_bound ? │
                    └─────────┬─────────────┘
                              │ yes
                              ▼
                       (debounce as in §2.3)
                              │
                              ▼
                    fire DetectedEvent(
                        event_type=B,
                        metadata={
                            "trigger_value": x,
                            "average":  avg_T2,
                            "lower_bound": lower_bound,
                            "upper_bound": upper_bound,
                            "tolerance_pct_lower": lower_threshold_pct,
                            "tolerance_pct_upper": upper_threshold_pct,
                        }
                    )
```

The `REF_VALUE = 100.0` constant comes from the legacy contract — at
that constant, `pct` simplifies to "absolute deviation in sensor units
when avg is 100", but the formula carries the constant explicitly so
a future change to `REF_VALUE` doesn't silently shift bounds.

### Asymmetry

`lower_threshold_pct` and `upper_threshold_pct` are independent. A
signal that's fine when noisy upward but worrying when noisy downward
(e.g. a temperature measuring degradation) can use `lower_pct=2.0`,
`upper_pct=10.0`.

### Config (TypeBConfig)

| Field | Default | Meaning |
|-------|---------|---------|
| `enabled` | `False` | |
| `T2` | `5.0` | Rolling-mean window in seconds |
| `lower_threshold_pct` | `5.0` | Fire when `value < avg − this%` |
| `upper_threshold_pct` | `5.0` | Fire when `value > avg + this%` |
| `debounce_seconds` | `0.0` | |
| `init_fill_ratio` | `0.9` | |
| `expected_sample_rate_hz` | `100.0` | |

---

## 5. Type C — absolute bound

[`services/hermes/detection/type_c.py`](../../services/hermes/detection/type_c.py).

The simplest detector. Fires when the rolling mean over `T3` leaves an
absolute range:

```
sample(ts, x) ──► push ──► avg_T3 = Σx / N
                              │
                              ▼
        avg_T3 < threshold_lower OR avg_T3 > threshold_upper ?
                              │ yes
                              ▼
                       (debounce)
                              │
                              ▼
                    DetectedEvent(
                        event_type=C,
                        metadata={
                            "current_avg": avg_T3,
                            "threshold_lower": threshold_lower,
                            "threshold_upper": threshold_upper,
                            "window_seconds": T3,
                            "n_samples": N,
                        }
                    )
```

`current_avg` is exposed publicly because Type D reads it on the same
sample tick (§6).

### Config (TypeCConfig)

| Field | Default | Meaning |
|-------|---------|---------|
| `enabled` | `False` | |
| `T3` | `10.0` | Rolling-mean window in seconds |
| `threshold_lower` | `0.0` | Absolute floor (raw sensor units) |
| `threshold_upper` | `100.0` | Absolute ceiling |
| `debounce_seconds` | `0.0` | |
| `init_fill_ratio` | `0.9` | |
| `expected_sample_rate_hz` | `100.0` | |

Thresholds are in **raw sensor units**, not percentages — Type C is
the "this temperature must stay between 20 °C and 80 °C" detector.

---

## 6. Type D — two-stage

[`services/hermes/detection/type_d.py`](../../services/hermes/detection/type_d.py).

The most complex. Detects long-term drift by comparing the *short-term
mean* (Type C's `avg_T3`) against a *trend-of-the-trend* baseline:

```
   raw samples  (~100 Hz)
        │
        │  push(ts, x)
        ▼
┌────────────────────────────┐
│ Stage 1: avg_T4            │   rolling mean of raw samples
│   = mean over last T4 s    │   over T4 (default 10 s)
└──────────┬─────────────────┘
           │  one value per sample
           ▼
┌────────────────────────────┐
│ Stage 2: per-second bucket │   for each elapsed wall-clock second,
│   one_sec_averages.append( │   average all the avg_T4 values that
│     mean of avg_T4 values  │   landed in that second
│     in that second)        │
└──────────┬─────────────────┘
           │  one value per second
           ▼
┌────────────────────────────┐
│ Stage 3: avg_T5            │   mean of last T5 entries of
│   = mean of last T5 entries│   one_sec_averages (default 30)
│     of one_sec_averages    │
└──────────┬─────────────────┘
           │
           ▼
   lower = avg_T5 − REF_VALUE × tolerance_pct / 100
   upper = avg_T5 + REF_VALUE × tolerance_pct / 100
           │
           ▼
   Fire when avg_T3 < lower OR avg_T3 > upper
   (avg_T3 is fetched from the paired Type C detector for this sensor)
```

```
                   ┌─ avg_T5 + tol_pct% ── upper ─────────────────
                   │
   avg_T3   ──────►│                                              ▲
                   │                                              │
   ──────── avg_T5 ──── (slow trend, updated every 1 s)            │
                   │                                              │
                   │                                              ▼
                   └─ avg_T5 − tol_pct% ── lower ─────────────────

   avg_T3 leaves the band → fire Type D
```

### Symmetric tolerance

The legacy schema has separate upper/lower fields but the live
detector only uses one — the rewrite preserves the legacy quirk via a
single `tolerance_pct` field in `TypeDConfig`.

### Config (TypeDConfig)

| Field | Default | Meaning |
|-------|---------|---------|
| `enabled` | `False` | |
| `T4` | `10.0` | Stage-1 raw-mean window (seconds) |
| `T5` | `30.0` | Stage-3 average-of-averages window (entries; one entry per second) |
| `tolerance_pct` | `5.0` | Symmetric band width |
| `debounce_seconds` | `0.0` | |
| `init_fill_ratio` | `0.9` | |
| `expected_sample_rate_hz` | `100.0` | |

### Why Type D depends on Type C

Within `DetectionEngine.feed_snapshot`, the per-sensor detector
iteration order is `(A, B, C, D)`. C must run BEFORE D for the same
sample tick — the engine asserts this by always running C first and
passing C's `current_avg` to D's constructor:

```python
c_detector = self._detector_for(device_id, sensor_id, EventType.C)
d_detector = TypeDDetector(self._config_provider.type_d_for(...),
                           c_detector)
```

If you ever add a new detector that depends on another, do it the same
way — make the dependency explicit in construction, don't try to
re-fetch by sensor_id at feed time.

---

## 7. BREAK + modes

[`services/hermes/detection/mode_switching.py`](../../services/hermes/detection/mode_switching.py)
(gap 3, alpha.17).

Three modes per sensor; integer codes match the legacy contract:

| Code | Mode | Detection |
|------|------|-----------|
| 0 | `POWER_ON` | suppressed |
| 1 | `STARTUP` | active — Type A/B/C/D run normally |
| 2 | `BREAK` | suppressed; previous BREAK already fired |

### State machine

```
                     POWER_ON
                        │
                        │  value > startup_threshold
                        │  sustained for startup_duration_seconds
                        │  (with grace window for transient dips)
                        ▼
                     STARTUP                 ◄─────┐
                        │                          │
                        │                          │ value > startup_threshold
                        │  value < break_threshold │ sustained for
                        │  sustained for           │ startup_duration_seconds
                        │  break_duration_seconds  │ (NO grace window
                        │                          │  on this side)
                        │  emits BREAK event       │
                        ▼                          │
                       BREAK ─────────────────────┘
                                                   no new BREAK event
                                                   on this transition
```

### When BREAK fires

Three properties of the emitted BREAK event:

1. `event_type = EventType.BREAK`.
2. `triggered_at = the FIRST below-threshold sample's wall time`,
   NOT the moment the duration boundary elapsed. This is a hard
   contract invariant — operator alarms wired to that earlier
   timestamp.
3. `metadata = {"trigger_value": x, "break_threshold": ...,
   "break_duration_seconds": ...}` where `trigger_value` is the
   sample that COMPLETED the duration window (informational, not the
   crossing).

### Asymmetric grace windows

POWER_ON → STARTUP entry: a transient dip during the wait is
**forgiven** if it lasts less than `startup_reset_grace_s` (default
1.0 s). Held above-threshold time accumulates across the dip.

STARTUP → BREAK entry: **no grace window**. Any single sample at or
above `break_threshold` resets the below-threshold timer immediately.

Why asymmetric: false-positive BREAK is more disruptive than false-
positive STARTUP-entry. We err on the side of staying in STARTUP.

### Disabled (default)

`mode_switching.config.enabled = false` is the default. The state
machine returns `active=True` for every sample, never transitions,
never emits BREAK. Detection runs unconditionally. Existing deployments
that haven't turned this on see zero behaviour change.

### Detection gating

When mode switching is enabled and the sensor is NOT active:

| Detector | Behaviour |
|----------|-----------|
| Type A | Window keeps filling (so it's primed when STARTUP begins), but events are SUPPRESSED |
| Type B | Skipped entirely (window does not fill) |
| Type C | Skipped entirely |
| Type D | Skipped entirely |

Type A keeps its window primed because variance windows take seconds
to build; suppressing the read but not the write means the moment
STARTUP begins, the detector can already fire on the latest CV%.
B/C/D have shorter dependencies and rebuild fast enough that we don't
bother priming them.

### Config (ModeSwitchingConfig)

| Field | Default | Meaning |
|-------|---------|---------|
| `enabled` | `False` | Disabled by default; state machine is no-op |
| `startup_threshold` | `100.0` | Above this in raw units → counts toward STARTUP |
| `break_threshold` | `50.0` | Below this → counts toward BREAK |
| `startup_duration_seconds` | `0.1` | Sustain-time for STARTUP entry |
| `break_duration_seconds` | `2.0` | Sustain-time for BREAK entry |
| `startup_reset_grace_s` | `1.0` | Forgiveness window for transient dips |

---

## 8. Priority and the TTL gate

`TtlGateSink` (gap 2, alpha.13) sits between the detection engine and
the durable sinks. Without it, a sustained out-of-band signal would
trigger an event on every sample and the operator UI would flood.

### The four rules

```
event arrives at TtlGateSink.publish(event)
                        │
                        ├─ event.event_type is BREAK ────► forward verbatim
                        │                                  (BREAK never gated)
                        │
                        ▼
        ┌───────────────────────────────────┐
        │ Rule 1 — Block lower priority      │
        │ Is a HIGHER-priority type already  │── yes ──► drop, return
        │ armed for this (device, sensor)?   │
        └───────────────┬────────────────────┘
                        │ no
                        ▼
        ┌───────────────────────────────────┐
        │ Rule 3 — Merge same type           │
        │ Is the SAME type already armed?    │── yes ──► drop, return
        └───────────────┬────────────────────┘
                        │ no
                        ▼
        ┌───────────────────────────────────┐
        │ Rule 2 — Preempt lower             │
        │ Are LOWER-priority types armed?    │── yes ──► clear them
        └───────────────┬────────────────────┘
                        │
                        ▼
        Rule 4 — Arm timer
        timers[(dev, sid, type)] = (triggered_at, ttl_seconds, event)
```

### Forwarding

Each call to `publish()` first runs `_expire_due(ts)` which forwards
any timer whose `ts >= armed_at + ttl_seconds`. So the gate's "clock"
is driven by the incoming event timestamps — there's no background
timer. Within a busy second, a stream of events keeps the clock
fresh; on a quiet sensor, an armed timer holds until the next event.

### Shutdown

`flush()` is called on graceful pipeline shutdown. It forwards every
armed timer regardless of elapsed time so a stop doesn't lose held
events.

### Settings.event_ttl_seconds

Default 5.0 s. Tuning recipe:

| Goal | Setting |
|------|---------|
| "Capture every individual fire" | 0.0 (disables dedup) |
| "Default — collapse bursts" | 5.0 |
| "Quiet sensors, only one event per minute" | 60.0 |

Per-sensor TTL is NOT supported (legacy didn't have it; rewrite
preserves the simpler global setting).

---

## 9. Storage shape

Two tables, one transaction per event (atomic):

### `events`

```
event_id          BIGSERIAL  PK
session_id        UUID       FK sessions(session_id)
triggered_at      TIMESTAMPTZ NOT NULL  ← FROM the detector
fired_at          TIMESTAMPTZ NOT NULL  ← when the writer COMMIT'd
event_type        event_type ENUM       (A/B/C/D/BREAK)
device_id         INTEGER    NOT NULL
sensor_id         SMALLINT   NOT NULL
triggered_value   REAL                  ← from metadata if present
metadata          JSONB      NOT NULL   ← detector-specific dict
window_id         BIGINT     FK event_windows(window_id)
```

`metadata` shape varies by `event_type`:

| Type | metadata keys |
|------|---------------|
| A | `cv_percent`, `average`, `std`, `window_seconds`, `n_samples` |
| B | `trigger_value`, `average`, `lower_bound`, `upper_bound`, `tolerance_pct_lower`, `tolerance_pct_upper` |
| C | `current_avg`, `threshold_lower`, `threshold_upper`, `window_seconds`, `n_samples` |
| D | `current_avg`, `avg_T5`, `lower_bound`, `upper_bound`, `tolerance_pct`, `T4`, `T5` |
| BREAK | `trigger_value`, `break_threshold`, `break_duration_seconds` |

Why `triggered_value` is a separate column even though it's also in
metadata: the API list view (`GET /api/events`) projects it directly
without parsing JSONB, which is much faster than `metadata->>'trigger_value'`.

### `event_windows`

```
window_id      BIGSERIAL  PK
event_id       BIGINT     FK events(event_id)  UNIQUE
window_start   TIMESTAMPTZ NOT NULL  ← triggered_at − 9 s
window_end     TIMESTAMPTZ NOT NULL  ← triggered_at + 9 s
sample_rate_hz REAL                  ← informational; e.g. 100.0
sample_count   INTEGER    NOT NULL
encoding       TEXT       NOT NULL   ← "json-utf8" today
data           BYTEA      NOT NULL   ← encoded sample list
```

`encoding` lets the BLOB shape evolve without breaking old rows.
Today's writer uses `json-utf8` (a list of `{"ts": float, "v": float}`
objects); migration to `zstd+delta-f32` is planned (~100× smaller).
The decoder picks the right path by reading the column.

### The 9-second post-window fence

`DbEventSink._writer_loop` waits until `triggered_at + 9 s` before
slicing the window and writing. Why:

- The ±9 s window must INCLUDE 9 s of post-trigger samples.
- At trigger time, those samples haven't arrived yet.
- We hold the event in memory until the wall clock reaches
  `triggered_at + 9 s`, then `EventWindowBuffer` has them all.

Operator-visible consequence: events appear in the UI 9 s after the
underlying disturbance. `events.fired_at` records the actual write
time so forensics can compare.

---

## 10. Tuning recipes

Common operator recipes. Edit via the `/config` page or
`PUT /api/config/{type}/global`.

### "I want to know about ANY signal disturbance"

- Enable Type A with `T1=1.0`, `threshold_cv=2.0`, `debounce=0.0`.
- Will fire on every transient — pair with `event_ttl_seconds=10`
  to suppress the flood.

### "Tell me when sensor 3 leaves 20–80 °C"

- Enable Type C only.
- `threshold_lower=20`, `threshold_upper=80`, `T3=2.0`, `debounce=1.0`.
- The 1 s debounce prevents single-sample noise from firing.

### "Tell me when the long-term trend drifts more than 5%"

- Enable Type C (Type D depends on it) AND Type D.
- `T3=10`, `T4=10`, `T5=30`, `tolerance_pct=5.0`.
- Will fire when the per-second mean drifts >5% from the 30-second
  baseline of per-second means.

### "Detect wire-break"

- Enable mode switching: `enabled=true`, `break_threshold=10`,
  `break_duration_seconds=2.0`.
- A clean wire reads roughly the supply voltage; a broken wire reads
  near zero. The 2-second sustain window prevents single-sample
  glitches from triggering BREAK.

### "Tune debounce to suppress flicker on a noisy signal"

- Bump `debounce_seconds` from 0 to a value larger than the noise
  period. E.g. if a signal flickers above-threshold every 200 ms,
  a `debounce_seconds=0.5` will suppress those.
- Trade-off: triggered_at still records the FIRST crossing, but the
  fire (and the ±9 s window) are delayed by that much.

---

## 11. Debugging "why didn't it fire?"

Symptom-to-cause checklist:

| Observation | Likely cause | Fix |
|-------------|--------------|-----|
| `enabled=true` but nothing fires for hours | `init_fill_ratio` too high; window never warmed | Drop to 0.5; check `n_samples` in test fires |
| Sensor in BREAK mode permanently | `mode_switching.enabled=true` and signal is below `break_threshold` | Either disable mode switching or check sensor / wiring |
| Type D never fires | Type C is disabled (D depends on C running first) | Enable Type C even with permissive thresholds |
| Events stop firing after a power cycle | Data-gap reset cleared windows; warmup re-incurred | Wait `T1+1s` (Type A) or `T2+1s` (Type B) etc. |
| Event fires but no row in DB | Writer queue stuck; check `hermes_db_writer_pending` gauge | Restart `hermes-ingest`; investigate Postgres |
| Event in DB but no MQTT publish | broker disconnect; check `hermes_mqtt_connected` | Mosquitto / network / credentials |
| Multiple events fire in 5 s window | TTL gate not catching them | Same `event_type` ↔ Rule 3 should suppress; check `event_ttl_seconds` is not 0 |
| Lower-priority event fires while higher armed | Rule 1 not catching it | Ensure events arrive at the gate in time order; check clock anchoring |
| BREAK fires repeatedly | Mode state machine not staying in BREAK | Check `break_duration_seconds` — too short can re-fire on glitches |

For deeper debugging, the harness in `tests/golden/` lets you replay
a recorded MQTT trace deterministically and inspect the captured
event stream — useful for "did this fire under config X?".
