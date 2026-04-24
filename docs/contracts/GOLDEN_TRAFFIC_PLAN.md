# GOLDEN_TRAFFIC_PLAN.md

## Purpose

The rewrite is allowed to diverge from the legacy system only where `BUG_DECISION_LOG.md` explicitly lists a FIX or FIX+FLAG decision. Everywhere else — timestamp anchoring, event timing, sensor values, mode transitions — the new ingest + detection pipeline must produce output **byte-identical** to the old one for the same input.

"Same input" means: a recorded stream of real hardware MQTT frames, replayed deterministically against both systems, produces the same event rows, the same `data_window` contents (modulo schema changes), and the same MQTT publish sequence.

Without this harness, we have no way to prove the rewrite is safe. Integration tests check contracts; the golden traffic harness checks *behaviour*.

## 1. What to capture

### 1.1 Capture scope

For the golden trace we need at minimum:

| Dimension | Requirement |
|---|---|
| Duration | 24 hours continuous, from real STM32 hardware |
| Sensors | All 12 channels active |
| Coverage | At least one occurrence of: Type A trigger, Type B trigger, Type C trigger, Type D trigger, BREAK transition, recovery from broker disconnect, sensor gap > `data_gap_reset_s`, timestamp drift > 5 s (forced) |
| Throughput | Steady ~123 Hz, with at least one burst-and-idle cycle |

If a 24-hour capture doesn't naturally include a BREAK or a timestamp drift, **we generate synthetic injections** (a controlled power cycle of one sensor, a manual `sudo date` jump during capture) during the capture window and document them in a capture log.

### 1.2 Capture artefacts

For each capture session, produce three artefacts:

1. **`trace.ndjson`** — raw MQTT frames as they arrived at the broker, one line per frame:
   ```json
   {"recv_ts": 1712345678.901, "topic": "stm32/adc", "payload": {"device_id": 1, "ts": 1712345, "adc1": [...], "adc2": [...]}}
   ```
   Captured by a dedicated `scripts/mqtt_capture.py` subscribed in parallel to production, writing to disk with `fsync` every second. `recv_ts` is `time.time()` at the capture script's `on_message` — NOT the STM32 `ts`. This is what enables deterministic replay.

2. **`config.snapshot.json`** — full DB snapshot of every config table at capture start:
   - `app_config` (all rows)
   - `event_config_type_a`, `event_config_type_a_per_sensor`
   - `event_config_type_b`, `event_config_type_b_per_sensor`
   - `event_config_type_c`, `event_config_type_c_per_sensor`
   - `event_config_type_d`, `event_config_type_d_per_sensor`
   - `event_config_mode_switching`, `mode_switching_config_per_sensor`
   - `sensor_offsets`
   - `devices`
   - `system_config`
   - `mqtt_config` (broker settings)

3. **`observed.sqlite`** — a copy of the legacy system's SQLite DB at capture end. This contains the `events` table with every row the legacy system produced while consuming `trace.ndjson`. Snapshots of the old `LiveDataHub` are not captured (they are in-memory and not load-bearing for correctness).

4. **`observed.mqtt.ndjson`** — every MQTT event the legacy system published during the capture window, captured by the same subscriber that captured the inbound frames. One line per publish.

### 1.3 Where to capture

Production hardware. A staging-rig replay won't surface timing quirks the real STM32 exhibits (the ~123 Hz is not exactly 123 Hz; `ts` field has millisecond jitter; broker disconnects are real). First capture happens during a scheduled window with at least one engineer on-site watching.

## 2. How to replay

The rewrite's ingest service exposes a test-only entry point:

```
hermes-ingest --replay trace.ndjson --config config.snapshot.json \
              --output-db replay.sqlite --output-mqtt replay.mqtt.ndjson \
              --deterministic
```

`--deterministic` flags:
- Wall-clock is frozen to `trace.ndjson`'s first `recv_ts`. All downstream `time.time()` calls inside ingest/detection are served from a mock clock that advances by the next frame's `recv_ts - prev_recv_ts`.
- Random-seeded components are seeded with 0.
- Worker queue flush intervals (`BATCH_FLUSH_INTERVAL`) are collapsed — the harness forces a flush after each frame so batching is not time-dependent.
- MQTT publishes are intercepted and written to `replay.mqtt.ndjson` instead of hitting a broker.
- No live TTL timer — TTL checks are driven off the mock clock.

This mirrors how `pytest-freezegun` operates but at a service level rather than test level.

