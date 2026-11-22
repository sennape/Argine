import json, sys
from fractions import Fraction
from pathlib import Path
import pytest
H = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(H))
import r3

def _ev(n=60):
    import random; rnd = random.Random(1); ev = []
    for i in range(n):
        ev.append({"i": i, "pass": i // 3, "layer": i % 3, "bytes": 100, "req": sorted(rnd.sample(range(12), 4))})
    return ev

def test_ces_no_future_attrs():
    p = r3.make_ces()
    assert not hasattr(p, "_predicted_next") and not hasattr(p, "_history") and not hasattr(p, "predict")

def test_ces_protects_only_current_event():
    ev = _ev(); p = r3.make_ces(); p.start(__import__("argine.research.policies", fromlist=["Context"]).Context(layers=[0,1,2], n_experts=12, size={0:100,1:100,2:100}, budget=500, top_k=4))
    for e in ev:
        p.access(e["i"], e["layer"], e["req"])
        assert p._cur == frozenset()           # cleared after the event
        assert p.resident_bytes <= 500

def test_ces_deterministic():
    ev = _ev(); a = r3.replay(ev, r3.make_ces(), budget=500, top_k=4, n_experts=12); b = r3.replay(ev, r3.make_ces(), budget=500, top_k=4, n_experts=12)
    assert a == b

def test_refresh_schedule():
    for n in r3.N_GRID:
        assert r3.refresh_count_expected(n) == 48 * len(range(0, 514, n))
    assert [r3.refresh_count_expected(n) for n in r3.N_GRID] == [24672, 12336, 6192, 2496, 1248, 528, 288]

def test_pp_n1_equals_n1_refresh_every_event():
    ev = _ev(); r = r3.replay(ev, r3.make_pp_n(1), budget=500, top_k=4, n_experts=12)
    assert r["predictor_refresh_count"] == len(ev)

def test_pp_refresh_count_small():
    ev = _ev(60); r = r3.replay(ev, r3.make_pp_n(4), budget=500, top_k=4, n_experts=12)
    assert r["predictor_refresh_count"] == 3 * len(range(0, 20, 4))

@pytest.mark.parametrize("c,exp", [(79, "R3_05_K2_PP_HAS_ADDITIONAL_EFFECT"), (80, "R3_05_K2_RETIRE_CURRENT_PP"), (81, "R3_05_K2_RETIRE_CURRENT_PP")])
def test_k2(c, exp): assert r3.k2_decide(c, 100)["classification"] == exp

@pytest.mark.parametrize("c,exp", [(19, "R3_05_K3_FREQUENCY_FAMILY_SURVIVES"), (20, "R3_05_K3_FREQUENCY_FAMILY_DROP"), (21, "R3_05_K3_FREQUENCY_FAMILY_SURVIVES")])
def test_k3(c, exp):
    exp = {19: "R3_05_K3_FREQUENCY_FAMILY_DROP", 20: "R3_05_K3_FREQUENCY_FAMILY_DROP", 21: "R3_05_K3_FREQUENCY_FAMILY_SURVIVES"}[c]
    assert r3.k3_decide(c, 100)["classification"] == exp

def test_retained_fraction(): assert r3.k3_decide(1, 4)["retained"] == 0.25

def test_budget_enforced():
    r = r3.replay(_ev(), r3.make_ces(), budget=500, top_k=4, n_experts=12); assert r["budget_violations"] == 0

def test_wrong_stream_rejected(tmp_path):
    p = tmp_path / "s.jsonl"; p.write_text('{"i":0}\n')
    with pytest.raises(r3.R3Error): r3.load_events(p)

def test_evidence_verifies_and_tamper():
    assert r3.verify() == []
    f = H / "K2_DECISION.json"; orig = f.read_bytes()
    try:
        f.write_bytes(orig.replace(b"RETIRE", b"RETIRX", 1)); assert r3.verify() != []
    finally: f.write_bytes(orig)
