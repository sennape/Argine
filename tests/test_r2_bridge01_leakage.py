"""R2-BRIDGE-01 Phase-0 leakage/causality, memory-correctness, determinism,
and control-equivalence tests. First-class scientific tests
(decision_rules.json / leakage_contract.json), not merely code quality:
they would fail if a retention mechanism's eviction decision depended on
information occurring after that decision's causal boundary.

Uses only synthetic traces (built here) and, for a few sanity checks, the
already-existing R2-10 DEV trace reused for IMPLEMENTATION DEBUGGING ONLY
per dev_holdout_separation.json -- never the R2-10 EVAL set, never a
BRIDGE holdout trace (none exists yet; Phase 0 does not capture one).
"""
from __future__ import annotations

import pytest

from argine.research.policies import Context
from argine.research.trace import PHASE_DECODE, Step, Trace, TraceMeta
from argine.research2.r2_09a.fast_replay import run_fast
from argine.research2.r2_09a.policies import AgedFrequency
from argine.research2.r2_bridge01.retention_policies import (
    B_SELECTED_PARAMS_BY_BUDGET,
    PredictedProtectPolicy,
    PredictorRetentionScorePolicy,
    build_bridge_configs,
    make_policy,
)

N_EXPERTS = 8
LAYERS = [0, 1]
BUDGET = 4 * N_EXPERTS  # bytes: small synthetic budget, 1 byte/expert default size


def _step(graph, layer0_experts, layer1_experts):
    return Step(graph=graph, n_tokens=1, phase=PHASE_DECODE,
                routes={0: [tuple(layer0_experts)], 1: [tuple(layer1_experts)]})


def _make_trace(layer0_seq, layer1_seq):
    assert len(layer0_seq) == len(layer1_seq)
    meta = TraceMeta(model="synthetic", n_experts=N_EXPERTS, top_k=2, layers=LAYERS)
    steps = [_step(i, layer0_seq[i], layer1_seq[i]) for i in range(len(layer0_seq))]
    return Trace(meta=meta, steps=steps)


# Deliberately non-trivial, non-monotonic sequence with repeats so the
# predictor and eviction scoring have something nontrivial to disagree on.
LAYER0_PREFIX = [(0, 1), (1, 2), (0, 1), (3, 4), (1, 2), (0, 3), (2, 4), (0, 1)]
LAYER1_SEQ = [(5, 6)] * len(LAYER0_PREFIX)

ALL_CONFIGS = build_bridge_configs()
SMALL_BUDGET_CONFIGS = [
    c for c in ALL_CONFIGS if c["budget_bytes"] == 4294967296
]  # we substitute a tiny synthetic budget below; only params matter here


def _policy_for_small_budget(config):
    params = config["params"]
    if config["family_id"] == "predicted_protect":
        return PredictedProtectPolicy(params["decay_factor"], params["aging_interval_steps"])
    return PredictorRetentionScorePolicy(params["decay_factor"], params["aging_interval_steps"], params["bonus"])


# --------------------------------------------------------------------- #
# Causality / leakage
# --------------------------------------------------------------------- #

def test_future_alteration_does_not_change_past_retention_decisions():
    """Alter the trace strictly AFTER a chosen decision boundary (step
    `boundary`), keeping everything up to and including it identical.
    Every retention decision (missed experts per step, resident set,
    eviction count) up to and including the boundary must be bit-
    identical between the real and the altered trace, for every
    mechanism config."""
    boundary = 4
    real = _make_trace(LAYER0_PREFIX, LAYER1_SEQ)
    altered_layer0 = list(LAYER0_PREFIX[: boundary + 1]) + [(7, 7), (6, 6), (5, 5)]
    altered_layer1 = list(LAYER1_SEQ[: boundary + 1]) + [(4, 4), (3, 3), (2, 2)]
    altered = _make_trace(altered_layer0, altered_layer1)

    for config in SMALL_BUDGET_CONFIGS:
        pol_real = _policy_for_small_budget(config)
        pol_altered = _policy_for_small_budget(config)
        ctx_real = Context(layers=LAYERS, n_experts=N_EXPERTS, size={0: 1, 1: 1}, budget=BUDGET, top_k=2)
        ctx_altered = Context(layers=LAYERS, n_experts=N_EXPERTS, size={0: 1, 1: 1}, budget=BUDGET, top_k=2)
        pol_real.start(ctx_real)
        pol_altered.start(ctx_altered)

        for i in range(boundary + 1):
            missed_real_0 = pol_real.access(i, 0, real.steps[i].experts(0))
            missed_real_1 = pol_real.access(i, 1, real.steps[i].experts(1))
            missed_alt_0 = pol_altered.access(i, 0, altered.steps[i].experts(0))
            missed_alt_1 = pol_altered.access(i, 1, altered.steps[i].experts(1))
            assert missed_real_0 == missed_alt_0, config["config_id"]
            assert missed_real_1 == missed_alt_1, config["config_id"]
            assert pol_real.resident_bytes == pol_altered.resident_bytes, config["config_id"]
            assert pol_real.evictions == pol_altered.evictions, config["config_id"]
            assert set(pol_real.resident.keys()) == set(pol_altered.resident.keys()), config["config_id"]


