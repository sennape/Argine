"""R2-10 Phase-0 leakage, causal-boundary, determinism, and output-contract
tests. First-class scientific tests (decision_rules.json), not merely code
quality: they would fail if a predictor accessed information occurring
after its prediction boundary.
"""
from __future__ import annotations

import pytest

from argine.research.trace import Step, Trace, TraceMeta
from argine.research2.r2_10.causal_walk import (
    iter_causal_contexts,
    same_layer_stream,
    target_at,
)
from argine.research2.r2_10.baselines import (
    GlobalFrequencyBaseline,
    LastObservationBaseline,
    shuffled_null_descriptive_recall,
)
from argine.research2.r2_10.output_contract import apply_output_contract, recall_at_k

N_EXPERTS = 8
TOP_K = 2
LAYER = 0
OTHER_LAYER = 1


def _step(graph: int, layer0_experts, layer1_experts, phase="decode"):
    return Step(graph=graph, n_tokens=1, phase=phase,
                routes={LAYER: [tuple(layer0_experts)], OTHER_LAYER: [tuple(layer1_experts)]})


def _make_trace(layer0_seq, layer1_seq):
    assert len(layer0_seq) == len(layer1_seq)
    meta = TraceMeta(model="synthetic", n_experts=N_EXPERTS, top_k=TOP_K, layers=[LAYER, OTHER_LAYER])
    steps = [_step(i, layer0_seq[i], layer1_seq[i]) for i in range(len(layer0_seq))]
    return Trace(meta=meta, steps=steps)


# A deliberately non-trivial, non-monotonic sequence so last-observation and
# global-frequency baselines can disagree with each other.
LAYER0_PREFIX = [(0, 1), (1, 2), (0, 1), (3, 4), (1, 2), (0, 3)]
LAYER1_BASE = [(5, 6)] * len(LAYER0_PREFIX)


def test_causal_context_never_exposes_future_same_layer_entries():
    stream = [(i, LAYER0_PREFIX[i]) for i in range(len(LAYER0_PREFIX))]
    for ctx in iter_causal_contexts(stream, k=TOP_K):
        assert len(ctx.history) == ctx.position + 1
        assert ctx.history == tuple(stream[: ctx.position + 1])
        seen_positions = set(t for t, _ in ctx.history)
        for future_pos in range(ctx.position + 1, len(stream)):
            assert stream[future_pos][0] not in seen_positions or stream[future_pos][0] <= ctx.token_index


def test_same_layer_future_alteration_does_not_change_past_predictions():
    i = 2  # prediction instant
    altered_tail = [(9, 9), (8, 8), (7, 7)]  # deliberately different from the real tail
    layer0_T = LAYER0_PREFIX
    layer0_Tprime = LAYER0_PREFIX[: i + 1] + altered_tail[: len(LAYER0_PREFIX) - i - 1]
    assert layer0_T[: i + 1] == layer0_Tprime[: i + 1]
    assert layer0_T != layer0_Tprime

    trace_T = _make_trace(layer0_T, LAYER1_BASE)
    trace_Tprime = _make_trace(layer0_Tprime, LAYER1_BASE)

    stream_T = same_layer_stream(trace_T, LAYER, phase="decode")
    stream_Tprime = same_layer_stream(trace_Tprime, LAYER, phase="decode")

    ctx_T = list(iter_causal_contexts(stream_T, k=TOP_K))[i]
    ctx_Tprime = list(iter_causal_contexts(stream_Tprime, k=TOP_K))[i]
    assert ctx_T == ctx_Tprime  # structural: history is bit-identical

    gfb = GlobalFrequencyBaseline(N_EXPERTS)
    lob = LastObservationBaseline(N_EXPERTS)
    for predictor in (gfb, lob):
        pred_T = predictor.predict(ctx_T, k=TOP_K)
        pred_Tprime = predictor.predict(ctx_Tprime, k=TOP_K)
        assert pred_T == pred_Tprime, (predictor.family_id, pred_T, pred_Tprime)


