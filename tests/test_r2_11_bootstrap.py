"""Unit tests for the R2-11 frozen paired bootstrap, on synthetic data only."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argine.research2.r2_11.bootstrap import paired_bootstrap_ci, BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED, CONFIDENCE_LEVEL


def test_frozen_constants():
    assert BOOTSTRAP_RESAMPLES == 100_000
    assert BOOTSTRAP_SEED == 42
    assert CONFIDENCE_LEVEL == 0.95


def test_deterministic_given_seed():
    deltas = [-5.0, -3.0, -1.0, 2.0, -4.0, -2.0, 0.5, -6.0, -1.5, -0.5, 1.0, -3.5]
    r1 = paired_bootstrap_ci(deltas, resamples=2000, seed=42)
    r2 = paired_bootstrap_ci(deltas, resamples=2000, seed=42)
    assert r1 == r2


def test_all_negative_deltas_gives_negative_ci():
    deltas = [-5.0, -3.0, -4.0, -6.0, -2.0, -4.5, -3.5, -5.5, -2.5, -4.2, -3.8, -4.9]
    mean, lo, hi = paired_bootstrap_ci(deltas, resamples=5000, seed=42)
    assert mean < 0
    assert lo <= hi
    assert hi < 0  # all strongly negative, CI should not cross zero


def test_mixed_sign_deltas_can_straddle_zero():
    deltas = [10.0, -10.0, 5.0, -5.0, 1.0, -1.0, 8.0, -8.0, 2.0, -2.0, 0.0, 0.0]
    mean, lo, hi = paired_bootstrap_ci(deltas, resamples=5000, seed=42)
    assert lo <= mean <= hi


def test_single_point_mass_deltas():
    deltas = [-7.0] * 12
    mean, lo, hi = paired_bootstrap_ci(deltas, resamples=1000, seed=42)
    assert mean == -7.0
    assert lo == -7.0
    assert hi == -7.0


def test_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        paired_bootstrap_ci([], resamples=10, seed=42)
