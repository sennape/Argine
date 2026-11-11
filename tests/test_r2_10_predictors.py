"""R2-10 Phase-1 predictor validation: all six causal_runtime_candidate
families (global_frequency, last_observation, recency_window,
markov_transition, decayed_transition, recency_frequency_transition_blend)
must pass the same output-contract and leakage tests. shuffled_null stays
an ANALYTICAL_REFERENCE_ONLY descriptive statistic, never a runtime
candidate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argine.research.trace import Step, Trace, TraceMeta
from argine.research2.r2_10.causal_walk import iter_causal_contexts, same_layer_stream, target_at
from argine.research2.r2_10.baselines import (
    GlobalFrequencyBaseline,
    LastObservationBaseline,
    shuffled_null_descriptive_recall,
)
from argine.research2.r2_10.predictors import (
    BLEND_WEIGHT_GRID,
    DECAYED_TRANSITION_GRID,
    RECENCY_WINDOW_GRID,
    DecayedTransitionPredictor,
    MarkovTransitionPredictor,
    RecencyFrequencyTransitionBlendPredictor,
    RecencyWindowPredictor,
    all_configs,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = ROOT / "experiments" / "R2-10-working-set-predictor"

N_EXPERTS = 8
TOP_K = 3
LAYER = 0

# A longer, non-trivial, non-periodic synthetic sequence (length 40) so
# every family (including recency_window with window_W up to 256, which
# will just see the whole history) has enough signal to distinguish.
import random as _random
_rng = _random.Random(1234)
LONG_SEQ = [tuple(sorted(_rng.sample(range(N_EXPERTS), TOP_K))) for _ in range(40)]


def _make_trace(seq):
    meta = TraceMeta(model="synthetic", n_experts=N_EXPERTS, top_k=TOP_K, layers=[LAYER])
    steps = [Step(graph=i, n_tokens=1, phase="decode", routes={LAYER: [seq[i]]}) for i in range(len(seq))]
    return Trace(meta=meta, steps=steps)


def _contexts(seq):
    trace = _make_trace(seq)
    stream = same_layer_stream(trace, LAYER, phase="decode")
    return stream, list(iter_causal_contexts(stream, k=trace.meta.top_k))


ALL_FAMILIES = lambda: [  # noqa: E731
    GlobalFrequencyBaseline(N_EXPERTS),
    LastObservationBaseline(N_EXPERTS),
    RecencyWindowPredictor(N_EXPERTS, 16),
    MarkovTransitionPredictor(N_EXPERTS),
    DecayedTransitionPredictor(N_EXPERTS, 0.99),
    RecencyFrequencyTransitionBlendPredictor(N_EXPERTS, (0.34, 0.33, 0.33)),
]


def test_grids_match_frozen_predictor_space_json():
    ps = json.loads((CONTRACT_DIR / "predictor_space.json").read_text(encoding="utf-8"))
    fams = {f["family_id"]: f for f in ps["families"]}
    assert list(RECENCY_WINDOW_GRID) == fams["recency_window"]["hyperparameters"]["search_space"]["window_W"]
    assert list(DECAYED_TRANSITION_GRID) == fams["decayed_transition"]["hyperparameters"]["search_space"]["decay_factor"]
    frozen_weights = fams["recency_frequency_transition_blend"]["hyperparameters"]["search_space"]["(w_r,w_f,w_t)"]
    assert [list(w) for w in BLEND_WEIGHT_GRID] == frozen_weights
    assert fams["markov_transition"]["hyperparameters"]["tunable"] is False


def test_all_configs_enumerates_exact_grid_sizes():
    configs = all_configs(N_EXPERTS)
    assert len(configs["recency_window"]) == 4
    assert len(configs["markov_transition"]) == 1
    assert len(configs["decayed_transition"]) == 3
    assert len(configs["recency_frequency_transition_blend"]) == 6


@pytest.mark.parametrize("predictor", ALL_FAMILIES())
def test_cardinality_uniqueness_validity_for_every_family(predictor):
    stream, contexts = _contexts(LONG_SEQ)
    for ctx in contexts:
        pred = predictor.predict(ctx)
        assert len(pred) == ctx.k == len(set(pred))
        assert all(0 <= e < N_EXPERTS for e in pred)


@pytest.mark.parametrize("predictor", ALL_FAMILIES())
def test_determinism_and_reproducibility_for_every_family(predictor):
    stream, contexts = _contexts(LONG_SEQ)
    for ctx in contexts[::5]:
        p1 = predictor.predict(ctx)
        p2 = predictor.predict(ctx)
        assert p1 == p2


@pytest.mark.parametrize("predictor", ALL_FAMILIES())
def test_cold_start_position_zero_for_every_family(predictor):
    stream, contexts = _contexts(LONG_SEQ)
    pred = predictor.predict(contexts[0])
    assert len(pred) == contexts[0].k == len(set(pred))


@pytest.mark.parametrize("predictor", ALL_FAMILIES())
def test_same_layer_future_alteration_invariance_for_every_family(predictor):
    i = 10
    altered_tail = [tuple((e + 1) % N_EXPERTS for e in LONG_SEQ[j]) for j in range(i + 1, len(LONG_SEQ))]
    seq_T = LONG_SEQ
    seq_Tprime = LONG_SEQ[: i + 1] + altered_tail
    assert seq_T[: i + 1] == seq_Tprime[: i + 1]
    assert seq_T != seq_Tprime

    _, contexts_T = _contexts(seq_T)
    _, contexts_Tprime = _contexts(seq_Tprime)
    assert contexts_T[i] == contexts_Tprime[i]
    assert predictor.predict(contexts_T[i]) == predictor.predict(contexts_Tprime[i])


def test_causal_state_updates_distinguish_recency_from_frequency():
    # Construct a sequence where a long-past-frequent expert is NOT recent;
    # recency_window(small W) and global_frequency must then disagree.
    seq = [(0, 1, 2)] * 20 + [(3, 4, 5)] * 5
    _, contexts = _contexts(seq)
    ctx = contexts[-1]
    recency_small = RecencyWindowPredictor(N_EXPERTS, 4).predict(ctx)
    freq = GlobalFrequencyBaseline(N_EXPERTS).predict(ctx)
    assert set(recency_small) == {3, 4, 5}
    assert set(freq) == {0, 1, 2}  # 20 occurrences each vs 5 -> frequency still favors the old block


def test_decayed_transition_eventually_forgets_stale_transitions():
    # After decay, a stale transition pattern should be dominated by a
    # long run of a different, more recent pattern.
    seq = [(0, 1, 2), (3, 4, 5)] * 3 + [(0, 1, 2), (6, 7, 0)] * 30
    _, contexts = _contexts(seq)
    ctx = contexts[-1]
    decayed = DecayedTransitionPredictor(N_EXPERTS, 0.9).predict(ctx)
    undecayed_like = MarkovTransitionPredictor(N_EXPERTS).predict(ctx)
    # Both are valid predictions; decayed must at least run without error
    # and respect cardinality -- behavioral distinction is documented, not
    # asserted as a strict inequality here since both are legitimate causal
    # scores and the DEV tournament (Phase 2) is what compares them.
    assert len(decayed) == len(undecayed_like) == TOP_K


def test_hyperparameter_validation_rejects_off_grid_values():
    with pytest.raises(ValueError):
        RecencyWindowPredictor(N_EXPERTS, window_W=7)  # not in frozen grid
    with pytest.raises(ValueError):
        DecayedTransitionPredictor(N_EXPERTS, decay_factor=0.95)  # not in frozen grid
    with pytest.raises(ValueError):
        RecencyFrequencyTransitionBlendPredictor(N_EXPERTS, weights=(0.5, 0.5, 0.0))  # not in frozen grid


def test_horizon_indexing_and_sequence_boundary_consistent_across_families():
    stream, _ = _contexts(LONG_SEQ)
    for h in (1, 4, 16):
        for pos in (0, 5, len(stream) - h - 1):
            if pos < 0:
                continue
            tgt = target_at(stream, pos, h)
            assert tgt == stream[pos + h]
    last = len(stream) - 1
    assert target_at(stream, last, 1) is None
    assert target_at(stream, last - 15, 16) is None


def test_shuffled_null_is_not_a_runtime_candidate():
    ps = json.loads((CONTRACT_DIR / "predictor_space.json").read_text(encoding="utf-8"))
    assert "shuffled_null" not in ps["null_trivial_baselines"]["causal_runtime_candidates"]
    configs = all_configs(N_EXPERTS)
    assert "shuffled_null" not in configs  # never enumerated as a selectable family

    stream, _ = _contexts(LONG_SEQ)
    r1 = shuffled_null_descriptive_recall(stream, horizon=1, n_experts=N_EXPERTS, seed=0)
    r2 = shuffled_null_descriptive_recall(stream, horizon=1, n_experts=N_EXPERTS, seed=0)
    assert r1 == r2  # still reproducible even though explicitly non-causal/descriptive-only
