"""R2-BRIDGE-01 memory-correctness, determinism, and control-equivalence
checks against a real R2-10 DEV trace (implementation debugging only, per
dev_holdout_separation.json -- never the R2-10 EVAL set, never a BRIDGE
holdout trace). Numeric results here are NOT reported as a scientific
BRIDGE finding; this module only asserts structural invariants hold on a
real, non-toy trace, in addition to the exhaustive (all 12 configs)
synthetic-trace coverage in tests/test_r2_bridge01_leakage.py. Scoped to
a small number of real-trace replays (each real-trace replay costs
~30s) to stay within a practical test-runtime budget.
"""
from __future__ import annotations

import os

import pytest

from argine.research.trace import Trace
from argine.research2.r2_09a.fast_replay import run_fast
from argine.research2.r2_09a.policies import AgedFrequency
from argine.research2.r2_bridge01.retention_policies import build_bridge_configs, make_policy

DEV_TRACE_PATH = os.environ.get(
    "R2_BRIDGE01_DEV_TRACE",
    os.path.expanduser(
        "~/mnt/Local--Argine3/research/R2-01/captures/"
        "286a41477ce54d5fb9ccc24f4e508f22/run-00-4G/trace.jsonl.gz"
    ),
)
PRIMARY_BUDGET = 4294967296

pytestmark = pytest.mark.skipif(
    not os.path.exists(DEV_TRACE_PATH),
    reason="R2-10 DEV trace not present on this machine; synthetic-trace "
           "tests in test_r2_bridge01_leakage.py already cover the same "
           "invariants (for all 12 configs) without requiring a real capture.",
)


@pytest.fixture(scope="module")
def dev_trace():
    return Trace.load(DEV_TRACE_PATH)


@pytest.fixture(scope="module")
def ctrl_config():
    return next(
        c for c in build_bridge_configs()
        if c["family_id"] == "predictor_retention_score"
        and c["budget_bytes"] == PRIMARY_BUDGET
        and c["params"]["bonus"] == 0.0
    )


@pytest.fixture(scope="module")
def pp_config():
    return next(
        c for c in build_bridge_configs()
        if c["family_id"] == "predicted_protect" and c["budget_bytes"] == PRIMARY_BUDGET
    )


@pytest.fixture(scope="module")
def det_base(dev_trace):
    base = AgedFrequency(decay_factor=0.99, aging_interval_steps=64)
    det, _ = run_fast(dev_trace, base, PRIMARY_BUDGET)
    return det


@pytest.fixture(scope="module")
def det_ctrl(dev_trace, ctrl_config):
    pol = make_policy(ctrl_config)
    det, _ = run_fast(dev_trace, pol, PRIMARY_BUDGET)
    return det


@pytest.fixture(scope="module")
def det_pp(dev_trace, pp_config):
    pol = make_policy(pp_config)
    det, _ = run_fast(dev_trace, pol, PRIMARY_BUDGET)
    return det


def test_budget_never_exceeded_on_real_dev_trace(det_ctrl, det_pp, ctrl_config, pp_config):
    assert det_ctrl["peak_resident"] <= ctrl_config["budget_bytes"]
    assert det_pp["peak_resident"] <= pp_config["budget_bytes"]


def test_control_cell_bonus_zero_matches_b_selected_on_real_dev_trace(det_base, det_ctrl):
    for key in ("misses", "bytes_missed", "evictions", "overflow_events",
                "overflow_misses", "peak_resident", "hits", "accesses", "per_layer"):
        assert det_base[key] == det_ctrl[key], key


def test_determinism_repeated_replay_bit_identical(dev_trace, ctrl_config, det_ctrl):
    pol2 = make_policy(ctrl_config)
    det2, _ = run_fast(dev_trace, pol2, PRIMARY_BUDGET)
    assert det2 == det_ctrl
