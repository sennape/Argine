"""Phase 1 tests (synthetic + frozen-artifact checks). No EVAL content is ever read."""
from __future__ import annotations

import json, subprocess, sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import engine as G  # noqa: E402
import models as M  # noqa: E402
import phase1 as P  # noqa: E402

REPO = P.REPO
P0 = P.P0


def jl(p): return json.loads(Path(p).read_text(encoding="utf-8"))


def es_from(events, budget=3, size=1):
    """events: [(step, layer, [experts])] -> EventStream over 48 layers of unit-size experts."""
    return G.EventStream("syn", [e[0] for e in events], [e[1] for e in events], [tuple(sorted(e[2])) for e in events], [True] * len(events), {l: size for l in range(48)}, budget, 8, 128, max(e[0] for e in events) + 1)


def capture(es, pol=None, every_all=True):
    out = []
    def hook(sim, dec, v):
        d2 = dec.shielded(); out.append((dec.j, dec.i, d2.c.tolist(), d2.matrix().tolist(), int(v)))
    G.SAMPLE_EVERY  # sampling constant exists
    old = G.SAMPLE_EVERY; G.SAMPLE_EVERY = 1
    try: G.Sim(es, pol or G.PolA(), hook=hook, need_next_use=True).run()
    finally: G.SAMPLE_EVERY = old
    return out


# ------------------------------------------------------------------ frozen identities
def test_phase0_digest_unchanged():
    fz = jl(P0 / "FREEZE.json")
    assert fz["contract_digest"] == P.PHASE0_DIGEST and fz["freeze_sha256"] == P.PHASE0_FREEZE and fz["state"] == "R4_CFDC_PHASE0_FROZEN"
    r = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", P.PHASE0_COMMIT, "--", "experiments/R4-00-causal-eviction-distillation"], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == ""


def test_eval_remains_locked():
    assert jl(P0 / "FREEZE.json")["eval_state"] == "LOCKED"
    for e in P.splits()["EVAL"]["traces"]:
        with pytest.raises(P.EvalLocked): P.load_trace("EVAL", e)
        with pytest.raises(P.EvalLocked): P.load_trace("VALIDATION", e)          # an EVAL entry is refused even under a loadable split name
        with pytest.raises(P.EvalLocked): P.trace_hash("EVAL", e)
    assert P.splits()["EVAL"]["state"] == "LOCKED"
    names = [p.name.upper() for p in HERE.iterdir()]
    assert not any("EVAL" in n for n in names)


def test_no_eval_source_read():
    for f in ("engine.py", "models.py", "phase1.py", "_rich_infer.c"):
        txt = (HERE / f).read_text(encoding="utf-8")
        assert "R2-09-EVAL" not in txt and "eval-v1" not in txt and "PHASE3B-EVAL" not in txt
    prov = HERE / "PROVENANCE.json"
    if prov.exists(): assert jl(prov)["eval_trace_content_read"] is False


def test_split_identities():
    sp = P.splits(); dev = jl(REPO / P0.parent.name / "R2-09-prediction-benchmark" / "r2_09a" / "dev_corpus_manifest.json")
    ids = [e["instance_id"] for e in sp["TRAIN"]["traces"]]
    assert ids == ["coding-v1__seed-42", "instruction-v1__seed-42", "reasoning-v1__seed-42", "summarization-v1__seed-42"]
    m = {i["instance_id"]: i["trace_sha256"] for i in dev["selected_instances"]}
    assert all(m[e["instance_id"]] == e["trace_sha256"] for e in sp["TRAIN"]["traces"])
    assert len(sp["VALIDATION"]["traces"]) == 12 and sorted({e["nominal_category"] for e in sp["VALIDATION"]["traces"]}) == ["coding", "instruction", "reasoning", "summarization"]
    sets = [{e["trace_sha256"] for e in sp[k]["traces"]} for k in ("TRAIN", "VALIDATION", "EVAL", "REPLICATE_CHECK")]
    for i in range(4):
        for j in range(i + 1, 4): assert not (sets[i] & sets[j])
    assert all(e["seed"] == 43 for e in sp["REPLICATE_CHECK"]["traces"])


