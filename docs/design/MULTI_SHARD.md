# MULTI_SHARD.md — Layer 3 horizontal scaling

> **Status:** designed and shipped in v0.1.0-alpha.15. Default deployments
> stay single-process; multi-shard is opt-in via env var.
>
> **Audience:** anyone running HERMES in production or considering a
> deployment that exceeds the single-process throughput ceiling.

---

## 1. The load and why this exists

Production target on Raspberry Pi 4 (4 cores, 2 GB RAM):

| Quantity                | Number          |
| ----------------------- | --------------- |
| Devices                 | 20              |
| Sensors per device      | 12              |
| Sample interval         | 10 ms (100 Hz)  |
| MQTT messages/s         | 2 000           |
| Sensor readings/s       | 24 000          |
| Detector updates/s      | 96 000          |

The single-process bench (alpha.14) sustains **~16 700 msg/s on a
laptop** and an estimated **~5 500 msg/s on a Pi 4** — comfortably
above the 2 000 msg/s production target. So why ship multi-shard?

**Safety**, not throughput. Specifically:

1. **Burst tolerance.** A GC pause, a slow DB write, or a config
   reload that briefly stalls the event loop can let the asyncio
   queue back up. Multi-shard distributes the slack across cores.
2. **Future device-count growth.** Doubling devices to 40 doubles
   the load. Single-process headroom on the Pi 4 (~2.7×) wouldn't
   absorb that with comfort.
3. **Co-tenant CPU pressure.** If the Pi runs other services
   (Redis, Mosquitto, the SvelteKit dev server during operator
   diagnostics), the GIL-bound Python loop competes with them on
   one core. Multi-shard moves the hot path to dedicated cores.

If those scenarios don't apply to your deployment, **stay
single-process** — the architecture is simpler and uses less RAM.

---

## 2. Operating modes

Selected via `HERMES_INGEST_MODE` env var on each process:

```
┌──────────────┬──────────────────────────────────────────────────────────┐
│ Mode         │ Behaviour                                                │
├──────────────┼──────────────────────────────────────────────────────────┤
│ "all"        │ Single process subscribes to stm32/adc, runs detection   │
│ (default)    │ on every device, fills live ring buffer, owns DB sink    │
│              │ + outbound MQTT. Used in single-process deployments.     │
│              │                                                          │
│ "shard"      │ One of N detection processes. Subscribes to stm32/adc,   │
│              │ drops messages where device_id % shard_count != index.   │
│              │ Runs detection + DB sink + outbound MQTT for its slice.  │
│              │ Does NOT fill the live ring buffer.                      │
│              │ Requires shard_count > 1.                                │
│              │                                                          │
│ "live_only"  │ Subscribes to stm32/adc, fills live ring buffer for      │
│              │ SSE, runs NO detection. Used by the API process when    │
│              │ shards are running.                                      │
└──────────────┴──────────────────────────────────────────────────────────┘
```

Implemented in [`services/hermes/config.py`](../../services/hermes/config.py)
(`Settings.hermes_ingest_mode`, `_validate_shard_config`) and
[`services/hermes/ingest/main.py`](../../services/hermes/ingest/main.py)
(`IngestPipeline.__init__`, `_consume`).

---

## 3. Topology

### 3.1 Single-process (default — alpha.14 and earlier)

```
            ┌──────────────────┐
            │   Mosquitto      │
            └─────────┬────────┘
                      │ stm32/adc
                      ▼
            ┌──────────────────┐
            │ hermes-ingest    │   IngestPipeline (mode="all"):
            │  + detection     │     • parse + clock + offsets
            │  + DB sink       │     • detection engine (4 types × 12 sensors × 20 devs)
            │  + outbound MQTT │     • TTL gate
            │  + live ring     │     • DB sink (events + windows)
            │    buffer        │     • outbound MQTT (stm32/events/...)
            └─────────┬────────┘     • live ring buffer for SSE
                      │
                      ▼
                 Postgres
                      ▲
                      │
            ┌──────────────────┐
            │   hermes-api     │   FastAPI; reads DB, embeds
            │   (FastAPI)      │   IngestPipeline(mode="all") for SSE
            └─────────┬────────┘
                      │ HTTP / SSE
                      ▼
                  Browser
```

