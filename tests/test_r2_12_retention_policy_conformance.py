"""R2-12 runtime-policy realization/conformance-phase tests.

Verifies the new C++ retention-policy module
(patches/argine-retention-policy.{h,cpp}) that makes the two frozen R2-12
conditions -- A = B_selected(M) (aged_frequency) and B = PredictedProtectPolicy
(decayed_transition, decay=0.99) -- genuinely selectable in the real runtime's
eviction mechanism, where before this phase they were not (see
experiments/R2-12-runtime-validation/R2_12_RUNTIME_BLOCKED_CAPABILITY.json).

Everything here runs against SYNTHETIC traces only: no real model, no real
llama-server, no .psxs store, no R2-12 counted run, no warmup, no tokens/s
measurement. The C++ side is built fresh into a temp directory with g++ (the
same standalone, llama.cpp-independent build already used for
argine-store-test.cpp) and exercised as a subprocess; the Python side calls
the ACTUAL frozen reference classes directly (AgedFrequency,
PredictedProtectPolicy), never a reimplementation.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "patches"
TOOLS = ROOT / "tools"

sys.path.insert(0, str(ROOT))

from argine.research.policies import Context  # noqa: E402
from argine.research2.r2_09a.policies import AgedFrequency  # noqa: E402
from argine.research2.r2_bridge01.retention_policies import PredictedProtectPolicy  # noqa: E402

# Frozen per-budget B_selected(M) pairs, copied verbatim (same source
# r2_bridge01/retention_policies.py already copies from) -- used to drive
# the conformance checks over all three real R2-12 budgets, never invented.
FROZEN_B_SELECTED_PAIRS = {
    1610612736: (0.999, 1),
    4294967296: (0.99, 64),
    8589934592: (0.999, 16),
}
FROZEN_PREDICTOR_DECAY = 0.99


def _have_gxx() -> bool:
    return shutil.which("g++") is not None


@pytest.fixture(scope="module")
def cpp_binary(tmp_path_factory):
    if not _have_gxx():
        pytest.skip("g++ not available in this environment; cannot build the "
                     "standalone C++ conformance harness")
    build_dir = tmp_path_factory.mktemp("retention_cpp_build")
    out = build_dir / "retention_conformance"
    cmd = [
        "g++", "-O2", "-std=c++17", "-Wall", "-Wextra",
        str(PATCHES / "argine-retention-policy.cpp"),
        str(PATCHES / "argine-retention-conformance-main.cpp"),
        "-o", str(out),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"C++ build failed:\n{res.stdout}\n{res.stderr}"
    return out


def _run_cpp(binary: Path, policy_id, decay, interval, budget_bytes,
             pred_decay=None, top_k=None, n_experts=None, trace_lines=()):
    args = [str(binary), policy_id, str(decay), str(interval)]
    if policy_id == "predicted_protect":
        args += [str(pred_decay), str(top_k), str(n_experts)]
    args += [str(budget_bytes)]
    res = subprocess.run(args, input="\n".join(trace_lines) + "\n",
                          capture_output=True, text=True)
    return res


def _run_python_ref(policy_id, decay, interval, budget_bytes, events,
                     n_experts=None, top_k=None):
    layers = sorted({e["layer"] for e in events})
    sizes = {e["layer"]: e["bytes_of_layer"] for e in events}
    ctx = Context(
        layers=layers,
        n_experts=(n_experts if n_experts else (max(max(e["experts"]) for e in events) + 1)),
        size=sizes,
        budget=budget_bytes,
        top_k=(top_k or 0),
    )
    if policy_id == "b_selected":
        policy = AgedFrequency(decay, interval)
    else:
        policy = PredictedProtectPolicy(decay, interval)
    policy.start(ctx)

    # See tools/r2_12_retention_conformance_ref.py for why this must hook
    # the real _evict_one call rather than diff resident-set snapshots: the
    # frozen policy core can legally evict and re-admit the SAME
    # (layer, expert) object within one event, which a before/after diff
    # cannot see.
    current_event_evictions = []
    if policy_id == "b_selected":
        def patched_evict_one(self):
            victim = self._choose_victim()
            if victim is None:
                return False
            sz = self.resident.pop(victim)
            self.scores.pop(victim, None)
            self.last_ref.pop(victim, None)
            self.resident_bytes -= sz
            self.evictions += 1
            current_event_evictions.append(list(victim))
            return True
    else:
        def patched_evict_one(self, layer):
            victim = self._choose_victim(layer)
            if victim is None:
                return False
            sz = self.resident.pop(victim)
            self.scores.pop(victim, None)
            self.last_ref.pop(victim, None)
            self.resident_bytes -= sz
            self.evictions += 1
            current_event_evictions.append(list(victim))
            return True
    policy._evict_one = patched_evict_one.__get__(policy, type(policy))

    decisions = []
    for ev in events:
        current_event_evictions.clear()
        missed = policy.access(ev["step"], ev["layer"], ev["experts"])
        evicted = list(current_event_evictions)
        decisions.append({
            "step": ev["step"],
            "layer": ev["layer"],
            "missed": sorted(missed),
            "evicted": evicted,
            "resident": sorted([list(o) for o in policy.resident.keys()]),
            "resident_bytes": policy.resident_bytes,
        })
    return decisions, policy


def _trace_lines(events):
    return [json.dumps({"step": e["step"], "layer": e["layer"],
                         "experts": e["experts"], "bytes_of_layer": e["bytes_of_layer"]})
            for e in events]


def _parse_cpp_decisions(stdout: str):
    return [json.loads(line) for line in stdout.strip().splitlines() if line.strip()]


def _random_trace(seed, n_events, n_layers, n_experts, max_k, bytes_per_layer):
    rng = random.Random(seed)
    events = []
    for step in range(n_events):
        layer = rng.randrange(n_layers)
        k = rng.randint(1, max_k)
        experts = sorted(rng.sample(range(n_experts), k))
        events.append({"step": step, "layer": layer, "experts": experts,
                        "bytes_of_layer": bytes_per_layer})
    return events


# ---------------------------------------------------------------------------
# Cross-language conformance: b_selected, all three frozen budgets' params
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("budget_bytes,pair", sorted(FROZEN_B_SELECTED_PAIRS.items()))
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_b_selected_cross_language_conformance(cpp_binary, budget_bytes, pair, seed):
    decay, interval = pair
    # Scale the synthetic budget down from the real GiB values (which would
    # need gigabytes of fake experts to stress eviction) while keeping the
    # SAME decay/interval scientific parameters -- only the toy byte budget
    # and object count are synthetic, never the frozen (decay, interval).
    toy_budget = 500
    events = _random_trace(seed=seed * 1000 + budget_bytes, n_events=60,
                            n_layers=3, n_experts=12, max_k=4, bytes_per_layer=50)
    trace = _trace_lines(events)

    cpp_res = _run_cpp(cpp_binary, "b_selected", decay, interval, toy_budget,
                        trace_lines=trace)
    assert cpp_res.returncode == 0, cpp_res.stderr
    cpp_decisions = _parse_cpp_decisions(cpp_res.stdout)

    py_decisions, policy = _run_python_ref("b_selected", decay, interval, toy_budget, events)

    assert len(cpp_decisions) == len(py_decisions) == len(events)
    for i, (c, p) in enumerate(zip(cpp_decisions, py_decisions)):
        assert c["missed"] == p["missed"], f"event {i}: missed mismatch {c} vs {p}"
        assert c["evicted"] == p["evicted"], f"event {i}: evicted mismatch {c} vs {p}"
        assert c["resident"] == p["resident"], f"event {i}: resident mismatch {c} vs {p}"
        assert c["resident_bytes"] == p["resident_bytes"], f"event {i}: resident_bytes mismatch"
        # memory correctness: never over budget
        assert c["resident_bytes"] <= toy_budget


@pytest.mark.parametrize("budget_bytes,pair", sorted(FROZEN_B_SELECTED_PAIRS.items()))
@pytest.mark.parametrize("seed", [10, 11, 12])
def test_predicted_protect_cross_language_conformance(cpp_binary, budget_bytes, pair, seed):
    decay, interval = pair
    toy_budget = 500
    n_experts = 12
    top_k = 4
    events = _random_trace(seed=seed * 1000 + budget_bytes, n_events=60,
                            n_layers=3, n_experts=n_experts, max_k=4, bytes_per_layer=50)
    trace = _trace_lines(events)

    cpp_res = _run_cpp(cpp_binary, "predicted_protect", decay, interval, toy_budget,
                        pred_decay=FROZEN_PREDICTOR_DECAY, top_k=top_k, n_experts=n_experts,
                        trace_lines=trace)
    assert cpp_res.returncode == 0, cpp_res.stderr
    cpp_decisions = _parse_cpp_decisions(cpp_res.stdout)

    py_decisions, policy = _run_python_ref("predicted_protect", decay, interval, toy_budget,
                                            events, n_experts=n_experts, top_k=top_k)

    assert len(cpp_decisions) == len(py_decisions) == len(events)
    for i, (c, p) in enumerate(zip(cpp_decisions, py_decisions)):
        assert c["missed"] == p["missed"], f"event {i}: missed mismatch {c} vs {p}"
        assert c["evicted"] == p["evicted"], f"event {i}: evicted mismatch {c} vs {p}"
        assert c["resident"] == p["resident"], f"event {i}: resident mismatch {c} vs {p}"
        assert c["resident_bytes"] == p["resident_bytes"]
        assert c["resident_bytes"] <= toy_budget


# ---------------------------------------------------------------------------
# Causality: mutating the trace strictly AFTER step t must never change the
# decision recorded AT step t (section 6 of the handoff).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("policy_id", ["b_selected", "predicted_protect"])
def test_causality_future_mutation_does_not_change_past_decisions(cpp_binary, policy_id):
    toy_budget = 500
    n_experts = 12
    top_k = 4
    decay, interval = FROZEN_B_SELECTED_PAIRS[4294967296]  # 4 GiB pair, arbitrary valid choice

    base_events = _random_trace(seed=777, n_events=40, n_layers=3, n_experts=n_experts,
                                 max_k=4, bytes_per_layer=50)
    cut = 20  # decisions at steps [0, cut) must be identical regardless of what follows

    def decide(events):
        trace = _trace_lines(events)
        kwargs = {}
        if policy_id == "predicted_protect":
            kwargs = dict(pred_decay=FROZEN_PREDICTOR_DECAY, top_k=top_k, n_experts=n_experts)
        res = _run_cpp(cpp_binary, policy_id, decay, interval, toy_budget,
                        trace_lines=trace, **kwargs)
        assert res.returncode == 0, res.stderr
        return _parse_cpp_decisions(res.stdout)[:cut]

    decisions_a = decide(base_events)

    # Mutate everything from `cut` onward with a different random trace.
    rng = random.Random(999)
    mutated_tail = _random_trace(seed=999, n_events=len(base_events) - cut, n_layers=3,
                                  n_experts=n_experts, max_k=4, bytes_per_layer=50)
    for i, ev in enumerate(mutated_tail):
        ev["step"] = cut + i
    mutated_events = base_events[:cut] + mutated_tail

    decisions_b = decide(mutated_events)

    assert decisions_a == decisions_b, (
        "a decision at or before the cut point changed when only the FUTURE "
        "(post-cut) trace was mutated -- this is a causal-boundary violation"
    )


# ---------------------------------------------------------------------------
# Fail-closed: unknown policy id / out-of-grid parameters must be REJECTED,
# never silently substituted or defaulted.
# ---------------------------------------------------------------------------

def test_unknown_policy_id_is_rejected(cpp_binary):
    res = _run_cpp(cpp_binary, "totally_not_a_real_policy", 0.99, 64, 300, trace_lines=["{}"])
    assert res.returncode == 3
    assert "REJECTED" in res.stderr
    assert "unrecognized" in res.stderr


def test_out_of_frozen_grid_b_selected_pair_is_rejected(cpp_binary):
    # A plausible-looking but NOT frozen pair.
    res = _run_cpp(cpp_binary, "b_selected", 0.95, 32, 300, trace_lines=["{}"])
    assert res.returncode == 3
    assert "REJECTED" in res.stderr
    assert "not one of the three frozen" in res.stderr


def test_predicted_protect_predictor_decay_must_be_frozen_value(cpp_binary):
    # 0.9 is in the general R2-10 grid but is NOT R2-12's frozen 0.99.
    res = _run_cpp(cpp_binary, "predicted_protect", 0.99, 64, 300,
                    pred_decay=0.9, top_k=4, n_experts=12, trace_lines=["{}"])
    assert res.returncode == 3
    assert "not R2-12's frozen value" in res.stderr


def test_predicted_protect_missing_topk_or_nexperts_is_rejected(cpp_binary):
    res = _run_cpp(cpp_binary, "predicted_protect", 0.99, 64, 300,
                    pred_decay=0.99, top_k=0, n_experts=12, trace_lines=["{}"])
    assert res.returncode == 3
    assert "REJECTED" in res.stderr


# ---------------------------------------------------------------------------
# Tie-breaking: equal scores must break by last_ref, then stable (layer,expert)
# ---------------------------------------------------------------------------

def test_tie_break_is_last_ref_then_layer_expert(cpp_binary):
    # Three objects admitted with identical scores (all score=1 after their
    # own admission); the budget forces eviction of exactly the least
    # recently referenced among ties, then stable (layer, expert) ascending.
    events = [
        {"step": 0, "layer": 0, "experts": [5], "bytes_of_layer": 100},
        {"step": 1, "layer": 0, "experts": [3], "bytes_of_layer": 100},
        {"step": 2, "layer": 0, "experts": [9], "bytes_of_layer": 100},
        # Budget=300 holds exactly 3; a 4th forces eviction of the oldest
        # (last_ref) among the three equal-score residents: expert 5.
        {"step": 3, "layer": 0, "experts": [1], "bytes_of_layer": 100},
    ]
    res = _run_cpp(cpp_binary, "b_selected", 0.99, 64, 300, trace_lines=_trace_lines(events))
    assert res.returncode == 0, res.stderr
    decisions = _parse_cpp_decisions(res.stdout)
    assert decisions[-1]["evicted"] == [[0, 5]]

    py_decisions, _ = _run_python_ref("b_selected", 0.99, 64, 300, events)
    assert py_decisions[-1]["evicted"] == [[0, 5]]


# ---------------------------------------------------------------------------
# Memory correctness across a longer randomized stress trace
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("policy_id", ["b_selected", "predicted_protect"])
def test_memory_correctness_never_exceeds_budget(cpp_binary, policy_id):
    toy_budget = 437  # deliberately not a clean multiple of bytes_per_layer
    n_experts = 16
    top_k = 4
    decay, interval = FROZEN_B_SELECTED_PAIRS[1610612736]
    events = _random_trace(seed=55, n_events=200, n_layers=4, n_experts=n_experts,
                            max_k=5, bytes_per_layer=37)
    kwargs = {}
    if policy_id == "predicted_protect":
        kwargs = dict(pred_decay=FROZEN_PREDICTOR_DECAY, top_k=top_k, n_experts=n_experts)
    res = _run_cpp(cpp_binary, policy_id, decay, interval, toy_budget,
                    trace_lines=_trace_lines(events), **kwargs)
    assert res.returncode == 0, res.stderr
    decisions = _parse_cpp_decisions(res.stdout)
    for d in decisions:
        assert d["resident_bytes"] <= toy_budget, f"budget violated at step {d['step']}"
        # no invalid/duplicate resident objects
        seen = set()
        for obj in d["resident"]:
            key = tuple(obj)
            assert key not in seen, f"duplicate resident object {obj} at step {d['step']}"
            seen.add(key)
            assert 0 <= obj[1] < n_experts, f"invalid expert id {obj} at step {d['step']}"
