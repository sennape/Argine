import sys, json
from pathlib import Path
import pytest
H = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(H))
import r4

def test_synthetic_all_pass(): assert r4.synthetic_cases()["all_pass"]
def test_freezes_verify(): assert r4.verify_inputs()["canonical_stream_sha256"] == r4.STREAM_SHA
def _ev():
    import random; r = random.Random(3); return [(i % 3, sorted(r.sample(range(1, 8), 3))) for i in range(80)]
def test_null_equals_ces():
    a, b = r4._run(r4.make_nue("NULL", "LEX"), 5, _ev(), 3), r4._run(r4.make_arm("CES"), 5, _ev(), 3)
    assert a[0] == b[0] and a[1] == b[1]
def test_deterministic_and_budget():
    x = r4._run(r4.make_nue("X", "LEX"), 5, _ev(), 3); y = r4._run(r4.make_nue("X", "LEX"), 5, _ev(), 3)
    assert x[0] == y[0] and x[2].resident_bytes <= 5
def test_next_use_not_current_use():
    log, m, p = r4._run(r4.make_nue("LO", "LEX"), 3, r4.C2, 1); assert log == [(1, 6)]      # victim chosen by next-use set, not by lowest score (A picks (1,5))
def test_lo_semantics():
    p = r4.make_nue("LO", "LEX"); r4._run(p, 5, [(0, [1, 2]), (0, [3])]); assert p._predicted_next[0] == frozenset({3})
@pytest.mark.parametrize("b,exp", [(0, "K4_FAIL_INFEASIBLE")])
def test_k4_below(b, exp): assert r4.k4_decide(b)["classification"] == exp
def test_k4_threshold():
    k = r4.kappa_low(); need = int(10 / k * 1e6 * r4.N_DECODE)
    assert r4.k4_decide(need - 10**7)["classification"] == "K4_FAIL_INFEASIBLE" and r4.k4_decide(need + 10**7)["classification"] == "K4_PASS"
    assert abs(r4.value_ms(need) - 10) < 1e-3
def test_k4_exactly_10(monkeypatch):
    monkeypatch.setattr(r4, "value_ms", lambda b: 10.0); assert r4.k4_decide(1)["classification"] == "K4_PASS"
@pytest.mark.parametrize("lo,exp", [(79, "R3_05_K5_NUE_LO_NOT_SUFFICIENT"), (80, "R3_05_K5_PREFER_NUE_LO"), (81, "R3_05_K5_PREFER_NUE_LO")])
def test_k5(lo, exp): assert r4.k5_decide(lo, 100)["classification"] == exp
def test_overall():
    assert r4.overall("K4_FAIL_INFEASIBLE", "K4_FAIL_INFEASIBLE", {"NUE_LO": True, "NUE_X": True}) == "R3_05_PREDICTIVE_RETENTION_KILLED"
    assert r4.overall("K4_FAIL_INFEASIBLE", "K4_PASS", {"NUE_LO": True, "NUE_X": True}) == "R3_05_NUE_MECHANISM_PASS"
    assert r4.overall("K4_FAIL_INFEASIBLE", "K4_PASS", {"NUE_LO": True, "NUE_X": False}) == "R3_05_PREDICTIVE_RETENTION_KILLED"
def test_selection_dev_only_and_tiebreak():
    z = {"decode": {"miss_bytes": 100}}; w = {"decode": {"miss_bytes": 90}}
    runs = {i: {"A": z, "NUE_X:LEX": w, "NUE_X:SOFT1": w, "NUE_X:SOFT4": {"decode": {"miss_bytes": 80}}} for i in r4.DEV_IDS}
    s = r4.dev_select(runs); assert s["winner"] == "SOFT4" and not s["tie_break_used"]
    runs = {i: {"A": z, "NUE_X:LEX": w, "NUE_X:SOFT1": w, "NUE_X:SOFT4": w} for i in r4.DEV_IDS}
    assert r4.dev_select(runs)["winner"] == "LEX"
def test_eval_refused_without_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(r4, "HERE", tmp_path)
    with pytest.raises(r4.R4Error): r4.evalrun("NUE_X")
def test_wrong_stream_rejected(tmp_path):
    p = tmp_path / "s.jsonl"; p.write_text('{"i":0}\n')
    with pytest.raises(Exception): r4._r3().load_events(p)
def test_evidence_and_tamper():
    assert r4.verify() == []
    f = H / "K4_DECISION.json"; o = f.read_bytes()
    try:
        f.write_bytes(o.replace(b"K4_FAIL", b"K4_PASS", 1)); assert r4.verify() != []
    finally: f.write_bytes(o)