def test_research3_immutable():
    r = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", P.SOURCE_R3_HEAD, "--", "experiments/R3-*", "paper/research3"], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == ""
    assert jl(REPO / "experiments" / "R3-final-closure" / "FINAL_RESULT.json")["classification"] == "R3_CLOSED_NEGATIVE_PREDICTIVE_RETENTION_NOT_VIABLE_IN_TESTED_REGIME"


# ------------------------------------------------------------------ features
def test_feature_vocabulary_matches_contract():
    mc = jl(P0 / "MODEL_CONTRACT.json"); fc = jl(P0 / "FEATURE_CONTRACT.json")
    assert G.FEATURE_ORDER == mc["feature_order"] == [f["name"] for f in fc["features"]] and len(G.FEATURE_ORDER) == 16
    assert not any(f["depends_on_future"] for f in fc["features"])


HAND = [(0, 0, [1]), (0, 1, [5]), (1, 0, [2]), (1, 1, [5]), (2, 0, [7])]


def test_feature_update_timing_hand_derived():
    rec = capture(es_from(HAND, budget=3))
    j, i, cands, X, v = rec[0]
    assert (j, i) == (4, 2)
    k01, k15, k02 = 0 * 128 + 1, 1 * 128 + 5, 0 * 128 + 2
    assert sorted(cands) == sorted([k01, k15, k02])
    row = {c: dict(zip(G.FEATURE_ORDER, X[n])) for n, c in enumerate(cands)}
    fx = lambda s: int(np.floor(65536.0 * s))
    pw = lambda n: 0.99 ** n
    S01 = 1.0 * pw(2); S15 = (1.0 * pw(1) + 1.0) * pw(1); S02 = 1.0 * pw(1)
    exp = {k01: dict(events_since_last_ref=4, decayed_count_fx=fx(S01), events_since_admission=4, hits_since_admission=0, reload_count=0, prev_reuse_interval=10**6, mean_reuse_interval=10**6, in_prev_layer_event=0, recency_rank=2, frequency_rank=2, layer_id=0, expert_id=1, layer_cycle_offset=48),
           k15: dict(events_since_last_ref=1, decayed_count_fx=fx(S15), events_since_admission=3, hits_since_admission=1, reload_count=0, prev_reuse_interval=1, mean_reuse_interval=1, in_prev_layer_event=1, recency_rank=0, frequency_rank=0, layer_id=1, expert_id=5, layer_cycle_offset=1),
           k02: dict(events_since_last_ref=2, decayed_count_fx=fx(S02), events_since_admission=2, hits_since_admission=0, reload_count=0, prev_reuse_interval=10**6, mean_reuse_interval=10**6, in_prev_layer_event=1, recency_rank=1, frequency_rank=1, layer_id=0, expert_id=2, layer_cycle_offset=48)}
    for k, d in exp.items():
        for name, val in d.items(): assert row[k][name] == val, (k, name, row[k][name], val)
        assert row[k]["occupancy_permille"] == 1000 and row[k]["pending_misses"] == 1 and row[k]["expert_bytes"] == 1


