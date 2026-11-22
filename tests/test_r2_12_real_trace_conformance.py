"""R2-12 real-trace conformance comparator -- synthetic unit tests.

Exercises tools/r2_12_real_trace_conformance.py's parse_trace/check_provenance/
compare functions directly against hand-built, schema-complete synthetic
JSONL trace events (the same shape servi_evento_con_politica emits -- see
RETENTION-POLICY-TRACE-INSTRUMENTATION.md for the schema). No real model, no
.psxs store, no llama-server, no subprocess, no R2-12 counted run: everything
here is pure comparator-logic testing. Ground-truth decisions are produced
by actually RUNNING the real frozen Python reference classes
(argine.research2.r2_09a.policies.AgedFrequency,
argine.research2.r2_bridge01.retention_policies.PredictedProtectPolicy) on a
hand-chosen event sequence and capturing their real output -- never
hand-computed/guessed expected values -- so a faithfully-built trace is
guaranteed to reflect genuine frozen-reference behavior before any test
deliberately corrupts a field to check detection. protected_before is a
normal, strictly-compared field for BOTH policies in these fixtures (the
real runtime's RetentionPolicy::protected_for() accessor closed what was
previously a reporting gap for predicted_protect -- see
RETENTION-POLICY-TRACE-INSTRUMENTATION.md).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from argine.research.policies import Context  # noqa: E402
from argine.research2.r2_09a.policies import AgedFrequency  # noqa: E402
from argine.research2.r2_bridge01.retention_policies import (  # noqa: E402
    PredictedProtectPolicy,
    FROZEN_PREDICTOR_DECAY,
)

import r2_12_real_trace_conformance as rtc  # noqa: E402


# ---------------------------------------------------------------------------
# Ground-truth trace generator: replays a real policy step by step and
# records a schema-complete event per step, exactly mirroring what the real
# C++ runtime's servi_evento_con_politica would have written. This is the
# single place that knows the trace schema; every test below builds its
# input through this, then (if it wants to test detection) corrupts exactly
# one field in a deep copy.
# ---------------------------------------------------------------------------

def _make_bsel_trace(decay_factor, interval, budget, bytes_of_layer, layer, event_specs):
    """event_specs: list of lists of expert ids, one per event, all on `layer`."""
    policy = AgedFrequency(decay_factor, interval)
    ctx = Context(layers=[layer], n_experts=128, size={layer: bytes_of_layer}, budget=budget)
    policy.start(ctx)
    evicted_log = []
    rtc._patch_evict_one(policy, "b_selected", evicted_log)

    events = []
    resident_bytes_running = 0
    for step, experts in enumerate(event_specs):
        resident_before = sorted(list(o) for o in policy.resident.keys())
        resident_before_rich = [
            {"layer": o[0], "expert": o[1], "score": policy.scores[o], "last_ref": policy.last_ref[o]}
            for o in policy.resident.keys()
        ]
        bytes_before = policy.resident_bytes
        evicted_log.clear()
        missed = policy.access(step, layer, experts)
        hit = sorted(e for e in sorted(set(experts)) if e not in missed)
        resident_after_rich = [
            {"layer": o[0], "expert": o[1], "score": policy.scores[o], "last_ref": policy.last_ref[o]}
            for o in policy.resident.keys()
        ]
        events.append({
            "step": step, "policy": "b_selected", "layer": layer, "bytes_of_layer": bytes_of_layer,
            "requested_experts": sorted(set(experts)), "hit_experts": hit, "miss_experts": sorted(missed),
            "protected_before": [],
            "resident_before": resident_before_rich,
            "evicted": [{"layer": l, "expert": e} for l, e in evicted_log],
            "resident_after": resident_after_rich,
            "cache_resident_bytes_before": bytes_before,
            "cache_resident_bytes_after": policy.resident_bytes,
            "policy_resident_bytes_after": policy.resident_bytes,
            "budget_bytes": budget,
            "policy_params": {
                "decay_factor": decay_factor, "aging_interval_steps": interval,
                "predictor_decay": 0.0, "top_k": 0, "n_experts": 0,
            },
        })
    return events


def _make_pp_trace(decay_factor, interval, budget, bytes_of_layer, layer, event_specs, top_k=2, n_experts=16):
    policy = PredictedProtectPolicy(decay_factor, interval)
    ctx = Context(layers=[layer], n_experts=n_experts, size={layer: bytes_of_layer}, budget=budget, top_k=top_k)
    policy.start(ctx)
    evicted_log = []
    rtc._patch_evict_one(policy, "predicted_protect", evicted_log)

    events = []
    for step, experts in enumerate(event_specs):
        protected_before = sorted(policy._predicted_next.get(layer, frozenset()))
        resident_before_rich = [
            {"layer": o[0], "expert": o[1], "score": policy.scores[o], "last_ref": policy.last_ref[o]}
            for o in policy.resident.keys()
        ]
        bytes_before = policy.resident_bytes
        evicted_log.clear()
        missed = policy.access(step, layer, experts)
        hit = sorted(e for e in sorted(set(experts)) if e not in missed)
        resident_after_rich = [
            {"layer": o[0], "expert": o[1], "score": policy.scores[o], "last_ref": policy.last_ref[o]}
            for o in policy.resident.keys()
        ]
        events.append({
            "step": step, "policy": "predicted_protect", "layer": layer, "bytes_of_layer": bytes_of_layer,
            "requested_experts": sorted(set(experts)), "hit_experts": hit, "miss_experts": sorted(missed),
            # RetentionPolicy::protected_for() (additive, read-only accessor
            # on the frozen module, see RETENTION-POLICY-TRACE-INSTRUMENTATION.md)
            # now exposes this exact value from the real runtime; this
            # fixture's `protected_before` (computed just above from
            # policy._predicted_next, before calling access()) is exactly
            # what a real, correctly-instrumented trace now reports.
            "protected_before": protected_before,
            "resident_before": resident_before_rich,
            "evicted": [{"layer": l, "expert": e} for l, e in evicted_log],
            "resident_after": resident_after_rich,
            "cache_resident_bytes_before": bytes_before,
            "cache_resident_bytes_after": policy.resident_bytes,
            "policy_resident_bytes_after": policy.resident_bytes,
            "budget_bytes": budget,
            "policy_params": {
                "decay_factor": decay_factor, "aging_interval_steps": interval,
                "predictor_decay": FROZEN_PREDICTOR_DECAY, "top_k": top_k, "n_experts": n_experts,
            },
        })
    return events


def _lines(events):
    return [json.dumps(e) + "\n" for e in events]


# ---------------------------------------------------------------------------
# 1. Parser
# ---------------------------------------------------------------------------

def test_parser_accepts_well_formed_trace():
    events = _make_bsel_trace(0.99, 64, 1_000_000, 100_000, 0, [[0, 1, 2]])
    parsed = rtc.parse_trace(_lines(events))
    assert len(parsed) == 1
    assert parsed[0]["policy"] == "b_selected"


def test_parser_rejects_malformed_json():
    with pytest.raises(rtc.TraceFormatError, match="not valid JSON"):
        rtc.parse_trace(["{not json at all", ""])


def test_parser_rejects_non_object_line():
    with pytest.raises(rtc.TraceFormatError, match="not a JSON object"):
        rtc.parse_trace(["[1, 2, 3]"])


def test_parser_rejects_missing_required_field():
    events = _make_bsel_trace(0.99, 64, 1_000_000, 100_000, 0, [[0, 1]])
    del events[0]["evicted"]
    with pytest.raises(rtc.TraceFormatError, match="missing required field"):
        rtc.parse_trace(_lines(events))


def test_parser_rejects_missing_policy_param_field():
    events = _make_bsel_trace(0.99, 64, 1_000_000, 100_000, 0, [[0, 1]])
    del events[0]["policy_params"]["aging_interval_steps"]
    with pytest.raises(rtc.TraceFormatError, match="policy_params missing"):
        rtc.parse_trace(_lines(events))


def test_parser_rejects_empty_trace():
    with pytest.raises(rtc.TraceFormatError, match="empty"):
        rtc.parse_trace(["", "   "])


def test_parser_skips_blank_lines_between_events():
    events = _make_bsel_trace(0.99, 64, 1_000_000, 100_000, 0, [[0, 1], [2]])
    lines = [_lines(events)[0], "\n", "   \n", _lines(events)[1]]
    parsed = rtc.parse_trace(lines)
    assert len(parsed) == 2


# ---------------------------------------------------------------------------
# 2. Hit/miss
# ---------------------------------------------------------------------------

def test_hit_miss_real_replay_matches():
    budget = 3 * 100_000 + 4096
    events = _make_bsel_trace(0.99, 64, budget, 100_000, 0, [[0, 1, 2], [0, 1], [3]])
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert ok, mismatch
    assert summary["events_compared"] == 3


def test_hit_miss_corrupted_hit_detected():
    budget = 3 * 100_000 + 4096
    events = _make_bsel_trace(0.99, 64, budget, 100_000, 0, [[0, 1, 2], [0, 1]])
    # Event 1 (step 1) genuinely hits both 0 and 1 (just admitted in step 0).
    # Falsely claim expert 1 missed instead.
    events[1]["hit_experts"] = [0]
    events[1]["miss_experts"] = [1]
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert not ok
    assert mismatch["step"] == 1
    assert mismatch["field"] in ("hit_experts", "miss_experts")


# ---------------------------------------------------------------------------
# 3. Eviction
# ---------------------------------------------------------------------------

def test_eviction_real_replay_matches_when_budget_forces_eviction():
    # Budget holds exactly 3 objects; the 4th distinct expert forces a real
    # eviction, driven entirely by the real AgedFrequency reference.
    budget = 3 * 100_000 + 4096
    events = _make_bsel_trace(0.99, 64, budget, 100_000, 0, [[0, 1, 2], [3]])
    assert len(events[1]["evicted"]) == 1, "fixture should force exactly one eviction"
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert ok, mismatch
    assert summary["total_evictions"] == 1


def test_eviction_wrong_victim_detected():
    budget = 3 * 100_000 + 4096
    events = _make_bsel_trace(0.99, 64, budget, 100_000, 0, [[0, 1, 2], [3]])
    real_victim = events[1]["evicted"][0]
    # Claim a DIFFERENT resident object was evicted instead of the real
    # lowest-(score,last_ref,key) victim.
    other = next(r for r in events[0]["resident_after"]
                 if (r["layer"], r["expert"]) != (real_victim["layer"], real_victim["expert"]))
    events[1]["evicted"] = [{"layer": other["layer"], "expert": other["expert"]}]
    # Keep resident_after self-consistent with the claimed (wrong) eviction
    # so only "evicted" itself disagrees with the real replay.
    events[1]["resident_after"] = [
        r for r in events[0]["resident_after"]
        if (r["layer"], r["expert"]) != (other["layer"], other["expert"])
    ] + [{"layer": 0, "expert": 3, "score": 1.0, "last_ref": 999}]
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert not ok
    assert mismatch["step"] == 1
    assert mismatch["field"] in ("evicted", "resident_after")


# ---------------------------------------------------------------------------
# 4. Protected set
# ---------------------------------------------------------------------------

def test_protected_set_strict_for_bsel_mismatch_detected():
    # b_selected has no protected-set concept at all: Python always reports
    # []. A trace that falsely claims a non-empty protected_before for
    # b_selected is a genuine bug, not a known API gap, and must be caught.
    events = _make_bsel_trace(0.99, 64, 1_000_000, 100_000, 0, [[0, 1], [2]])
    events[1]["protected_before"] = [0]
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert not ok
    assert mismatch["step"] == 1
    assert mismatch["field"] == "protected_before"


def test_protected_set_accessor_closes_gap_for_predicted_protect():
    # RetentionPolicy::protected_for() (additive, read-only accessor on the
    # frozen module) now lets the real runtime report the TRUE protected
    # set for predicted_protect -- no more [] placeholder, no more
    # tolerated gap. A faithfully-instrumented trace now matches the real
    # Python reference's protected set exactly, event by event, including
    # the genuine non-empty sets after the first event on a layer, and the
    # summary carries no protected_before_gap_* keys at all (the field is
    # a normal, strictly-compared field now, like any other).
    events = _make_pp_trace(0.99, 64, 6 * 3_000_000 + 4096, 3_000_000, 0,
                             [[0, 1, 2], [0, 1], [0, 1], [3, 4], [0, 1], [5, 6], [0, 1]])
    assert any(e["protected_before"] for e in events), (
        "fixture assumption wrong: expected a genuine non-empty protected set "
        "to appear after the first event -- fix the fixture, not this test"
    )
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert ok, mismatch
    assert "protected_before_gap_events" not in summary
    assert "protected_before_gap_note" not in summary
    assert "protected_before_gap_sample" not in summary


def test_protected_set_mismatch_for_predicted_protect_now_detected():
    # Before the accessor, a wrong protected_before for predicted_protect
    # was invisible to the comparator (always [] on both the real and
    # tolerated sides). Now that the accessor reports the real set, a wrong
    # protected_before must be caught like any other field -- this is
    # exactly the behavior upgrade this phase delivers.
    events = _make_pp_trace(0.99, 64, 6 * 3_000_000 + 4096, 3_000_000, 0,
                             [[0, 1, 2], [0, 1], [0, 1], [3, 4], [0, 1], [5, 6], [0, 1]])
    step_with_protected = next(i for i, e in enumerate(events) if e["protected_before"])
    events[step_with_protected]["protected_before"] = [123456]
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert not ok
    assert mismatch["step"] == step_with_protected
    assert mismatch["field"] == "protected_before"
    assert mismatch["real_runtime_value"] == [123456]


def test_protected_set_strict_check_does_not_mask_a_real_decision_bug():
    # protected_before is now compared strictly and first in the checks
    # list for predicted_protect too. A genuinely wrong eviction decision
    # must still be caught on its OWN field (evicted/resident_after), not
    # just incidentally via a protected_before disagreement -- i.e. the new
    # strict protected_before check must not become a different kind of
    # blind spot that stops the comparison before reaching eviction fields
    # in a scenario where protected_before happens to still agree.
    events = _make_pp_trace(0.99, 64, 6 * 3_000_000 + 4096, 3_000_000, 0,
                             [[0, 1, 2], [0, 1], [0, 1], [3, 4], [0, 1], [5, 6], [0, 1]])
    victim_step = next(i for i, e in enumerate(events) if e["evicted"])
    real_victim = events[victim_step]["evicted"][0]
    other = next(r for r in events[victim_step]["resident_after"]
                 if (r["layer"], r["expert"]) != (real_victim["layer"], real_victim["expert"]))
    events[victim_step]["evicted"] = [{"layer": other["layer"], "expert": other["expert"]}]
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert not ok
    assert mismatch["step"] == victim_step
    assert mismatch["field"] in ("evicted", "resident_after")


# ---------------------------------------------------------------------------
# 5. Tie-breaking
# ---------------------------------------------------------------------------

def test_tie_breaking_deterministic_victim_real_replay():
    # Three same-score, same-last_ref objects admitted in the SAME event
    # (so score=1.0, last_ref identical for all three); AgedFrequency's own
    # frozen tie-break is (score, last_ref, stable_obj_key) ascending, so
    # the lowest (layer, expert) -- here (0, 1) -- must be the victim when
    # the budget forces exactly one eviction next.
    budget = 3 * 100_000 + 4096
    events = _make_bsel_trace(0.99, 64, budget, 100_000, 0, [[1, 2, 5], [9]])
    assert events[1]["evicted"] == [{"layer": 0, "expert": 1}], (
        "fixture assumption about the tie-break winner is wrong; fix the fixture, "
        "not the assertion below"
    )
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert ok, mismatch


def test_tie_breaking_wrong_tiebreak_winner_detected():
    budget = 3 * 100_000 + 4096
    events = _make_bsel_trace(0.99, 64, budget, 100_000, 0, [[1, 2, 5], [9]])
    # Falsely claim expert 2 (not the real tie-break winner, expert 1) was
    # evicted instead.
    events[1]["evicted"] = [{"layer": 0, "expert": 2}]
    events[1]["resident_after"] = [
        {"layer": 0, "expert": 1, "score": 1.0, "last_ref": 3},
        {"layer": 0, "expert": 5, "score": 1.0, "last_ref": 3},
        {"layer": 0, "expert": 9, "score": 1.0, "last_ref": 4},
    ]
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert not ok
    assert mismatch["field"] in ("evicted", "resident_after")


# ---------------------------------------------------------------------------
# 6. Mismatch detection (stops at the FIRST divergence, reports full state)
# ---------------------------------------------------------------------------

def test_mismatch_detection_stops_at_first_divergence_not_last():
    budget = 3 * 100_000 + 4096
    events = _make_bsel_trace(0.99, 64, budget, 100_000, 0, [[0, 1, 2], [3], [4]])
    # Corrupt step 1 AND step 2; the comparator must report step 1, never
    # step 2, and never both.
    events[1]["miss_experts"] = []
    events[2]["miss_experts"] = []
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert not ok
    assert mismatch["step"] == 1
    assert "resident_before" in mismatch
    assert "python_value" in mismatch and "real_runtime_value" in mismatch


# ---------------------------------------------------------------------------
# 7. Parameter / provenance inconsistency
# ---------------------------------------------------------------------------

def test_provenance_policy_id_change_mid_trace_rejected():
    events = _make_bsel_trace(0.99, 64, 1_000_000, 100_000, 0, [[0], [1]])
    events[1]["policy"] = "predicted_protect"
    with pytest.raises(rtc.ProvenanceMismatchError, match="policy="):
        rtc.compare(rtc.parse_trace(_lines(events)))


def test_provenance_params_change_mid_trace_rejected():
    events = _make_bsel_trace(0.99, 64, 1_000_000, 100_000, 0, [[0], [1]])
    events[1]["policy_params"]["decay_factor"] = 0.5
    with pytest.raises(rtc.ProvenanceMismatchError, match="policy_params="):
        rtc.compare(rtc.parse_trace(_lines(events)))


def test_provenance_budget_change_mid_trace_rejected():
    events = _make_bsel_trace(0.99, 64, 1_000_000, 100_000, 0, [[0], [1]])
    events[1]["budget_bytes"] = 2_000_000
    with pytest.raises(rtc.ProvenanceMismatchError, match="budget_bytes="):
        rtc.compare(rtc.parse_trace(_lines(events)))


def test_provenance_predictor_decay_disagrees_with_frozen_constant_rejected():
    # The frozen predictor decay is a hardcoded constant (0.99); a trace
    # that reports a different value for predicted_protect is reporting an
    # impossible configuration and must be blocked, not silently accepted.
    events = _make_pp_trace(0.99, 64, 6 * 3_000_000 + 4096, 3_000_000, 0, [[0, 1]])
    events[0]["policy_params"]["predictor_decay"] = 0.5
    with pytest.raises(rtc.ProvenanceMismatchError, match="predictor_decay"):
        rtc.compare(rtc.parse_trace(_lines(events)))


# ---------------------------------------------------------------------------
# 8. Causality / future-alteration
# ---------------------------------------------------------------------------

def test_causality_comparator_recomputes_rather_than_trusting_trace_state():
    """The comparator must derive resident/protected state itself by
    replaying step by step through the real frozen policy, never by
    trusting the trace's own self-reported `resident_before`/
    `protected_before` as ground truth. Proof: build a trace whose
    DECISION fields (evicted/resident_after) are wrong, but whose
    `resident_before` is forged to look internally consistent with that
    wrong decision (as if a future/altered state had leaked backward).
    The comparator must still catch the wrong decision, because it only
    ever reads `resident_before`/`protected_before` from the trace for
    DISPLAY in a mismatch report -- the actual comparison is against
    freshly recomputed Python state, never against the trace's own claims
    about itself."""
    budget = 3 * 100_000 + 4096
    events = _make_bsel_trace(0.99, 64, budget, 100_000, 0, [[0, 1, 2], [3]])
    real_victim = events[1]["evicted"][0]
    forged_victim = next(r for r in events[0]["resident_after"]
                          if (r["layer"], r["expert"]) != (real_victim["layer"], real_victim["expert"]))
    # Forge resident_before to already exclude the forged victim, as if the
    # (wrong, future) eviction had already causally happened -- internally
    # self-consistent, but not what the real engine actually did.
    events[1]["resident_before"] = [
        r for r in events[0]["resident_after"]
        if (r["layer"], r["expert"]) != (forged_victim["layer"], forged_victim["expert"])
    ]
    events[1]["evicted"] = [{"layer": forged_victim["layer"], "expert": forged_victim["expert"]}]
    events[1]["resident_after"] = [
        r for r in events[1]["resident_before"]
    ] + [{"layer": 0, "expert": 3, "score": 1.0, "last_ref": 999}]
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert not ok, (
        "comparator was fooled by a self-consistent-looking but causally wrong trace; "
        "it must recompute state independently, not trust resident_before"
    )
    assert mismatch["step"] == 1


def test_causality_future_experts_cannot_be_protected_before_first_observation():
    # Cold start: before any event on a layer has ever been observed, a
    # predicted_protect policy's predicted-next set for that layer must be
    # empty (there is no history yet to predict from) -- it can never
    # "know" about experts requested only in a LATER event. This is a
    # property of the real frozen predictor itself, exercised here (not
    # reimplemented) via the same ground-truth generator every other test
    # uses.
    events = _make_pp_trace(0.99, 64, 6 * 3_000_000 + 4096, 3_000_000, 0, [[7, 8], [9]])
    assert events[0]["step"] == 0
    # Nothing in event 0's own trace can reference layer 0's future (step 1)
    # experts; this is simply asserting the generator/fixture never leaks
    # future data into the first event, which the comparator then verifies
    # transitively by matching every field via real replay.
    assert 9 not in events[0]["requested_experts"]
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert ok, mismatch


# ---------------------------------------------------------------------------
# Sanity controls: a faithfully-built multi-event trace for EACH policy
# family must report a full MATCH end to end.
# ---------------------------------------------------------------------------

def test_full_match_real_replay_b_selected():
    budget = 6 * 100_000 + 4096
    events = _make_bsel_trace(0.99, 64, budget, 100_000, 0,
                               [[0, 1, 2], [0, 1], [3, 4], [0, 1], [5, 6], [0, 1]])
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert ok, mismatch
    assert summary["policy_id"] == "b_selected"


def test_full_match_real_replay_predicted_protect():
    budget = 6 * 3_000_000 + 4096
    events = _make_pp_trace(0.99, 64, budget, 3_000_000, 0,
                             [[0, 1, 2], [0, 1], [0, 1], [3, 4], [0, 1], [5, 6], [0, 1]])
    ok, mismatch, summary = rtc.compare(rtc.parse_trace(_lines(events)))
    assert ok, mismatch
    assert summary["policy_id"] == "predicted_protect"
    assert "protected_before_gap_events" not in summary


# ---------------------------------------------------------------------------
# CLI entry point (main()) smoke test
# ---------------------------------------------------------------------------

def test_cli_main_exits_1_on_mismatch(tmp_path, monkeypatch, capsys):
    events = _make_bsel_trace(0.99, 64, 1_000_000, 100_000, 0, [[0, 1], [2]])
    events[1]["miss_experts"] = []
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("".join(_lines(events)))
    monkeypatch.setattr(sys, "argv", ["r2_12_real_trace_conformance.py", str(trace_path)])
    with pytest.raises(SystemExit) as exc_info:
        rtc.main()
    assert exc_info.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "MISMATCH"


def test_cli_main_exits_0_on_match(tmp_path, monkeypatch, capsys):
    events = _make_bsel_trace(0.99, 64, 1_000_000, 100_000, 0, [[0, 1], [2]])
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("".join(_lines(events)))
    monkeypatch.setattr(sys, "argv", ["r2_12_real_trace_conformance.py", str(trace_path)])
    with pytest.raises(SystemExit) as exc_info:
        rtc.main()
    assert exc_info.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "MATCH"