### 3.2 Multi-shard (opt-in — alpha.15)

```
                       ┌────────── Mosquitto ───────────┐
                       │           ▲                    │
                       │  4 separate subscriptions      │ stm32/events/<dev>/<sid>/<TYPE>
                       │  to stm32/adc (one per shard,  │ (each shard publishes its slice)
                       │  filter in code)               │
                       ▼                                │
        ┌─────────────┬─────────────┬─────────────┬─────────────┐
        │ ingest-0    │ ingest-1    │ ingest-2    │ ingest-3    │
        │ devs %4==0  │ devs %4==1  │ devs %4==2  │ devs %4==3  │   "shard" mode
        │ (4,8,12,16, │ (1,5,9,13,  │ (2,6,10,14, │ (3,7,11,15, │
        │  20)        │  17)        │  18)        │  19)        │
        │             │             │             │             │
        │ detection + │ detection + │ detection + │ detection + │
        │ DB sink +   │ DB sink +   │ DB sink +   │ DB sink +   │
        │ TTL gate    │ TTL gate    │ TTL gate    │ TTL gate    │
        └──────┬──────┴──────┬──────┴──────┬──────┴──────┬──────┘
               └─────────────┴──────┬──────┴─────────────┘
                                    ▼
                                Postgres                    ◄── single shared DB
                                 ▲   ▲
                  config writes  │   │  NOTIFY hermes_config_changed
                  (parameters)   │   │  (LISTEN in each shard)
                                 │   │
                       ┌─────────┴───┴──────┐
                       │   hermes-api       │   "live_only" mode:
                       │   (FastAPI)        │     subscribes stm32/adc for ALL devices,
                       │   + IngestPipeline │     fills live ring buffer for SSE,
                       │     (live_only)    │     runs NO detection
                       └─────────┬──────────┘
                                 │ HTTP / SSE
                                 ▼
                             Browser
```

Key invariants:

* **Per-device routing.** A device's `device_id` deterministically lands
  on exactly one shard via `device_id % shard_count`. Detection state,
  TTL timers, and outbound MQTT publishes for that device all happen on
  the same shard for the lifetime of the process.
* **Disjoint slicing.** The union of all shards' device sets equals the
  full set; the intersection is empty. (Verified by
  [`tests/unit/test_consume_shard.py::test_all_shards_combined_cover_all_devices_with_no_overlap`](../../tests/unit/test_consume_shard.py).)
* **Single source of truth.** All shards write to the same Postgres.
  No coordinator process. The DB does the synchronisation.
* **Live SSE keeps working.** The API runs its own MQTT subscription in
  `live_only` mode and fills its own live ring buffer for ALL devices.
  Detection shards don't fill the API's live buffer; the API doesn't
  run detection. Two parallel MQTT consumers, each doing half the work.

---

## 4. Topic sharding strategy

We chose **filter-in-code** over MQTT shared subscriptions or per-device
topics. Each shard subscribes to the full `stm32/adc` topic and discards
messages whose `device_id` doesn't hash to its index.

### Why filter-in-code

| Option                                   | Trade-off                                                                                                  |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| **MQTT shared subscriptions** (`$share`) | Broker round-robins per message — splits a single device's stream across shards, breaking detection state. |
| **Per-device topics** (`stm32/adc/<id>`) | Cleanest, but requires hardware firmware change. Out of scope for the rewrite phase.                       |
| **Filter-in-code** (chosen)              | Wasteful network (each shard sees every message), but parse-and-discard is cheap and detection state stays | per-device.

At 2 000 msg/s, four shards each parsing every message is 8 000 parses/s
total — well within the per-shard CPU budget. The discarded messages
never go through clock anchoring, offset application, or detection, so
the wasted work is bounded to ≈ 30 µs per discarded message.

