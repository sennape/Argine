"""Phase 2 tests. Synthetic/identity tests always run; result-dependent tests skip until EVAL has been scored. No test reads EVAL trace content."""
from __future__ import annotations

import json, subprocess, sys
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import phase2 as P  # noqa: E402
import runner as R  # noqa: E402
import engine as G  # noqa: E402

REPO = P.REPO; P1 = P.P1
def jl(p): return json.loads(Path(p).read_text(encoding="utf-8"))
def scored(): return (HERE / "EVAL_RESULTS.json").exists()
F = Fraction


def es_from(events, budget=10, size=1):
    return G.EventStream("syn", [e[0] for e in events], [e[1] for e in events], [tuple(sorted(e[2])) for e in events], [True] * len(events), {l: size for l in range(48)}, budget, 8, 128, max(e[0] for e in events) + 1)


def rnd(seed, n=500, pool=14):
    rng = np.random.default_rng(seed)
    return [(j // 4, int(rng.integers(0, 3)), sorted(set(int(x) for x in rng.integers(0, pool, int(rng.integers(1, 5)))))) for j in range(n)]


# ---------------------------------------------------------------- frozen identities and immutability
def test_phase0_digest_unchanged():
    fz = jl(P1.P0 / "FREEZE.json"); assert fz["contract_digest"] == P1.PHASE0_DIGEST and fz["freeze_sha256"] == P1.PHASE0_FREEZE
    assert subprocess.run([sys.executable, str(P1.P0 / "phase0.py"), "verify"], capture_output=True).returncode == 0


def test_phase1_digest_unchanged():
    fz = jl(P.P1D / "FREEZE.json"); assert fz["contract_digest"] == P.PHASE1_CONTRACT_DIGEST and fz["freeze_sha256"] == P.PHASE1_FREEZE_SHA and fz["state"] == "R4_CFDC_PHASE1_VALIDATED"
    assert subprocess.run([sys.executable, str(P.P1D / "phase1.py"), "verify"], capture_output=True).returncode == 0


def test_rich_and_cheap_hashes_unchanged():
    mh = P1.model_hashes(); assert mh["rich_model_digest"] == P.RICH_DIGEST and mh["cheap_tree_hash"] == P.CHEAP_TREE_HASH
    assert P.identity_problems(P.identity()) == []


def test_research3_and_phase01_immutable():
    for rev, paths in ((P1.SOURCE_R3_HEAD, ["experiments/R3-*", "paper/research3"]), (P1.PHASE0_COMMIT, ["experiments/R4-00-causal-eviction-distillation"]), (P.PHASE1_COMMIT_C, ["experiments/R4-01-cfdc-training-validation"])):
        r = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", rev, "--", *paths], capture_output=True, text=True); assert r.stdout.strip() == ""


def test_no_post_eval_policy_change():
    imp = jl(P.P1D / "IMPLEMENTATION.json")["files_lf_sha256"]
    for f, h in imp.items(): assert P1.lf_sha(P.P1D / f) == h
    if (HERE / "EVAL_OPENED.json").exists():
        op = jl(HERE / "EVAL_OPENED.json")
        for f, h in op["scorer_code_lf_sha256"].items(): assert P1.lf_sha(HERE / f) == h
        assert op["identity"] == P.identity()


# ---------------------------------------------------------------- EVAL identity / opening
def test_eval_trace_identities_frozen_and_expected_hashes():
    ents = P.eval_entries(); assert len(ents) == 4
    assert {e["workload_id"]: e["trace_sha256"][:8] for e in ents} == {"coding-eval-v1": "46104e88", "reasoning-eval-v1": "9adc1ce8", "summarization-eval-v1": "e33fa1e2", "instruction-eval-v1": "6a51113f"}
    if (HERE / "EVAL_OPENED.json").exists():
        assert {x["workload_id"]: x["expected_trace_sha256"] for x in jl(HERE / "EVAL_OPENED.json")["eval_trace_identities"]} == P.expected_eval_hashes()


def test_eval_read_refused_without_committed_opening(tmp_path):
    if (HERE / "EVAL_OPENED.json").exists() and P.committed(P.rel("EVAL_OPENED.json")): pytest.skip("opening committed")
    with pytest.raises(P.P2Error): P.eval_guard()
    with pytest.raises(P.P2Error): P.load_eval_trace(P.eval_entries()[0])


def test_opening_record_has_no_metrics():
    if not (HERE / "EVAL_OPENED.json").exists(): pytest.skip("not opened")
    t = (HERE / "EVAL_OPENED.json").read_text(); op = jl(HERE / "EVAL_OPENED.json")
    assert op["eval_outcome_contained"] is False and op["eval_state_before"] == "LOCKED" and op["eval_state_after"] == "OPENED"
    for bad in ("miss_bytes", "\"capture\"", "\"B_P\"", "decode_misses", "avoided_vs_A"): assert bad not in t


def test_opening_commit_precedes_metrics():
    if not scored(): pytest.skip("not scored yet")
    def first(path): return subprocess.run(["git", "-C", str(REPO), "log", "--diff-filter=A", "--format=%H", "--", path], capture_output=True, text=True).stdout.split()
    op = first(P.rel("EVAL_OPENED.json")); res = first(P.rel("EVAL_RESULTS.json"))
    if res: assert subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor", op[-1], res[-1]]).returncode == 0 and op[-1] != res[-1]


# ---------------------------------------------------------------- runner equivalence (exactness of the technical wrapper)
STRIP = ("step_miss_bytes", "chunks", "seconds", "backend", "result_sha256")
def strip(r): return {k: v for k, v in r.items() if k not in STRIP}


@pytest.mark.parametrize("arm", ["A", "NUE_X", "DECAYED_COUNT", "CHEAP"])
def test_runner_matches_frozen_sim_run(arm, tmp_path):
    es = es_from(rnd(3), 10); mk = lambda: P._policy(arm)
    ref = G.Sim(es, mk()).run(); out = R.run_arm(es, mk(), None)
    assert strip(out) == strip(ref) and P.result_sha(out) == P.result_sha(ref)
    assert sum(b for _, b in out["step_miss_bytes"]) == out["decode"]["miss_bytes"]


@pytest.mark.parametrize("arm", ["DECAYED_COUNT", "CHEAP"])
def test_runner_checkpoint_resume_is_exact(arm, tmp_path):
    es = es_from(rnd(5, 600), 10); ck = str(tmp_path / "ck.pkl"); ref = G.Sim(es, P._policy(arm)).run(); n = 0; out = None
    while out is None and n < 200:
        out = R.run_arm(es, P._policy(arm), ck, 0.0); n += 1
    assert out is not None and n > 1 and strip(out) == strip(ref) and P.result_sha(out) == P.result_sha(ref)


def test_runner_belady_matches_frozen_adapter():
    es = es_from(rnd(11), 10); ref = G.run_belady(es); out = R.run_belady_steps(es)
    assert strip(out) == strip(ref) and sum(b for _, b in out["step_miss_bytes"]) == ref["decode"]["miss_bytes"]


# ---------------------------------------------------------------- endpoint and rules
def test_primary_endpoint_formula():
    rows = {"a": {"A": 100, "BELADY": 40, "P": 70}, "b": {"A": 50, "BELADY": 30, "P": 60}}
    assert P.pooled_capture(rows, "P") == F((100 - 70) + (50 - 60), (100 - 40) + (50 - 30)) == F(20, 80)
    assert P.capture(10, 10, 10) is None and P.capture(10, 5, 12) is None
    assert P.capture(100, 150, 50) == F(-1, 1)                      # uncapped
    assert P.capture(100, 0, 50) == F(2, 1)


def test_incremental_threshold_formula():
    assert P.required_capture(F(1, 10), F(1, 20)) == F(1, 4)
    assert P.required_capture(F(22, 100), F(0)) == F(25, 100)
    assert P.required_capture(F(23, 100), F(0)) == F(26, 100) and P.required_capture(F(1, 2), F(2, 5)) == F(53, 100)


def _val(cheap=F(-1, 3), rich=F(1, 6), nue=F(1, 13), dc=F(-1, 18)): return {"CHEAP": cheap, "RICH": rich, "NUE_X": nue, "DECAYED_COUNT": dc}


def test_validation_result_imported_exactly():
    v = jl(P.P1D / "VALIDATION_RESULTS.json")["aggregate_all_12"]; d = jl(P.P1D / "VALIDATION_DECISION.json")
    assert F(v["CHEAP"]["capture"]) == F(-7411084, 20561187) and abs(float(F(v["CHEAP"]["capture"])) - (-0.3604)) < 1e-4
    assert d["cheap_meets_validation_gate"] is False and v["CHEAP"]["denominator"] == 1515935195136


def test_classification_precedence():
    ok = {"w": F(1, 2)}; bad = {"w": F(-1, 10)}
    top = F(3, 10)
    assert P.classify(False, _val(F(1, 2)), _val(F(1, 2)), ok)["classification"] == "R4_CFDC_INVALID"
    assert P.classify(True, _val(F(3, 10)), {"CHEAP": F(4, 10), "RICH": 0, "NUE_X": F(1, 10), "DECAYED_COUNT": 0}, ok)["classification"] == "R4_CFDC_CHEAP_VIABLE"
    assert P.classify(True, _val(F(3, 10)), {"CHEAP": F(4, 10), "RICH": 0, "NUE_X": F(1, 10), "DECAYED_COUNT": 0}, bad)["classification"] == "R4_CFDC_HETEROGENEOUS_NOT_VIABLE"
    assert P.classify(True, _val(F(3, 10)), {"CHEAP": F(3, 10), "RICH": 0, "NUE_X": F(29, 100), "DECAYED_COUNT": 0}, ok)["classification"] == "R4_CFDC_CHEAP_NOT_INCREMENTAL"
    assert P.classify(True, _val(rich=F(3, 10)), {"CHEAP": F(1, 10), "RICH": F(3, 10), "NUE_X": 0, "DECAYED_COUNT": 0}, ok)["classification"] == "R4_CFDC_CAUSAL_INFORMATION_PRESENT_CHEAP_NOT_DEMONSTRATED"
    assert P.classify(True, _val(), {"CHEAP": F(1, 10), "RICH": F(3, 10), "NUE_X": 0, "DECAYED_COUNT": 0}, ok)["classification"] == "R4_CFDC_CAUSAL_STATE_INSUFFICIENT"   # RICH fails on VALIDATION, so the gate cannot pass
    assert P.classify(True, _val(), {"CHEAP": F(9, 10), "RICH": F(9, 10), "NUE_X": 0, "DECAYED_COUNT": 0}, ok)["classification"] == "R4_CFDC_CAUSAL_STATE_INSUFFICIENT"       # CHEAP EVAL alone cannot upgrade a failed VALIDATION
    c = P.classify(True, _val(nue=F(1, 3)), {"CHEAP": F(1, 10), "RICH": 0, "NUE_X": F(1, 3), "DECAYED_COUNT": 0}, ok); assert c["comparator_reaches_viability"] and c["classification"] == "R4_CFDC_CAUSAL_STATE_INSUFFICIENT"
    assert 24999 / 100000 < 0.25 and not P.classify(True, _val(F(1, 3)), {"CHEAP": F(24999, 100000), "RICH": 0, "NUE_X": 0, "DECAYED_COUNT": 0}, ok)["rule_A"]


def test_workload_consistency_rule():
    ev = {"CHEAP": F(4, 10), "RICH": 0, "NUE_X": 0, "DECAYED_COUNT": 0}
    assert P.classify(True, _val(F(3, 10)), ev, {"a": F(0), "b": F(1, 2)})["rule_C"] is True
    assert P.classify(True, _val(F(3, 10)), ev, {"a": F(-1, 1000), "b": F(1, 2)})["rule_C"] is False
    assert P.classify(True, _val(F(3, 10)), ev, {"a": None, "b": F(1, 2)})["rule_C"] is False


def test_bootstrap_deterministic_and_blocks():
    sb = {"t": {"A": {i: 10 for i in range(130)}, "P": {i: 6 for i in range(130)}, "BELADY": {i: 2 for i in range(130)}}}
    a = P.block_bootstrap(sb, "P"); b = P.block_bootstrap(sb, "P"); assert a == b and a["n_blocks"] == 3 and abs(a["p05"] - 0.5) < 1e-9


# ---------------------------------------------------------------- outcome-dependent
def test_exactly_one_result_per_arm_per_trace_and_hashes():
    if not scored(): pytest.skip("not scored yet")
    raw = jl(HERE / "EVAL_RAW_RESULTS.json"); ids = [e["workload_id"] for e in P.eval_entries()]
    assert raw["unit_count"] == 24 and raw["reruns"] == 0 and sorted(raw["results"]) == sorted(ids)
    for w in ids: assert sorted(raw["results"][w]) == sorted(P.ARMS) and all(P.result_sha(raw["results"][w][a]) == raw["execution_units"][w][a]["result_sha256"] for a in P.ARMS)
    assert all(v["match"] for v in raw["eval_trace_hash_check"].values())


def test_results_consistent_with_rules():
    if not scored(): pytest.skip("not scored yet")
    raw = jl(HERE / "EVAL_RAW_RESULTS.json"); res = jl(HERE / "EVAL_RESULTS.json"); dec = jl(HERE / "DECISION_APPLICATION.json"); fin = jl(HERE / "FINAL_CLASSIFICATION.json"); ids = list(raw["results"])
    mb = {w: {a: raw["results"][w][a]["decode"]["miss_bytes"] for a in P.ARMS} for w in ids}
    for a in P.POLS: assert F(res["aggregate"][a]["capture"]) == P.pooled_capture(mb, a)
    ev = {a: F(res["aggregate"][a]["capture"]) for a in P.POLS}; v = {a: F(x) for a, x in dec["validation_captures_imported"].items()}
    pw = {w: P.capture(mb[w]["A"], mb[w]["CHEAP"], mb[w]["BELADY"]) for w in ids}
    c = P.classify(res["integrity"]["ok"], v, ev, pw); assert c["classification"] == fin["classification"] == dec["classification"] and F(dec["rule_B"]["required_capture"]) == c["required_capture"]
    assert fin["r4_cfdc_state"] == "R4_CFDC_CLOSED" and fin["eval_state"] == "CLOSED" and fin["rescue_authorized"] is False
    assert fin["phase3_authorized"] == (fin["classification"] == "R4_CFDC_CHEAP_VIABLE")
    assert all(raw["results"][w][a]["budget_violations"] == 0 for w in ids for a in P.ARMS)


def test_final_freeze_deterministic():
    if not (HERE / "FREEZE.json").exists(): pytest.skip("not frozen yet")
    fz = jl(HERE / "FREEZE.json"); assert fz["freeze_sha256"] == P1.freeze_sha(fz) and fz["state"] == "R4_CFDC_CLOSED" and fz["eval_state"] == "CLOSED"
    assert fz["contract_digest"] == P1.sha_bytes(P1.canon({k: v for k, v in fz.items() if k not in ("freeze_sha256", "contract_digest")}))
    for f, h in fz["files_sha256"].items(): assert P1.lf_sha(HERE / f) == h
    assert P.verify() == []


# ---------------------------------------------------------------- end-to-end plumbing on synthetic (non-EVAL) event streams
def test_pipeline_dry_run_on_synthetic_units(tmp_path, monkeypatch):
    H = tmp_path / "h"; H.mkdir(); (H / "tests").mkdir(); C = tmp_path / "c"; C.mkdir()
    ids = ["w-coding", "w-reasoning", "w-summarization", "w-instruction"]; cats = ["coding", "reasoning", "summarization", "instruction"]
    ents = [{"workload_id": w, "nominal_category": c, "trace_sha256": "0" * 64, "normalized_step_count": 1, "trace_path_host": "x"} for w, c in zip(ids, cats)]
    monkeypatch.setattr(P, "HERE", H); monkeypatch.setattr(P, "CACHE", C); monkeypatch.setattr(P, "eval_entries", lambda: ents); monkeypatch.setattr(P, "eval_guard", lambda: None)
    (C / "hashcheck.json").write_text(json.dumps({w: {"expected": "0" * 64, "actual": "0" * 64, "match": True} for w in ids}))
    for k, w in enumerate(ids):
        es = es_from(rnd(20 + k, 700, 20), 12)
        for a in P.ARMS:
            r = R.run_belady_steps(es) if a == "BELADY" else R.run_arm(es, P._policy(a), None)
            r["seconds"] = 0.0; r["backend"] = "numpy"; r["result_sha256"] = P.result_sha(r); r["step_sha256"] = "x"; (C / f"evalunit_{w}_{a}.json").write_text(P.dumps(r))
    P.finalize(); fin = jl(H / "FINAL_CLASSIFICATION.json"); assert fin["classification"] in P.CLASSES and fin["eval_state"] == "CLOSED"
    res = jl(H / "EVAL_RESULTS.json"); assert res["integrity"]["one_result_per_arm_per_trace"] and "block_bootstrap_90pct_pooled_capture" in res["nongating"]
    op = jl(HERE / "EVAL_OPENED.json") if (HERE / "EVAL_OPENED.json").exists() else {"identity": P.identity(), "starting_head": "x"}
    (H / "EVAL_OPENED.json").write_text(P.dumps(op)); (H / "EVAL_PLAN.json").write_text("{}\n"); (H / "README.md").write_text("r\n")
    for f in P.CODE_FILES + P.TESTS: (H / f).write_text("x\n")
    fz = P.freeze(); fz2 = P.freeze(); assert fz == fz2 and fz["state"] == "R4_CFDC_CLOSED"