The legacy system is replayed the same way via a separate adapter script (`scripts/replay_legacy.py`) that feeds `trace.ndjson` into the legacy `mqtt_data_consumer` with the same mock clock.

## 3. Diff comparison

### 3.1 Comparable artefacts

After both replays, we have four files each:

```
observed.sqlite / observed.mqtt.ndjson   — from the legacy system
replay.sqlite    / replay.mqtt.ndjson    — from the rewrite
```

### 3.2 The diff tool

`scripts/golden_diff.py` reads both pairs and emits:

1. **Event-row diff** (`events` table). For each `(device_id, sensor_id, event_type, timestamp_bucketed_to_1ms)`, check:
   - Presence in both: must match.
   - `sensor{N}_value`, `sensor{N}_variance`, `sensor{N}_average`: equal to 6 decimal places (relative tolerance `1e-6`).
   - `event_datetime`, `created_at`: equal to millisecond precision.
   - `event_flags`: equal.
   - `data_window` BLOB: unpacked, compared sample-by-sample (timestamp + value) with `1e-9` relative tolerance.

2. **MQTT publish diff** (`mqtt.ndjson`). For each published event `(timestamp, topic, payload)`:
   - Sequence order must match.
   - `topic` must match exactly.
   - `payload.timestamp` must match to ms.
   - `payload.sensor_value` must match to `1e-6` tolerance.

3. **Allowed-divergence suppressions.** The diff tool consumes a `allowed_differences.yaml` file listing FIX and FIX+FLAG items from `BUG_DECISION_LOG.md`. Each entry specifies an event pattern or column that is *expected* to differ, with a rationale. Example:

```yaml
suppressions:
  - id: fix-5-separate-rows-per-event-type
    kind: events_row_multiplicity
    description: |
      Legacy 500ms dedup merged multiple event types into one row.
      Rewrite emits one row per (sensor, type).
    legacy_shape: row with multiple sensor{N}_event_{a,b,c,d} = 'Yes'
    rewrite_shape: N separate rows, one per flagged type
    action: collapse-before-compare
  - id: fix-18-strict-threshold-validation
    kind: absent_event_from_rewrite
    description: |
      Legacy silently rewrote upper <= lower to upper = lower + 0.01
      and may have produced events under the artificial band.
      Rewrite rejects the config, so zero events are produced.
    precondition: |
      config_row.upper_threshold <= config_row.lower_threshold + 0.01
    action: ignore-rewrite-empty
```

If the diff tool finds a divergence NOT covered by a suppression, it fails with a precise description of the unexpected difference.

### 3.3 CI integration

`golden_diff.py` runs as part of the release pipeline. The rewrite's CI artefact includes:

- A pinned `trace.ndjson` + `config.snapshot.json` + `observed.*` baseline (checked into `tests/golden/baseline_2026-04-*.*` with git LFS, not raw git).
- Replay against the rewrite.
- Diff vs baseline.
- Explicit failure if any unexpected divergence appears.

A rewrite release is blocked if the diff tool reports unsuppressed differences. A suppression can be added only with explicit reference to a `BUG_DECISION_LOG.md` entry, and the CI job greps for that reference — dangling suppressions fail the build.

## 4. Capture schedule

Minimum two independent captures:

1. **Baseline capture (Week 1)**: 24 h on production hardware, normal traffic. Goal: establish what "identical behaviour" means.
2. **Edge-case capture (Week 4)**: Staged 8 h capture during which we deliberately trigger: sensor unplug, broker restart, NTP step forward +10 s and back, manual event injection on one sensor, high-variance signal pattern per event type. Goal: cover branches the natural 24 h didn't hit.

Captures are repeated before every major release. Each capture increments a suffix (`baseline_2026-04-23.ndjson`, `baseline_2026-05-07.ndjson`, etc.) and the prior captures are not deleted — they serve as historical regression evidence.

## 5. Storage & size

A 24 h capture at 123 Hz × 12 sensors × ~200 bytes/frame ≈ 260 MB of NDJSON. Compresses with zstd to ~30 MB. Git LFS is acceptable; raw git is not.

`observed.sqlite` at 24 h with ~200 events and 18 s × 123 Hz × 12 sensors of BLOB per event ≈ 50 MB raw, ~8 MB compressed.

Per capture, total artefact footprint ≈ 40 MB compressed. Storing the last 6 captures costs ~240 MB — manageable.

## 6. Operational runbook

### 6.1 Taking a capture

