"""
Shared helpers for the golden tier.

Tests in this directory don't need DB/MQTT — they replay corpora
deterministically through the in-memory detection engine. The
harness itself is in ``harness.py``; this conftest is a thin layer
that just declares the ``golden`` marker for selective runs.

Run only the golden tier with ``pytest -m golden``. Re-bless
baselines with ``HERMES_GOLDEN_UPDATE=1 pytest -m golden``.
"""

from __future__ import annotations