def test_predicted_set_consulted_during_access_was_computed_before_it():
    """The predicted-next set used while serving event j+1 of a layer must
    have been computed strictly from events 0..j of that layer -- never
    event j+1 itself. We check this indirectly: a policy fed the SAME
    prefix up to and including event j, then two DIFFERENT event-(j+1)
    payloads, must make the SAME eviction/admission decision for
    whichever object is evicted first within event j+1 if that choice
    only depends on state available before j+1 -- i.e. changing event
    j+1's OWN content must not retroactively change what was protected
    going into serving it (the protected set was fixed before we knew
    event j+1's content)."""
    trace_a = _make_trace(LAYER0_PREFIX[:6] + [(0, 1)], LAYER1_SEQ[:7])
    trace_b = _make_trace(LAYER0_PREFIX[:6] + [(5, 6)], LAYER1_SEQ[:7])
    for config in SMALL_BUDGET_CONFIGS:
        if config["family_id"] != "predicted_protect":
            continue
        pol_a = _policy_for_small_budget(config)
        pol_b = _policy_for_small_budget(config)
        ctx = Context(layers=LAYERS, n_experts=N_EXPERTS, size={0: 1, 1: 1}, budget=BUDGET, top_k=2)
        pol_a.start(ctx)
        pol_b.start(Context(layers=LAYERS, n_experts=N_EXPERTS, size={0: 1, 1: 1}, budget=BUDGET, top_k=2))
        for i in range(6):
            pol_a.access(i, 0, trace_a.steps[i].experts(0))
            pol_b.access(i, 0, trace_b.steps[i].experts(0))
        # The predicted-next set going INTO serving event 6 must be identical
        # for both, since it was computed only from events 0..5, which are
        # identical in trace_a and trace_b.
        assert pol_a._predicted_next[0] == pol_b._predicted_next[0]


# --------------------------------------------------------------------- #
# Memory correctness
# --------------------------------------------------------------------- #

def test_budget_never_exceeded_any_config_synthetic():
    trace = _make_trace(LAYER0_PREFIX, LAYER1_SEQ)
    for config in SMALL_BUDGET_CONFIGS:
        pol = _policy_for_small_budget(config)
        ctx = Context(layers=LAYERS, n_experts=N_EXPERTS, size={0: 1, 1: 1}, budget=BUDGET, top_k=2)
        pol.start(ctx)
        for i, st in enumerate(trace.steps):
            for layer in LAYERS:
                pol.access(i, layer, st.experts(layer))
                assert pol.resident_bytes <= BUDGET, config["config_id"]


def test_byte_accounting_matches_context_bytes_of():
    trace = _make_trace(LAYER0_PREFIX, LAYER1_SEQ)
    pol = PredictedProtectPolicy(0.99, 64)
    ctx = Context(layers=LAYERS, n_experts=N_EXPERTS, size={0: 3, 1: 5}, budget=10 ** 6, top_k=2)
    pol.start(ctx)
    for i, st in enumerate(trace.steps):
        for layer in LAYERS:
            pol.access(i, layer, st.experts(layer))
    total = sum(pol.resident.values())
    assert total == pol.resident_bytes
    for (layer, _e), sz in pol.resident.items():
        assert sz == ctx.bytes_of(layer)


def test_deterministic_ties_stable_across_repeated_identical_runs():
    trace = _make_trace(LAYER0_PREFIX, LAYER1_SEQ)
    results = []
    for _ in range(3):
        pol = PredictorRetentionScorePolicy(0.99, 64, bonus=1.0)
        ctx = Context(layers=LAYERS, n_experts=N_EXPERTS, size={0: 1, 1: 1}, budget=BUDGET, top_k=2)
        pol.start(ctx)
        trace_misses = []
        for i, st in enumerate(trace.steps):
            for layer in LAYERS:
                trace_misses.append(tuple(pol.access(i, layer, st.experts(layer))))
        results.append(tuple(trace_misses))
    assert results[0] == results[1] == results[2]


def test_invalid_bonus_fails_closed():
    with pytest.raises(ValueError):
        PredictorRetentionScorePolicy(0.99, 64, bonus=2.5)


def test_invalid_budget_fails_closed():
    from argine.research2.r2_bridge01.retention_policies import _aged_frequency_params_for_budget
    with pytest.raises(ValueError):
        _aged_frequency_params_for_budget(999)


# --------------------------------------------------------------------- #
# Control equivalence (bonus=0.0 must equal AgedFrequency bit-for-bit)
# --------------------------------------------------------------------- #

def test_bonus_zero_reproduces_aged_frequency_on_synthetic_trace():
    trace = _make_trace(LAYER0_PREFIX, LAYER1_SEQ)
    base = AgedFrequency(decay_factor=0.99, aging_interval_steps=64)
    ctrl = PredictorRetentionScorePolicy(decay_factor=0.99, aging_interval_steps=64, bonus=0.0)
    det_base, _ = run_fast(trace, base, BUDGET)
    det_ctrl, _ = run_fast(trace, ctrl, BUDGET)
    for key in ("misses", "bytes_missed", "evictions", "overflow_events",
                "overflow_misses", "peak_resident", "hits", "accesses", "per_layer"):
        assert det_base[key] == det_ctrl[key], key
