"""
Event detection package.

Per-sensor detectors for Types A/B/C/D plus the engine that coordinates
them. Pure-Python hot path — no async, no I/O — so the logic is testable
in isolation and runs comfortably in the ingest consumer task.

Module layout:
    types.py     — Sample / DetectedEvent / Protocols (SensorDetector, EventSink)
    config.py    — typed per-detector config dataclasses
    engine.py    — DetectionEngine: routes samples to per-sensor detectors
    type_a.py    — Type A (variance/CV%) detector — incremental O(1) sliding window
    sink.py      — EventSink implementations (logging now; DB wiring in Phase 3e)

Behavioral parity with the legacy system is verified by the
golden-traffic replay harness (see docs/contracts/GOLDEN_TRAFFIC_PLAN.md).
"""
