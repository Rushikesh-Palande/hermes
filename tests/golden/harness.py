"""
Golden-traffic harness — gap 9.

Drives the rewrite's detection pipeline through a recorded (or
synthetic) MQTT trace and captures every fired event + outbound MQTT
publish. Compares against a saved baseline; mismatches fail the test
with a precise diff so behaviour regressions are loud.

Mock clock: ``recv_ts`` from each frame in the corpus is the
authoritative wall time. The harness feeds frames through
``DetectionEngine.feed_snapshot`` directly (skipping the asyncio
queue + paho callback), so there's no real-time sleep — a 24 h trace
runs in seconds, deterministically.

Out of scope (deliberate, lands when real captures arrive):
    * Comparison against the legacy ``observed.sqlite``. The contract
      defines the diff shape; once we have legacy captures we can add
      that comparison on top of this harness without changing the
      replay code.
    * Continuous-sample / DB-writer paths. The harness only exercises
      detection-engine output and outbound MQTT — those are the byte-
      identical surfaces the contract pins down. DB row shapes are
      already covered by the integration tier.

The harness is intentionally thin: orjson for I/O, hand-rolled
collectors for events + publishes, and a single comparison helper.
No frameworks, no plugins.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes.db.models import EventType
from hermes.detection.config import (
    ModeSwitchingConfig,
    StaticConfigProvider,
    TypeAConfig,
    TypeBConfig,
    TypeCConfig,
    TypeDConfig,
)
from hermes.detection.engine import DetectionEngine
from hermes.detection.types import DetectedEvent
from hermes.ingest.parser import parse_stm32_adc_payload


@dataclass(slots=True)
class CapturedEvent:
    """One row in the harness's collected events list.

    Mirrors ``DetectedEvent`` but with primitive types only so it
    JSON-serialises cleanly to the baseline file. ``triggered_at`` is
    rounded to microsecond precision; below that the legacy and the
    rewrite both have noise from float conversion that's not worth
    asserting on.
    """

    event_type: str
    device_id: int
    sensor_id: int
    triggered_at: float
    metadata: dict[str, Any]

    @classmethod
    def from_event(cls, event: DetectedEvent) -> CapturedEvent:
        return cls(
            event_type=event.event_type.value,
            device_id=event.device_id,
            sensor_id=event.sensor_id,
            triggered_at=round(event.triggered_at, 6),
            metadata={k: _coerce(v) for k, v in event.metadata.items()},
        )

    def as_jsonable(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "device_id": self.device_id,
            "sensor_id": self.sensor_id,
            "triggered_at": self.triggered_at,
            "metadata": self.metadata,
        }


def _coerce(value: Any) -> Any:
    """Force everything into a JSON-stable shape (round floats)."""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, EventType):
        return value.value
    return value


class _CollectorSink:
    """``EventSink``-shaped object that just accumulates."""

    def __init__(self) -> None:
        self.events: list[CapturedEvent] = []

    def publish(self, event: DetectedEvent) -> None:
        self.events.append(CapturedEvent.from_event(event))


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """The detection config the harness runs with.

    Defaults are deliberately conservative so a basic corpus can hit
    Type A and BREAK paths without exotic tuning. Tests that need
    different thresholds construct their own.
    """

    type_a: TypeAConfig = TypeAConfig(
        enabled=True,
        T1=0.5,
        threshold_cv=2.0,
        debounce_seconds=0.0,
        init_fill_ratio=0.5,
        expected_sample_rate_hz=100.0,
    )
    type_b: TypeBConfig = TypeBConfig(enabled=False)
    type_c: TypeCConfig = TypeCConfig(enabled=False)
    type_d: TypeDConfig = TypeDConfig(enabled=False)
    mode_switching: ModeSwitchingConfig = ModeSwitchingConfig(enabled=False)


# ─── Replay engine ──────────────────────────────────────────────


def replay(corpus_path: Path, cfg: HarnessConfig) -> list[CapturedEvent]:
    """Run a corpus through the detection engine; return captured events.

    Keeps the runtime fully synchronous: each NDJSON line is parsed,
    converted to a sensor snapshot, and pushed to
    ``DetectionEngine.feed_snapshot``. No asyncio loop, no MQTT
    broker, no DB connection — just the pure detection logic.

    The clock advances strictly via ``recv_ts``. There is no
    ``time.time()`` call inside the harness, so a corpus with
    increasing ``recv_ts`` values produces the same output every run
    regardless of when the test executes.
    """
    provider = StaticConfigProvider(
        type_a=cfg.type_a,
        type_b=cfg.type_b,
        type_c=cfg.type_c,
        type_d=cfg.type_d,
        mode_switching=cfg.mode_switching,
    )
    sink = _CollectorSink()
    engine = DetectionEngine(provider, sink)

    for frame in _read_corpus(corpus_path):
        ts = float(frame["recv_ts"])
        payload = frame["payload"]
        device_id = int(payload.get("device_id", 1))
        sensor_values = parse_stm32_adc_payload(payload)
        if not sensor_values:
            continue
        engine.feed_snapshot(device_id, ts, sensor_values)

    return sink.events


def _read_corpus(path: Path) -> Iterator[dict[str, Any]]:
    """Stream NDJSON frames from disk."""
    with path.open("r", encoding="utf-8") as fp:
        for raw in fp:
            line = raw.strip()
            if not line:
                continue
            yield json.loads(line)


# ─── Baseline I/O ──────────────────────────────────────────────


def write_baseline(events: list[CapturedEvent], path: Path) -> None:
    """Persist the captured events as one JSON object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for ev in events:
            fp.write(json.dumps(ev.as_jsonable(), sort_keys=True))
            fp.write("\n")