def _random_events(rng, n, pool=14):
    return [(j // 4, int(rng.integers(0, 3)), sorted(set(int(x) for x in rng.integers(0, pool, int(rng.integers(1, 5)))))) for j in range(n)]


def test_future_routing_cannot_change_features():
    rng = np.random.default_rng(7)
    ev = _random_events(rng, 400); base = capture(es_from(ev, budget=12))
    for cut in (60, 150, 300):
        ev2 = ev[:cut + 1] + _random_events(np.random.default_rng(cut), len(ev) - cut - 1)
        ev2 = [(e[0], e[1], e[2]) for e in ev2]
        # keep step numbers non-decreasing and identical to the original for the shared prefix
        ev2 = ev[:cut + 1] + [(ev[cut][0] + 1 + (k // 4), l, ex) for k, (_, l, ex) in enumerate(ev2[cut + 1:])]
        alt = capture(es_from(ev2, budget=12))
        pre = [r for r in base if r[0] <= cut]; pre2 = [r for r in alt if r[0] <= cut]
        assert len(pre) == len(pre2) and len(pre) > 5
        assert pre == pre2          # candidates, all 16 features and the victim of every decision at or before the cut are unchanged
        assert any(r[3] for r in pre)


def test_future_perturbation_each_feature_individually():
    rng = np.random.default_rng(11); ev = _random_events(rng, 300); base = capture(es_from(ev, budget=10)); cut = 120
    ev2 = ev[:cut + 1] + [(ev[cut][0] + 1, 2, [0, 1, 2, 3, 4, 5]) for _ in range(len(ev) - cut - 1)]
    alt = capture(es_from(ev2, budget=10))
    for a, b in zip([r for r in base if r[0] <= cut], [r for r in alt if r[0] <= cut]):
        for n, name in enumerate(G.FEATURE_ORDER):
            assert [row[n] for row in a[3]] == [row[n] for row in b[3]], name


def test_teacher_next_use_matches_oracle():
    from argine.research.oracle import next_uses
    rng = np.random.default_rng(3); ev = _random_events(rng, 200); es = es_from(ev, budget=8)
    sim = G.Sim(es, G.PolA(), need_next_use=True); nu = next_uses([(es.step[j], es.layer[j], es.exp[j]) for j in range(es.n)])
    for j in range(es.n):
        for e in es.exp[j]:
            d = nu[j][e]; got = sim.next_use_distance(es.layer[j] * 128 + e, j)
            assert (got == float("inf")) if d == float("inf") else (got == d - j)
    # non-members by brute force
    for j in (0, 50, 120, 199):
        for k in range(0, 3 * 128, 7):
            fut = [x for x in range(j + 1, es.n) if k in [es.layer[x] * 128 + e for e in es.exp[x]]]
            assert sim.next_use_distance(k, j) == ((fut[0] - j) if fut else float("inf"))


def test_teacher_target_encoding():
    es = es_from(_random_events(np.random.default_rng(5), 300), budget=6); old = G.SAMPLE_EVERY; G.SAMPLE_EVERY = 1
    try: out = G.teacher_rows(es)
    finally: G.SAMPLE_EVERY = old
    assert out["y"].max() <= G.Y_INF and ((out["y"] == G.Y_INF) == (out["y"] > 20)).all() and out["X"].shape[1] == 16


# ------------------------------------------------------------------ policies
def test_decayed_count_decay_exactly_099():
    assert G.PolDC.decay == 0.99 and G.DECAY == 0.99
    sim = G.Sim(es_from([(0, 0, [1]), (3, 0, [1])]), G.PolDC()); sim.run()
    k = 1; assert sim.POW[1] == 0.99 and sim.POW[3] == 0.99 ** 3 and sim.S[k] == 1.0 * 0.99 ** 3 + 1.0


def test_decayed_count_victim_and_ties():
    ev = [(0, 0, [1]), (0, 0, [2]), (1, 0, [2]), (2, 0, [3]), (3, 0, [4])]
    es = es_from(ev, budget=3); rec = capture(es, G.PolDC()); j, i, cands, X, v = rec[-1]
    assert v == 1          # (0,1) has the lowest decayed count; the others have 2 requests or are newer
    ev = [(0, 0, [1]), (0, 0, [2]), (1, 0, [3]), (2, 0, [4])]
    rec = capture(es_from(ev, budget=3), G.PolDC()); assert rec[-1][4] == 1   # equal S not possible; lowest S is the oldest request


def test_learned_policies_replay_under_own_state():
    tree = jl(HERE / "CHEAP_MODEL.json")["tree"] if (HERE / "CHEAP_MODEL.json").exists() else None
    assert tree is not None
    pol = G.PolCheap(tree); assert not hasattr(pol, "sc") and not hasattr(pol, "last")             # no A state is consulted
    seen = []
    class Spy(G.PolCheap):
        def choose(self, s, d):
            assert set(d.c.tolist()) <= set(np.flatnonzero(s.res).tolist()) and not (set(d.c.tolist()) & set(np.flatnonzero(s.incur).tolist())) or d.fallback
            seen.append(len(d.c)); return super().choose(s, d)
    es = es_from(_random_events(np.random.default_rng(9), 500), budget=10)
    r1 = G.Sim(es, Spy(tree)).run()
    assert seen and r1["budget_violations"] == 0 and r1["evictions_total"] > 0


# ------------------------------------------------------------------ models
def test_rich_hyperparameters():
    c = M.RICH_CFG
    assert (c["rounds"], c["learning_rate"], c["max_depth"], c["min_samples_leaf"], c["max_bins"], c["random_seed"]) == (200, 0.1, 6, 200, 255, 0)
    mc = jl(P0 / "MODEL_CONTRACT.json")["RICH"]
    assert (mc["rounds"], mc["learning_rate"], mc["max_depth"], mc["min_samples_leaf"], mc["random_seed"]) == (200, 0.1, 6, 200, 0)
    if (HERE / "RICH_MODEL.json").exists():
        m = jl(HERE / "RICH_MODEL.json"); assert m["rounds"] == 200 and m["config"] == c and m["learning_rate"] == 0.1


def _toy(n_dec=300, per=40, seed=1):
    rng = np.random.default_rng(seed); g = np.repeat(np.arange(n_dec), per); n = len(g); X = np.zeros((n, 16), np.int32)
    for f in range(16): X[:, f] = rng.integers(0, 3000 if f % 3 else 6, n)
    y = np.log2(1 + X[:, 0] / 20.0 + (X[:, 5] > 1500) * 4 + (X[:, 1] % 7) + rng.normal(0, .5, n) ** 2)
    return X, y, g


def test_cheap_structural_constraints_on_toy_and_frozen():
    cfg = {**M.CHEAP_CFG, "min_samples_leaf": 300, "min_decisions_per_leaf": 20}
    X, y, g = _toy(); t = M.fit_cheap(X, y, g, cfg)
    leaves = [i for i, f in enumerate(t["feature"]) if f < 0]
    assert max(n["depth"] for n in t["node_stats"]) <= 3 and len(t["features_used"]) <= 4
    assert all(t["node_stats"][i]["n_rows"] >= 300 and t["node_stats"][i]["n_decisions"] >= 20 for i in leaves)
    assert all(isinstance(v, int) for v in t["threshold"] + t["leaf_rank"])
    if (HERE / "CHEAP_MODEL.json").exists():
        f = jl(HERE / "CHEAP_MODEL.json")["tree"]; lv = [i for i, ff in enumerate(f["feature"]) if ff < 0]
        assert max(n["depth"] for n in f["node_stats"]) <= 3 and len(f["features_used"]) <= 4
        assert all(f["node_stats"][i]["n_rows"] >= 1000 and f["node_stats"][i]["n_decisions"] >= 50 for i in lv)
        assert all(isinstance(v, int) for v in f["threshold"] + f["leaf_rank"]) and sorted(f["leaf_rank"][i] for i in lv) == list(range(len(lv)))


def test_cheap_refit_rule_and_determinism():
    cfg = {**M.CHEAP_CFG, "min_samples_leaf": 100, "min_decisions_per_leaf": 5, "max_depth": 3}
    X, y, g = _toy(n_dec=400, per=40, seed=4)
    y = y + (X[:, 2] > 1000) * 3 + (X[:, 3] > 500) * 2 + (X[:, 4] > 2000) * 1.5 + (X[:, 6] > 800) * 1.2          # encourage many distinct split features
    t1 = M.fit_cheap(X, y, g, cfg); t2 = M.fit_cheap(X, y, g, cfg)
    assert t1["hash"] == t2["hash"]
    fi = t1["fit_info"]
    if len(fi["first_fit_features"]) > 4:
        assert fi["refit"] is True and len(t1["features_used"]) <= 4
        gains = fi["first_fit_feature_gain"]; order = sorted(gains, key=lambda n: (-gains[n], M.FEATURE_ORDER.index(n)))[:4]
        assert sorted(fi["kept_features"], key=M.FEATURE_ORDER.index) == sorted(order, key=M.FEATURE_ORDER.index)
    else:
        assert fi["refit"] is False


def test_cheap_policy_uses_integer_comparisons_and_tiebreak():
    tree = {"max_depth": 1, "features_used": ["events_since_last_ref"], "feature": [0, -1, -1], "threshold": [5, 0, 0], "left": [1, -1, -1], "right": [2, -1, -1], "leaf_rank": [-1, 0, 1]}
    ev = [(0, 0, [1]), (0, 0, [2]), (1, 0, [3]), (2, 0, [4])]
    rec = capture(es_from(ev, budget=3), G.PolCheap(tree)); assert rec[-1][4] in (1, 2, 3)     # all candidates are in the left leaf (rank 0): the tie goes to the largest events_since_last_ref -> (0,1)
    assert rec[-1][4] == 1


def test_rich_and_hash_determinism_and_c_equivalence():
    X, y, g = _toy(n_dec=60, per=30, seed=2); cfg = {**M.RICH_CFG, "rounds": 5}
    m1 = M.fit_rich(X, y, cfg); m2 = M.fit_rich(X, y, cfg)
    assert m1.digest() == m2.digest()
    assert (m1.predict_q(X[:3000]) == m1.predict_q_numpy(X[:3000])).all()
    assert (M.RichModel.from_json(m1.to_json()).predict_q_numpy(X[:500]) == m1.predict_q_numpy(X[:500])).all()
    assert m1.predict_q_numpy(X[:10]).dtype == np.int64


def test_fitted_artifacts_hashes_consistent():
    if not (HERE / "MODEL_HASHES.json").exists(): pytest.skip("not packed yet")
    assert jl(HERE / "MODEL_HASHES.json") == P.model_hashes()
    imp = jl(HERE / "IMPLEMENTATION.json")["files_lf_sha256"]
    assert imp == {f: P.lf_sha(HERE / f) for f in P.IMPL_FILES}


# ------------------------------------------------------------------ fidelity (live) and recorded
def test_recorded_fidelity_all_exact():
    d = jl(HERE / "REPLAY_FIDELITY.json")
    assert d["all_exact"] is True and d["n_checks"] == 23 and all(c["exact_equal"] and c["budget_violations"] == 0 for c in d["checks"])


def _canon():
    cs = P.R3 / "r1-fidelity" / "CANONICAL_STREAM.jsonl"
    return G.from_canonical([json.loads(l) for l in cs.read_bytes().splitlines()], P.FIRST_DECODE, P.N_DECODE)


def test_reference_a_fidelity_live():
    exp = jl(P.R3 / "r2-belady-ceiling" / "RESULTS.json")["budgets"]["1.5"]["A"]["decode"]; r = G.Sim(_canon(), G.PolA()).run()["decode"]
    assert all(r[k] == exp[k] for k in ("misses", "hits", "miss_bytes", "evictions"))


def test_belady_fidelity_live():
    exp = jl(P.R3 / "r2-belady-ceiling" / "RESULTS.json")["budgets"]["1.5"]["BELADY"]["decode"]; r = G.run_belady(_canon())["decode"]
    assert all(r[k] == exp[k] for k in ("misses", "hits", "miss_bytes", "evictions"))


def test_nue_x_fidelity_live():
    exp = jl(P.R3 / "r4-nue-candidate-gate" / "EVAL_RESULTS.json")["raw"]["NUE_X"]["decode"]; r = G.Sim(_canon(), G.PolNUEX()).run()["decode"]
    assert all(r[k] == exp[k] for k in ("misses", "hits", "miss_bytes", "evictions"))


# ------------------------------------------------------------------ opening precedes metrics
def test_score_refuses_without_committed_opening():
    if (HERE / "VALIDATION_OPENED.json").exists(): pytest.skip("opening already written")
    with pytest.raises(P.P1Error): P.score_guard()


def test_opening_commit_precedes_results_in_history():
    if not (HERE / "VALIDATION_RESULTS.json").exists(): pytest.skip("not scored yet")
    def first(path): return subprocess.run(["git", "-C", str(REPO), "log", "--diff-filter=A", "--format=%ct %H", "--", path], capture_output=True, text=True).stdout.split()
    op = first("experiments/R4-01-cfdc-training-validation/VALIDATION_OPENED.json"); res = first("experiments/R4-01-cfdc-training-validation/VALIDATION_RESULTS.json")
    if res:
        anc = subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor", op[-1], res[-1]]); assert anc.returncode == 0
    o = jl(HERE / "VALIDATION_OPENED.json"); assert o["validation_outcome_contained"] is False and o["eval_state"] == "LOCKED"