```bash
# on the capture host (not the production app host; must be independent)
python3 scripts/mqtt_capture.py \
    --broker localhost --topics 'stm32/adc' 'stm32/events/#' \
    --outdir captures/baseline_$(date +%Y-%m-%d)/ \
    --duration 86400 \
    --fsync-interval 1

# start time T — take full config snapshot
python3 scripts/snapshot_config.py \
    --db /mnt/ssd/mqtt_database/mqtt_database.db \
    --output captures/baseline_$(date +%Y-%m-%d)/config.snapshot.json

# end time T+24h — stop capture, take DB snapshot
sqlite3 /mnt/ssd/mqtt_database/mqtt_database.db \
    ".backup captures/baseline_$(date +%Y-%m-%d)/observed.sqlite"

# compress
cd captures/ && tar cvf - baseline_$(date +%Y-%m-%d)/ | zstd > \
    baseline_$(date +%Y-%m-%d).tar.zst
```

### 6.2 Running a diff

```bash
# decompress a baseline
zstd -d baseline_2026-04-23.tar.zst && tar xf baseline_2026-04-23.tar

# replay against rewrite
hermes-ingest --replay baseline_2026-04-23/trace.ndjson \
              --config baseline_2026-04-23/config.snapshot.json \
              --output-db /tmp/replay.sqlite \
              --output-mqtt /tmp/replay.mqtt.ndjson \
              --deterministic

# diff
python3 scripts/golden_diff.py \
    --baseline baseline_2026-04-23/ \
    --replay /tmp/ \
    --suppressions tests/golden/allowed_differences.yaml \
    --report /tmp/diff_report.html

# if anything failed, the report lists exactly which rows diverged
```

### 6.3 Adding a suppression

Appears only in review context:

1. Diff run fails: "Unexpected divergence at event id=1234, column=sensor3_event_b".
2. Developer traces it to `BUG_DECISION_LOG.md` entry #18 ("strict threshold validation").
3. Developer adds entry to `allowed_differences.yaml` with `id: fix-18-...` and `description: <copy from BUG_DECISION_LOG>`.
4. PR reviewer verifies the entry matches a real FIX/FIX+FLAG decision and the scope of the suppression is not broader than the fix.
5. CI re-runs; diff passes with explicit tolerance.

Suppressions are **never** added silently. Every one is reviewed.

## 7. What the golden plan does not cover

Honesty check: even a perfect golden-traffic pass is not sufficient. It doesn't cover:

- **Load scalability**: golden traffic is at ~123 Hz, one device. Multi-device scaling needs separate perf tests.
- **Failure modes**: broker-down-for-hours, disk-full, memory pressure. These need their own chaos/fault-injection tests.
- **Future hardware changes**: if the STM32 firmware changes its payload shape, the golden capture is out of date. Re-capture.
- **Human-UX behaviours**: the live charts, graph rendering, CSV exports — none of this is tested by the harness. UI regression is Playwright's job.
- **Anything the current system does wrong silently** and the rewrite "accidentally" fixes. If the fix isn't in `BUG_DECISION_LOG.md`, the diff will fail — that's the intended behaviour, and it forces the developer to either add a decision entry or back out the change.

The harness guarantees **behavioural continuity where we claim continuity**, not that the system as a whole is correct. It is one of several quality gates, not all of them.

## 8. Minimum viable harness (Phase 0.75)

Before any of the rewrite's detection code is written, the minimum harness is:

1. `scripts/mqtt_capture.py` — writes `trace.ndjson` and `observed.mqtt.ndjson` by subscribing to the broker. ~80 lines of Python.
2. `scripts/snapshot_config.py` — dumps all config tables to JSON. ~40 lines.
3. One baseline capture on production hardware.
4. `scripts/replay_legacy.py` — drives the legacy consumer off `trace.ndjson` with a mock clock. ~120 lines.
5. `scripts/golden_diff.py` — a first version that only diffs event rows (MQTT diff can come later). ~200 lines.

With those five pieces, we can verify the replay produces byte-identical output from the legacy system to itself — which is the control case before we trust it against the rewrite.

Then, as each rewrite module lands (ingest, detection, storage), we add its counterpart replay entry point. The harness evolves with the rewrite.

## 9. Success criteria

A release of the rewrite ships only when:

- The latest baseline capture replayed through the rewrite produces an event row set matching (modulo suppressions) the legacy system's event row set for the same inputs.
- The MQTT publish stream matches (modulo suppressions).
- All suppressions trace to entries in `BUG_DECISION_LOG.md`.
- The diff report HTML is attached to the release artefacts.

If any one of these fails, the release is blocked. No exceptions.