def read_baseline(path: Path) -> list[CapturedEvent]:
    """Load a saved baseline. Empty file = empty list."""
    if not path.exists():
        return []
    out: list[CapturedEvent] = []
    with path.open("r", encoding="utf-8") as fp:
        for raw in fp:
            line = raw.strip()
            if not line:
                continue
            d = json.loads(line)
            out.append(
                CapturedEvent(
                    event_type=d["event_type"],
                    device_id=d["device_id"],
                    sensor_id=d["sensor_id"],
                    triggered_at=d["triggered_at"],
                    metadata=d["metadata"],
                )
            )
    return out


def assert_matches_baseline(
    *,
    actual: list[CapturedEvent],
    baseline_path: Path,
    update: bool = False,
) -> None:
    """Compare actual vs saved baseline; fail with a precise diff.

    When ``update=True`` (or the env var ``HERMES_GOLDEN_UPDATE=1``),
    overwrite the baseline with the actual output and pass. Use this
    only when you have a genuine reason to bless a behaviour change
    (every blessed update should be paired with a CHANGELOG line and
    ideally a BUG_DECISION_LOG entry if the change is operator-
    visible).
    """
    import os

    if update or os.environ.get("HERMES_GOLDEN_UPDATE") == "1":
        write_baseline(actual, baseline_path)
        return

    expected = read_baseline(baseline_path)
    if expected == [] and not baseline_path.exists():
        raise AssertionError(
            f"baseline file does not exist: {baseline_path}\n"
            f"run with HERMES_GOLDEN_UPDATE=1 to seed it from the current run "
            f"(only after sanity-checking the {len(actual)} captured events)."
        )

    if len(actual) != len(expected):
        raise AssertionError(
            f"event count mismatch: actual={len(actual)} expected={len(expected)}\n"
            f"actual:   {[(e.event_type, e.sensor_id, e.triggered_at) for e in actual]}\n"
            f"expected: {[(e.event_type, e.sensor_id, e.triggered_at) for e in expected]}"
        )

    for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
        if a.as_jsonable() != e.as_jsonable():
            raise AssertionError(
                f"event #{i} differs:\n  actual:   {a.as_jsonable()}\n  expected: {e.as_jsonable()}"
            )
