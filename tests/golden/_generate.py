"""
Synthetic corpus generators.

Run as ``python -m tests.golden._generate`` to (re)write the NDJSON
files in ``corpora/``. The output is checked into git so CI doesn't
re-generate on every run; the script exists so a developer can
regenerate the corpora deterministically when they need to evolve a
scenario (for instance, adding a Type B trigger to ``mode_break``).

Each generator emits frames in the same shape the production capture
script writes: ``{"recv_ts", "topic", "payload": {...}}``. The
``recv_ts`` values are spaced by ``DT`` seconds; payload timestamps
match.

Why hand-rolled signal generators rather than a library:
    The corpora must be byte-stable across Python versions and
    platforms. A NumPy-based generator could shift values between
    versions of NumPy. Pure-Python ``math``/``random`` with a fixed
    seed gives us reproducible bytes.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

CORPORA_DIR = Path(__file__).parent / "corpora"
DT = 0.01  # 10 ms ticks → 100 Hz, matching production


def _frame(recv_ts: float, device_id: int, sensor_values: list[float]) -> dict[str, Any]:
    """Build one frame in the capture-script schema.

    Matches the STM32 payload shape from
    ``services/hermes/ingest/parser.py``: 12 sensor values split into
    ``adc1`` (sensors 1..6) and ``adc2`` (sensors 7..12).
    """
    assert len(sensor_values) == 12, "expected exactly 12 sensor values"
    return {
        "recv_ts": recv_ts,
        "topic": "stm32/adc",
        "payload": {
            "device_id": device_id,
            # STM32 ts in milliseconds — for synthetic corpora we just
            # mirror recv_ts. Real captures preserve whatever the STM32
            # actually sent.
            "ts": int(recv_ts * 1000),
            "adc1": sensor_values[0:6],
            "adc2": sensor_values[6:12],
        },
    }


def _write(name: str, frames: list[dict[str, Any]]) -> Path:
    out = CORPORA_DIR / f"{name}.ndjson"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fp:
        for frame in frames:
            fp.write(json.dumps(frame, sort_keys=True))
            fp.write("\n")
    return out


# ─── Scenario: Type A high-variance trigger on sensor 1 ──────────


def _type_a_high_variance(seed: int = 1) -> list[dict[str, Any]]:
    """A 5 s trace where sensor 1 develops high variance halfway through.

    First ~2.5 s: all 12 sensors stable around 50.0 with ±0.1 noise.
    Last ~2.5 s: sensor 1 alternates 30 ↔ 70 each tick — high CV%
    that should fire Type A under the harness's default threshold of
    ``threshold_cv=2.0``.
    """
    rng = random.Random(seed)
    frames: list[dict[str, Any]] = []
    base = 1700000000.0
    n_ticks = 500  # 5 s at 100 Hz

    for i in range(n_ticks):
        ts = base + i * DT
        sensors: list[float] = []
        for sid in range(1, 13):
            if sid == 1 and i >= n_ticks // 2:
                # Square-wave-ish high-CV signal.
                sensors.append(70.0 if i % 2 == 0 else 30.0)
            else:
                # Stable around 50 with small jitter.
                sensors.append(50.0 + rng.uniform(-0.1, 0.1))
        frames.append(_frame(ts, device_id=1, sensor_values=sensors))
    return frames


# ─── Scenario: STARTUP → BREAK transition on sensor 5 ──────────


def _mode_break() -> list[dict[str, Any]]:
    """Drive sensor 5 through POWER_ON → STARTUP → BREAK.

    The harness configures this with ``ModeSwitchingConfig(enabled=True,
    startup_threshold=80.0, break_threshold=20.0,
    startup_duration_seconds=0.1, break_duration_seconds=0.5)`` so:

      * t=0.0–0.5 s: sensor 5 = 100.0  → above startup_threshold for
        more than startup_duration_seconds → enters STARTUP at ~0.1 s.
      * t=0.5 s onward: sensor 5 = 10.0 → below break_threshold for
        more than break_duration_seconds → BREAK fires at ~1.0 s,
        triggered_at = the FIRST below-threshold sample (t=0.5).

    Type A is enabled at a high threshold so it doesn't fire and
    pollute the baseline; the assertion is that exactly one BREAK
    fires with the expected timestamp.

    Other 11 sensors stay at 50 (in-band).
    """
    frames: list[dict[str, Any]] = []
    base = 1700000000.0
    n_ticks = 250  # 2.5 s at 100 Hz

    for i in range(n_ticks):
        ts = base + i * DT
        sensors: list[float] = [50.0] * 12
        if i < 50:
            sensors[4] = 100.0  # sensor index 4 == sensor_id 5
        else:
            sensors[4] = 10.0
        frames.append(_frame(ts, device_id=1, sensor_values=sensors))
    return frames


# ─── Sine-wave smoke trace for non-firing baseline ──────────────


def _stable_sine() -> list[dict[str, Any]]:
    """A short steady signal where no detector should fire.

    Useful as a smoke baseline: confirms the harness produces an
    empty event list when nothing should trigger. Catches false-
    positive regressions.
    """
    frames: list[dict[str, Any]] = []
    base = 1700000000.0
    n_ticks = 200  # 2 s

    for i in range(n_ticks):
        ts = base + i * DT
        # Slow drift, low amplitude — well below Type A threshold.
        v = 50.0 + 0.5 * math.sin(2 * math.pi * 0.5 * i * DT)
        frames.append(_frame(ts, device_id=1, sensor_values=[v] * 12))
    return frames


# ─── Entry point ────────────────────────────────────────────────


def main() -> None:
    written = []
    written.append(_write("type_a_high_variance", _type_a_high_variance()))
    written.append(_write("mode_break", _mode_break()))
    written.append(_write("stable_sine", _stable_sine()))
    for path in written:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
