# Golden traffic harness — gap 9

Replays a recorded (or synthetic) MQTT trace through the rewrite's
detection pipeline and asserts the resulting **detected events** and
**outbound MQTT publishes** match a saved baseline. This is the
mechanism that catches behaviour drift between releases — and, once
real captures from production hardware are available, between the
rewrite and the legacy system per
[`docs/contracts/GOLDEN_TRAFFIC_PLAN.md`](../../docs/contracts/GOLDEN_TRAFFIC_PLAN.md).

## Status (alpha.23)

- ✅ Harness scaffolding: deterministic replay, in-process event
  collector, baseline read/write helpers.
- ✅ Synthetic corpora exercising Type A and BREAK paths.
- ⏳ Real legacy MQTT captures + `observed.sqlite` diff: pending
  production-hardware capture window. The harness is ready to drop
  them into `corpora/` and add a `baselines/` snapshot when they
  arrive.

## Layout

```
tests/golden/
├── corpora/        — input NDJSON traces (one frame per line)
├── baselines/      — expected event + MQTT-publish outputs (NDJSON)
├── harness.py      — replay engine + collector + baseline I/O
├── test_*.py       — round-trip tests, one per corpus
└── conftest.py     — markers + helpers shared across the tier
```

## Frame format

Each line in a corpus is a JSON object matching the capture schema
from `GOLDEN_TRAFFIC_PLAN.md` §1.2:

```json
{"recv_ts": 1700000000.0, "topic": "stm32/adc",
 "payload": {"device_id": 1, "ts": 0,
             "adc1": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
             "adc2": [20.0, 21.0, 22.0, 23.0, 24.0, 25.0]}}
```

`recv_ts` drives the deterministic clock — the harness feeds frames
through `_consume` in order, advancing wall time only via the
captured timestamps. There's no real-time sleep; a 24 h trace runs
in seconds.

## Adding a corpus

```python
from tests.golden.harness import GoldenRunner, write_baseline

corpus_path = "tests/golden/corpora/my_scenario.ndjson"
baseline_path = "tests/golden/baselines/my_scenario.events.ndjson"

# 1. Write the corpus (programmatic generator or copy a real capture).
# 2. Run once with `pytest --update-golden` (see harness.py) to seed
#    the baseline from the rewrite's current output.
# 3. Subsequent runs assert the output matches the saved baseline.
```

## Why synthetic seeds first

Real captures live in git LFS (per the contract — single capture is
~30 MB compressed); they need production hardware access and a
scheduled window. Synthetic corpora give us behaviour-regression
coverage now and define the harness shape so real captures slot in
without code changes when they arrive.
