"""
Round-trip tests over the synthetic corpora.

For each scenario we:
  1. Replay the corpus through the rewrite's detection engine.
  2. Compare the captured events against a saved baseline.

To re-bless a baseline (e.g. after a deliberate behaviour change
documented in BUG_DECISION_LOG.md):

    HERMES_GOLDEN_UPDATE=1 pytest -m golden tests/golden

Make sure to inspect the diff in ``tests/golden/baselines/`` before
committing — every blessed change is a contract update.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.detection.config import (
    ModeSwitchingConfig,
    TypeAConfig,
    TypeBConfig,
    TypeCConfig,
    TypeDConfig,
)
from tests.golden.harness import (
    HarnessConfig,
    assert_matches_baseline,
    replay,
)

CORPORA = Path(__file__).parent / "corpora"
BASELINES = Path(__file__).parent / "baselines"


@pytest.mark.golden
def test_type_a_high_variance() -> None:
    """Type A must fire on sensor 1 once high CV% develops, nowhere else."""
    cfg = HarnessConfig()  # default Type A enabled, threshold_cv=2.0
    events = replay(CORPORA / "type_a_high_variance.ndjson", cfg)
    assert_matches_baseline(
        actual=events,
        baseline_path=BASELINES / "type_a_high_variance.events.ndjson",
    )


@pytest.mark.golden
def test_mode_break_transition() -> None:
    """STARTUP → BREAK on sensor 5; triggered_at = first below-threshold ts."""
    cfg = HarnessConfig(
        # Disable Type A so its variance window doesn't fire on the
        # square-wave portion of the corpus and pollute the baseline.
        type_a=TypeAConfig(enabled=False),
        type_b=TypeBConfig(enabled=False),
        type_c=TypeCConfig(enabled=False),
        type_d=TypeDConfig(enabled=False),
        mode_switching=ModeSwitchingConfig(
            enabled=True,
            startup_threshold=80.0,
            break_threshold=20.0,
            startup_duration_seconds=0.1,
            break_duration_seconds=0.5,
        ),
    )
    events = replay(CORPORA / "mode_break.ndjson", cfg)
    assert_matches_baseline(
        actual=events,
        baseline_path=BASELINES / "mode_break.events.ndjson",
    )


@pytest.mark.golden
def test_stable_sine_fires_nothing() -> None:
    """Smoke: a sub-threshold signal must produce zero events."""
    cfg = HarnessConfig()  # default Type A enabled
    events = replay(CORPORA / "stable_sine.ndjson", cfg)
    # No baseline file needed — assert directly that nothing fired.
    # If detection regresses to a false-positive, this fails loudly.
    assert events == [], f"unexpected events on stable input: {events}"
