"""Proves the fast DEV-tournament runner (dev_runner.py) reproduces the
already-tested slow path (retention_policies.py, committed and tested in
Phase 0) bit-for-bit, before the fast path is trusted for the full
8-instance x 12-config x 3-budget DEV tournament."""
from __future__ import annotations

import os

import pytest

from argine.research.trace import Trace
from argine.research2.r2_09a.fast_replay import run_fast
from argine.research2.r2_bridge01 import dev_runner as fast
from argine.research2.r2_bridge01.retention_policies import (
    PredictedProtectPolicy,
    PredictorRetentionScorePolicy,
    B_SELECTED_PARAMS_BY_BUDGET,
)

DEV_TRACE_PATH = os.environ.get(
    "R2_BRIDGE01_DEV_TRACE",
    os.path.expanduser(
        "~/mnt/Local--Argine3/research/R2-01/captures/"
        "286a41477ce54d5fb9ccc24f4e508f22/run-00-4G/trace.jsonl.gz"
    ),
)

pytestmark = pytest.mark.skipif(not os.path.exists(DEV_TRACE_PATH), reason="DEV trace not present on this machine")

COMPARE_KEYS = ("misses", "bytes_missed", "evictions", "overflow_events",
                "overflow_misses", "peak_resident", "hits", "accesses", "per_layer")


@pytest.fixture(scope="module")
def trace():
    return Trace.load(DEV_TRACE_PATH)


@pytest.fixture(scope="module")
def predictions(trace):
    return fast.precompute_predictions(trace)


def test_predicted_protect_equivalence(trace, predictions):
    budget = 4294967296
    params = B_SELECTED_PARAMS_BY_BUDGET[budget]
    slow = PredictedProtectPolicy(params["decay_factor"], params["aging_interval_steps"])
    det_slow, _ = run_fast(trace, slow, budget)
    fast_pol = fast.make_fast_policy("predicted_protect", budget, predictions)
    det_fast = fast.run_fast_bridge(trace, fast_pol, budget)
    for key in COMPARE_KEYS:
        assert det_slow[key] == det_fast[key], key


def test_predictor_retention_score_bonus1_equivalence(trace, predictions):
    budget = 4294967296
    params = B_SELECTED_PARAMS_BY_BUDGET[budget]
    slow = PredictorRetentionScorePolicy(params["decay_factor"], params["aging_interval_steps"], bonus=1.0)
    det_slow, _ = run_fast(trace, slow, budget)
    fast_pol = fast.make_fast_policy("predictor_retention_score", budget, predictions, bonus=1.0)
    det_fast = fast.run_fast_bridge(trace, fast_pol, budget)
    for key in COMPARE_KEYS:
        assert det_slow[key] == det_fast[key], key


def test_predictor_retention_score_bonus0_equivalence_at_secondary_budget(trace, predictions):
    budget = 1610612736
    params = B_SELECTED_PARAMS_BY_BUDGET[budget]
    slow = PredictorRetentionScorePolicy(params["decay_factor"], params["aging_interval_steps"], bonus=0.0)
    det_slow, _ = run_fast(trace, slow, budget)
    fast_pol = fast.make_fast_policy("predictor_retention_score", budget, predictions, bonus=0.0)
    det_fast = fast.run_fast_bridge(trace, fast_pol, budget)
    for key in COMPARE_KEYS:
        assert det_slow[key] == det_fast[key], key