def test_same_step_later_layer_alteration_does_not_affect_layer_l_stream():
    layer1_T = LAYER1_BASE
    layer1_Tprime = [(7, 7)] * len(LAYER1_BASE)  # layer > l altered throughout
    assert layer1_T != layer1_Tprime

    trace_T = _make_trace(LAYER0_PREFIX, layer1_T)
    trace_Tprime = _make_trace(LAYER0_PREFIX, layer1_Tprime)

    stream_T = same_layer_stream(trace_T, LAYER, phase="decode")
    stream_Tprime = same_layer_stream(trace_Tprime, LAYER, phase="decode")
    assert stream_T == stream_Tprime  # layer-l walker is structurally blind to layer>l content


def test_target_at_horizon_indexing_and_end_of_trace_unavailable():
    stream = [(i, LAYER0_PREFIX[i]) for i in range(len(LAYER0_PREFIX))]
    assert target_at(stream, position=0, horizon=1) == stream[1]
    assert target_at(stream, position=0, horizon=4) == stream[4]
    last = len(stream) - 1
    assert target_at(stream, position=last, horizon=1) is None
    assert target_at(stream, position=last - 1, horizon=4) is None
    with pytest.raises(ValueError):
        target_at(stream, position=0, horizon=0)


def test_determinism_same_trace_config_horizon_identical_predictions():
    trace = _make_trace(LAYER0_PREFIX, LAYER1_BASE)
    stream = same_layer_stream(trace, LAYER, phase="decode")
    contexts = list(iter_causal_contexts(stream, k=TOP_K))
    gfb1, gfb2 = GlobalFrequencyBaseline(N_EXPERTS), GlobalFrequencyBaseline(N_EXPERTS)
    for ctx in contexts:
        assert gfb1.predict(ctx, k=TOP_K) == gfb2.predict(ctx, k=TOP_K)


def test_cold_start_position_zero_satisfies_cardinality():
    trace = _make_trace(LAYER0_PREFIX, LAYER1_BASE)
    stream = same_layer_stream(trace, LAYER, phase="decode")
    ctx0 = next(iter_causal_contexts(stream, k=TOP_K))
    for predictor in (GlobalFrequencyBaseline(N_EXPERTS), LastObservationBaseline(N_EXPERTS)):
        pred = predictor.predict(ctx0, k=TOP_K)
        assert len(pred) == TOP_K == len(set(pred))
        assert all(0 <= e < N_EXPERTS for e in pred)


def test_apply_output_contract_cardinality_uniqueness_validity_and_errors():
    scores = [0.0] * N_EXPERTS
    scores[3] = 5.0
    scores[1] = 5.0  # tie with 3; expert_id ascending breaks it
    pred = apply_output_contract(scores, k=3, n_experts=N_EXPERTS)
    assert len(pred) == 3 == len(set(pred))
    assert pred[0] == 1 and pred[1] == 3  # tie broken by lower expert_id first
    assert all(0 <= e < N_EXPERTS for e in pred)

    with pytest.raises(ValueError):
        apply_output_contract([0.0] * (N_EXPERTS - 1), k=3, n_experts=N_EXPERTS)
    with pytest.raises(ValueError):
        apply_output_contract(scores, k=0, n_experts=N_EXPERTS)
    with pytest.raises(ValueError):
        apply_output_contract(scores, k=N_EXPERTS + 1, n_experts=N_EXPERTS)
    with pytest.raises(ValueError):
        apply_output_contract([float("nan")] * N_EXPERTS, k=1, n_experts=N_EXPERTS)


def test_recall_at_k_matches_definition():
    assert recall_at_k([1, 2, 3], [2, 3, 4]) == pytest.approx(2 / 3)
    assert recall_at_k([1, 2], [1, 2]) == 1.0
    with pytest.raises(ValueError):
        recall_at_k([1, 2], [])


def test_shuffled_null_is_descriptive_only_and_reproducible():
    trace = _make_trace(LAYER0_PREFIX, LAYER1_BASE)
    stream = same_layer_stream(trace, LAYER, phase="decode")
    r1 = shuffled_null_descriptive_recall(stream, horizon=1, n_experts=N_EXPERTS, seed=0)
    r2 = shuffled_null_descriptive_recall(stream, horizon=1, n_experts=N_EXPERTS, seed=0)
    assert r1 == r2  # explicitly seeded -> reproducible even though non-causal