Implementation:
[`services/hermes/ingest/main.py:_consume`](../../services/hermes/ingest/main.py)
— after `orjson.loads` we read `device_id` and skip if
`sharded and device_id % shard_count != shard_index`. The check happens
**before** any metric counter ticks, so summing
`hermes_msgs_received_total` across all shards gives the true total.

---

## 5. Cross-shard config sync (LISTEN/NOTIFY)

When the operator updates thresholds via `PUT /api/config/...`, the API
process commits the change and reloads its own provider in-process. In
multi-shard mode the detection shards are separate processes — they
have stale provider caches.

### The protocol

1. After committing, the API runs
   `SELECT pg_notify('hermes_config_changed', '<package_id>')` (see
   [`services/hermes/api/routes/config.py:_notify_config_changed`](../../services/hermes/api/routes/config.py)).
2. Each shard's `DbConfigProvider.start_listener()` (see
   [`services/hermes/detection/db_config.py`](../../services/hermes/detection/db_config.py))
   opens a dedicated asyncpg connection and `LISTEN`s on the channel.
3. asyncpg dispatches each notification to a sync callback `_on_notify`
   which spawns an async `_reload_and_reset` task. That task:
   - calls `provider.reload()` to refetch parameter rows
   - calls `engine.reset_device(device_id)` for each cached detector
4. New samples arriving after the reset see the new thresholds.

### Why a dedicated asyncpg connection

SQLAlchemy's pooled connections multiplex transactions across coroutines,
so a long-lived `LISTEN` would either monopolise a pool slot or be
silently torn down between transactions. asyncpg's
`Connection.add_listener` model is purpose-built for this and runs the
dispatch on its own internal reader task — zero impact on the asyncio
event loop.

### Failure modes

| Failure                                | What happens                                                                                                           |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Postgres restart                       | asyncpg reports the connection drop; we log + best-effort reconnect on the next start_listener cycle.                  |
| Network partition between API and DB   | API write fails (no commit, no NOTIFY) — operator sees the error.                                                      |
| Network partition between shard and DB | Shard misses the notification. On reconnect, the next API write resends.                                               |
| API crash mid-write                    | Postgres rolls back the parameter row — no NOTIFY emitted. Shards remain on the old config (correctly).                |
| Shard crash                            | systemd restarts; on startup `provider.reload()` reads the current state. No replay needed.                            |

### What's deliberately NOT done

We don't carry a generation counter or version stamp. The notification
is a fire-and-forget cache-invalidation hint; the source of truth is
the `parameters` table. A duplicate or out-of-order notification just
causes a redundant reload — idempotent and cheap.

---

## 6. Memory budget

Per-process Python heap on a Pi 4:

| Component                       | Approx. RSS    |
| ------------------------------- | -------------- |
| Python interpreter + stdlib     | 60 MB          |
| FastAPI + SQLAlchemy + asyncpg  | 60 MB          |
| paho-mqtt + orjson + structlog  | 20 MB          |
| `LiveDataHub` (2 000 samples)   | 8 MB / device  |
| `EventWindowBuffer` (30 s)      | 2 MB / device  |
| Detection state (4 types × 12 × 20) | 5 MB       |
| **Total per shard**             | **~150–200 MB** |

systemd unit caps:
- `hermes-ingest@.service` — `MemoryHigh=200M`, `MemoryMax=300M`
- `hermes-ingest.service`  — `MemoryHigh=400M`, `MemoryMax=600M`
- `hermes-api.service`     — `MemoryHigh=400M`, `MemoryMax=600M`

A 4-shard deployment uses ~600–800 MB total Python heap. On a 2 GB
Pi 4, that leaves ≥ 1 GB for Postgres, Mosquitto, page cache, and
nginx — comfortable.

---

## 7. Deployment

### 7.1 Single-process (default)

```bash
sudo systemctl enable --now hermes-api.service hermes-ingest.service
```

Both units already ship with `HERMES_INGEST_MODE=all` set in their
`[Service]` sections.

### 7.2 Multi-shard (4 cores)

```bash
# 1. Disable the single-process unit (it Conflicts= with the shard template).
sudo systemctl disable --now hermes-ingest.service

# 2. Set shard_count on the shared ingest env file.
echo 'HERMES_SHARD_COUNT=4' | sudo tee -a /etc/hermes/ingest.env

# 3. Tell the API to run live_only.
sudo sed -i 's/^HERMES_INGEST_MODE=.*/HERMES_INGEST_MODE=live_only/' \
    /etc/hermes/api.env

# 4. Enable the four shard instances.
sudo systemctl enable --now hermes-ingest@0.service \
                            hermes-ingest@1.service \
                            hermes-ingest@2.service \
                            hermes-ingest@3.service

# 5. Restart the API to pick up the live_only mode.
sudo systemctl restart hermes-api.service

# 6. Verify.
systemctl status hermes-ingest@*.service
journalctl -u hermes-ingest@0.service -f | grep ingest_starting
# Should print: mode=shard shard_index=0 shard_count=4
```

### 7.3 Rolling back to single-process

```bash
sudo systemctl disable --now hermes-ingest@*.service
sudo sed -i 's/^HERMES_INGEST_MODE=.*/HERMES_INGEST_MODE=all/' /etc/hermes/api.env
sudo systemctl enable --now hermes-ingest.service
sudo systemctl restart hermes-api.service
```

No data migration is required — Postgres state is identical between
modes.

---

## 8. What stays identical to single-process

These invariants hold regardless of shard count:

| Invariant                                                         | Where enforced                                              |
| ----------------------------------------------------------------- | ----------------------------------------------------------- |
| Detection thresholds (A/B/C/D bounds, debounce, ±9 s windows)     | `DbConfigProvider` (read by every shard from the same DB)   |
| Event priority + TTL dedup (A < B < C < D, BREAK bypass, 5 s TTL) | `TtlGateSink` (per-shard, but state is per-(device, sensor))|
| MQTT topic shape (`stm32/adc`, `stm32/events/<dev>/<sid>/<TYPE>`) | `mqtt_topic_*` settings, frozen by `HARDWARE_INTERFACE.md`  |
| DB row shape (events, event_windows, sessions, offsets)           | Migrations (append-only)                                    |
| API contracts (JSON shapes, status codes)                         | Integration tests                                           |

Multi-shard is **transparent to the device, the operator, and the UI**.

---

## 9. Tests

Unit:
- [`tests/unit/test_shard_config.py`](../../tests/unit/test_shard_config.py)
  — `Settings._validate_shard_config` rejects bad shard math.
- [`tests/unit/test_consume_shard.py`](../../tests/unit/test_consume_shard.py)
  — round-trips synthetic MQTT messages through `_consume` with various
  `(shard_count, shard_index)` and verifies device-set membership.

Integration / soak: pending. The right time to add them is when we have
a real Pi 4 to run a sustained load against. For now the unit tests
cover the routing math; correctness of the rest of the pipeline is
unchanged from single-process and exercised by the existing 144-test
suite.

---

## 10. Future work

- **MQTT shared subscriptions** (`$share/group/topic`) when paho-mqtt
  v5 + Mosquitto 2.x adoption is universal. Eliminates the
  filter-in-code waste at the cost of broker config complexity.
- **Per-device MQTT topics** (`stm32/adc/<id>`) when the firmware can
  publish them. Cleanest topology; trivial wildcard subscription per
  shard. Requires a hardware change.
- **Soak test on a real Pi 4** with Layer 1 + Layer 3 enabled, holding
  at 2 000 msg/s for 1 h, asserting p99 latency stays under N ms and
  no event drops.
- **Per-shard health endpoint** so the API can surface "shard 2 down"
  to ops dashboards.
