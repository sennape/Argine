"""Research 4 Phase 2 -- CFDC prospective EVAL and final classification.
Commands: plan | open | hashcheck | unit | run | finalize | freeze | verify.
Nothing here changes a frozen policy, model, feature, threshold, comparator or rule: Phase 0/1 are imported and hash-checked, not edited.
EVAL trace bytes are read only through load_eval_trace(), which refuses to run unless EVAL_OPENED.json is committed and every frozen hash still matches."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
P1D = REPO / "experiments" / "R4-01-cfdc-training-validation"
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(P1D)); sys.path.insert(0, str(REPO))
import numpy as np  # noqa: E402
import engine as G  # noqa: E402
import models as M  # noqa: E402
import phase1 as P1  # noqa: E402
import runner as R  # noqa: E402

WORK = Path(os.environ.get("R4_WORK", str(Path.home() / "r4work"))); CACHE = WORK / "cache2"
STATE_OPENED = "R4_CFDC_PHASE2_EVAL_OPENED_NO_OUTCOME"
STATE_CLOSED = "R4_CFDC_CLOSED"
PHASE1_COMMIT_C = "e46df73992ec64e829eddce63e3d50e65bdbd434"
PHASE1_IMPL_COMMIT = "db84d702989a3334578b00c1922063056118cd94"
PHASE1_FREEZE_SHA = "dbea96e96a386286e49b93e733fb1ed68b5fe22771d9b4d9cf7d8cf55f1756d5"
PHASE1_CONTRACT_DIGEST = "8d89ecd8198db93000456cde58f995e4fa23124d554402b61170a776d9a0e74f"
RICH_DIGEST = "8206a5098d0c1a094b91c5da3b9ecbcc0d07adefc584f78df10d6b043f58c3af"
CHEAP_TREE_HASH = "8603908b188c3346c7ed86e1d6914f378c869e017a305fccf9b1adafc734fcf2"
ARMS = ["A", "BELADY", "NUE_X", "DECAYED_COUNT", "CHEAP", "RICH"]
POLS = ["NUE_X", "DECAYED_COUNT", "CHEAP", "RICH"]
CODE_FILES = ["runner.py", "phase2.py"]
TESTS = ["tests/test_phase2.py"]
PRE = ["EVAL_PLAN.json", "EVAL_OPENED.json"]
POST = ["EVAL_RAW_RESULTS.json", "EVAL_RESULTS.json", "PER_WORKLOAD_RESULTS.json", "DECISION_APPLICATION.json", "FINAL_CLASSIFICATION.json", "CLAIM_BOUNDARIES.md", "README.md", "PROVENANCE.json"]
KAPPA_LOW = 0.3792
CLASSES = ["R4_CFDC_INVALID", "R4_CFDC_CHEAP_VIABLE", "R4_CFDC_HETEROGENEOUS_NOT_VIABLE", "R4_CFDC_CHEAP_NOT_INCREMENTAL", "R4_CFDC_CAUSAL_INFORMATION_PRESENT_CHEAP_NOT_DEMONSTRATED", "R4_CFDC_CAUSAL_STATE_INSUFFICIENT"]
Q, MARGIN = Fraction(1, 4), Fraction(3, 100)


class P2Error(Exception):
    pass


sha_bytes, lf_sha, canon, dumps, jread = P1.sha_bytes, P1.lf_sha, P1.canon, P1.dumps, P1.jread
def jwrite(name: str, o: Any) -> None: (HERE / name).write_text(dumps(o), encoding="utf-8", newline="\n")
git, freeze_sha = P1.git, P1.freeze_sha
def splits() -> Dict[str, Any]: return P1.splits()
def eval_entries() -> List[Dict[str, Any]]: return splits()["EVAL"]["traces"]
def rel(p: str) -> str: return "experiments/R4-02-cfdc-prospective-eval/" + p
def committed(path: str) -> bool: return bool(git("log", "--format=%H", "-n", "1", "--", path)) and not git("status", "--short", "--", path)
def result_sha(r: Dict[str, Any]) -> str: return sha_bytes(canon({k: v for k, v in r.items() if k not in ("seconds", "backend", "chunks", "chunk_seconds", "step_miss_bytes", "step_sha256", "result_sha256")}))
def _cache(n: str) -> Path: CACHE.mkdir(parents=True, exist_ok=True); return CACHE / (n + ".json")


# ====================================================================== pure decision logic (unit-tested on synthetic inputs)
def capture(b_a: int, b_p: int, b_bel: int) -> Optional[Fraction]:
    den = b_a - b_bel
    return Fraction(b_a - b_p, den) if den > 0 else None


def pooled_capture(rows: Dict[str, Dict[str, int]], pol: str) -> Optional[Fraction]:
    return capture(sum(r["A"] for r in rows.values()), sum(r[pol] for r in rows.values()), sum(r["BELADY"] for r in rows.values()))


def required_capture(nue_x: Fraction, dc: Fraction) -> Fraction:
    return max(Q, max(nue_x, dc) + MARGIN)


def classify(integrity_ok: bool, val: Dict[str, Fraction], ev: Dict[str, Fraction], per_workload_cheap: Dict[str, Optional[Fraction]]) -> Dict[str, Any]:
    """Frozen DECISION_RULES.json applied mechanically. val/ev map policy -> pooled capture; per_workload_cheap maps EVAL workload -> capture or None (undefined)."""
    A = val["CHEAP"] >= Q and ev["CHEAP"] >= Q
    req = required_capture(ev["NUE_X"], ev["DECAYED_COUNT"]); B = ev["CHEAP"] >= req
    C = all(v is not None and v >= 0 for v in per_workload_cheap.values())
    rich = val["RICH"] >= Q and ev["RICH"] >= Q
    comp = any(val[p] >= Q and ev[p] >= Q for p in ("NUE_X", "DECAYED_COUNT"))
    if not integrity_ok: cls = CLASSES[0]
    elif A and B and C: cls = CLASSES[1]
    elif A and B and not C: cls = CLASSES[2]
    elif A and not B: cls = CLASSES[3]
    elif rich: cls = CLASSES[4]
    else: cls = CLASSES[5]
    return {"rule_A": A, "rule_B": B, "rule_C": C, "rich_gate": rich, "required_capture": req, "comparator_reaches_viability": comp, "classification": cls}


def block_bootstrap(step_bytes: Dict[str, Dict[str, Dict[int, int]]], pol: str, seed: int = 0, n: int = 2000, block: int = 64) -> Dict[str, Any]:
    """step_bytes[trace][arm][step] = decode miss bytes. Contiguous 64-step decode blocks per trace, pooled and resampled with replacement."""
    blocks = []
    for tr in sorted(step_bytes):
        steps = sorted(step_bytes[tr]["A"])
        for b in range(0, len(steps), block):
            ss = steps[b:b + block]; blocks.append([sum(step_bytes[tr][a][s] for s in ss) for a in ("A", pol, "BELADY")])
    arr = np.array(blocks, dtype=np.float64); rng = np.random.RandomState(seed); vals = []
    for _ in range(n):
        idx = rng.randint(0, len(arr), len(arr)); s = arr[idx].sum(axis=0); den = s[0] - s[2]
        if den > 0: vals.append((s[0] - s[1]) / den)
    lo, hi = np.percentile(vals, [5, 95]) if vals else (None, None)
    return {"n_blocks": len(arr), "resamples": n, "valid_resamples": len(vals), "seed": seed, "p05": None if lo is None else float(lo), "p95": None if hi is None else float(hi)}


# ====================================================================== frozen-identity gate
def identity() -> Dict[str, Any]:
    mh = P1.model_hashes(); imp = P1.impl_record()["files_lf_sha256"]; fz1 = jread(P1D / "FREEZE.json"); fz0 = jread(P1.P0 / "FREEZE.json")
    return {"phase0_commit": P1.PHASE0_COMMIT, "phase0_contract_digest": fz0["contract_digest"], "phase0_freeze_sha256": fz0["freeze_sha256"], "phase1_commit": PHASE1_COMMIT_C, "phase1_implementation_commit": PHASE1_IMPL_COMMIT,
            "phase1_contract_digest": fz1["contract_digest"], "phase1_freeze_sha256": fz1["freeze_sha256"], "phase1_state": fz1["state"], "phase1_eval_state": fz1["eval_state"], "rich_model_digest": mh["rich_model_digest"], "cheap_tree_hash": mh["cheap_tree_hash"],
            "model_hashes": mh, "implementation_files_lf_sha256": imp, "phase1_validation_results_lf_sha256": lf_sha(P1D / "VALIDATION_RESULTS.json"), "phase1_validation_decision_lf_sha256": lf_sha(P1D / "VALIDATION_DECISION.json"),
            "decision_rules_lf_sha256": lf_sha(P1.P0 / "DECISION_RULES.json"), "data_splits_lf_sha256": lf_sha(P1.P0 / "DATA_SPLITS.json"), "baselines_lf_sha256": lf_sha(P1.P0 / "BASELINES.json")}


def identity_problems(idn: Dict[str, Any]) -> List[str]:
    p = []
    exp = {"phase0_contract_digest": P1.PHASE0_DIGEST, "phase0_freeze_sha256": P1.PHASE0_FREEZE, "phase1_contract_digest": PHASE1_CONTRACT_DIGEST, "phase1_freeze_sha256": PHASE1_FREEZE_SHA, "rich_model_digest": RICH_DIGEST, "cheap_tree_hash": CHEAP_TREE_HASH}
    for k, v in exp.items():
        if idn[k] != v: p.append(f"{k} differs")
    if idn["phase1_state"] != P1.STATE_FINAL or idn["phase1_eval_state"] != "LOCKED": p.append("phase 1 state/eval state")
    return p


def expected_eval_hashes() -> Dict[str, str]: return {e["workload_id"]: e["trace_sha256"] for e in eval_entries()}


def plan() -> Dict[str, Any]:
    ents = eval_entries()
    return {"artifact": "R4-02-EVAL-PLAN", "purpose": "Open the frozen EVAL corpus exactly once, score the six frozen arms, apply the preregistered rules mechanically, close Research 4 CFDC.", "state_transition": "LOCKED -> OPENED -> CLOSED",
            "eval_corpus": [{"workload_id": e["workload_id"], "category": e["nominal_category"], "trace_sha256": e["trace_sha256"], "normalized_step_count": e["normalized_step_count"]} for e in ents], "corpus_note": "exactly the four canonical R2-09 EVAL traces frozen in Phase 0; no substitution, no new prompt/trace, no model run",
            "arms": ARMS, "budget_bytes": G.BUDGET, "replay": "frozen Phase 1 engine; own-state replay; one cold-cache replay per (trace, arm); each (trace, arm) unit is executed exactly once; technical checkpoint/resume is deterministic and does not alter a completed result",
            "primary_endpoint": "capture(P) = sum(B_A - B_P) / sum(B_A - B_Belady), decode miss bytes at 1.5 GiB, pooled over the four EVAL traces, exact rational, uncapped",
            "rules": {"A": "capture_VAL(CHEAP) >= 1/4 AND capture_EVAL(CHEAP) >= 1/4", "B": "capture_EVAL(CHEAP) >= max(1/4, max(NUE_X, DECAYED_COUNT) + 3/100)", "C": "capture_w(CHEAP) >= 0 for every EVAL workload; undefined denominator fails",
                      "RICH_gate": "capture_VAL(RICH) >= 1/4 AND capture_EVAL(RICH) >= 1/4", "classification_order": [c for c in jread(P1.P0 / "DECISION_RULES.json")["classification_order"]]},
            "validation_import": "VALIDATION_RESULTS.json / VALIDATION_DECISION.json of Phase 1 are imported verbatim (hash-checked); nothing is recomputed",
            "non_gating_reports": ["EVAL workloads on which CHEAP beats the best comparator", "block-bootstrap 90% interval (64-token decode blocks, seed 0, 2000 resamples)", "VALIDATION comparator captures with/without r2-11-coding-v1 (imported)",
                                   "REPLICATE_CHECK seed-43: NOT COMPUTED (report-only, outside the Phase 2 scope of four EVAL traces)", "teacher-agreement shift diagnostic on EVAL: NOT COMPUTED (done on TRAIN in Phase 1; needs no further evidence for classification)"],
            "forbidden": ["policy/model/feature/threshold/comparator/rule change", "rescue", "new candidate", "tuning", "rerunning a unit in search of a better value", "Phase 3 unless CHEAP is classified viable"],
            "execution_protocol": {"workers": 2, "orchestrator": "phase2.py run", "resume": "per-unit scratch caches and pickle checkpoints; a completed unit is never recomputed", "hash_check_first": True},
            "code_files_lf_sha256": {f: lf_sha(HERE / f) for f in CODE_FILES}, "test_files_lf_sha256": {f: lf_sha(HERE / f) for f in TESTS if (HERE / f).exists()}, "python": sys.version.split()[0], "numpy": np.__version__}


def open_record() -> Dict[str, Any]:
    if git("status", "--short", "--", "experiments", "paper", "argine", ":(exclude)experiments/R4-02-cfdc-prospective-eval"): raise P2Error("worktree not clean outside the new Phase 2 directory")
    idn = identity(); pr = identity_problems(idn)
    if pr: raise P2Error("frozen identity mismatch: " + "; ".join(pr))
    return {"artifact": "R4-02-EVAL-OPENED", "state": STATE_OPENED, "eval_state_before": "LOCKED", "eval_state_after": "OPENED", "branch": git("branch", "--show-current"), "starting_head": git("rev-parse", "HEAD"), "identity": idn,
            "eval_plan_lf_sha256": lf_sha(HERE / "EVAL_PLAN.json"), "scorer_code_lf_sha256": {f: lf_sha(HERE / f) for f in CODE_FILES}, "scorer_test_lf_sha256": {f: lf_sha(HERE / f) for f in TESTS},
            "eval_trace_identities": [{"workload_id": e["workload_id"], "category": e["nominal_category"], "expected_trace_sha256": e["trace_sha256"], "normalized_step_count": e["normalized_step_count"]} for e in eval_entries()], "eval_trace_count": len(eval_entries()),
            "arms": ARMS, "budget_bytes": G.BUDGET, "frozen_rules": jread(P1.P0 / "DECISION_RULES.json")["rules"], "endpoint": "capture(P) = sum(B_A - B_P) / sum(B_A - B_Belady), pooled, exact",
            "eval_outcome_contained": False, "eval_trace_content_read_before_this_record": False, "eval_metrics_computed_before_this_record": False, "rule": "no source change affecting policy behaviour after this record; each (trace, arm) unit runs once"}


# ====================================================================== guarded EVAL access
def eval_guard() -> Dict[str, Any]:
    if (HERE / "EVAL_RESULTS.json").exists(): raise P2Error("EVAL already scored: refusing to run twice")
    if not (HERE / "EVAL_OPENED.json").exists() or not committed(rel("EVAL_OPENED.json")): raise P2Error("EVAL_OPENED.json is not committed: refusing to read EVAL")
    op = jread(HERE / "EVAL_OPENED.json")
    idn = identity()
    if idn != op["identity"]: raise P2Error("a frozen identity differs from the opening record")
    for f, h in op["scorer_code_lf_sha256"].items():
        if lf_sha(HERE / f) != h: raise P2Error(f"{f} changed after the opening record")
    if git("status", "--short", "--", "experiments", "paper", "argine"): raise P2Error("worktree not clean")
    return op


def load_eval_trace(entry: Dict[str, Any]):
    eval_guard()
    p = P1.resolve_trace(entry); h = sha_bytes(p.read_bytes())
    if h != entry["trace_sha256"]: raise P2Error(f"EVAL trace hash differs from DATA_SPLITS: {entry['workload_id']}")
    from argine.research.trace import Trace
    return Trace.load(str(p))


def hashcheck() -> Dict[str, Any]:
    eval_guard(); out = {}
    for e in eval_entries():
        h = sha_bytes(P1.resolve_trace(e).read_bytes()); out[e["workload_id"]] = {"expected": e["trace_sha256"], "actual": h, "match": h == e["trace_sha256"]}
    if not all(v["match"] for v in out.values()): raise P2Error("EVAL trace hash mismatch: STOP")
    _cache("hashcheck").write_text(dumps(out)); return out


# ====================================================================== execution (one unit = one trace x one arm)
def _policy(arm: str):
    if arm == "A": return G.PolA()
    if arm == "NUE_X": return G.PolNUEX()
    if arm == "DECAYED_COUNT": return G.PolDC()
    if arm == "CHEAP": return G.PolCheap(jread(P1D / "CHEAP_MODEL.json")["tree"])
    if arm == "RICH": return G.PolRich(M.RichModel.from_json(jread(P1D / "RICH_MODEL.json")))
    raise P2Error(arm)


def unit(wid: str, arm: str, budget_s: float) -> str:
    f = _cache(f"evalunit_{wid}_{arm}")
    if f.exists(): return "cached"
    e = next(x for x in eval_entries() if x["workload_id"] == wid)
    es = G.from_trace(load_eval_trace(e), G.BUDGET, wid); t = time.time(); ck = str(CACHE / f"ckpt_{wid}_{arm}.pkl")
    pol = None if arm == "BELADY" else _policy(arm)
    if arm == "BELADY": r = R.run_belady_steps(es)
    elif arm in R.RESUMABLE: r = R.run_arm(es, pol, ck, budget_s)
    else: r = R.run_arm(es, pol, None)
    if r is None: return "partial"
    st = jread(CACHE / f"secs_{wid}_{arm}.json")["s"] if (CACHE / f"secs_{wid}_{arm}.json").exists() else []
    st.append(round(time.time() - t, 1)); r["seconds"] = round(sum(st), 1); r["chunk_seconds"] = st
    r["backend"] = pol.m.backend if arm == "RICH" else ("numpy" if arm != "BELADY" else "frozen-oracle")
    r["result_sha256"] = result_sha(r); r["step_sha256"] = sha_bytes(canon(r["step_miss_bytes"])); f.write_text(dumps(r)); return "done"


def note_chunk(wid: str, arm: str, secs: float) -> None:
    p = CACHE / f"secs_{wid}_{arm}.json"; s = jread(p)["s"] if p.exists() else []; s.append(round(secs, 1)); p.write_text(json.dumps({"s": s}))


def run(total_s: float) -> int:
    op = eval_guard(); t0 = time.time(); hashcheck()
    M.RichModel.from_json(jread(P1D / "RICH_MODEL.json"))._load_lib()
    ids = [e["workload_id"] for e in eval_entries()]
    tasks = [(w, a) for a in ("A", "BELADY", "NUE_X", "DECAYED_COUNT", "CHEAP") for w in ids if not _cache(f"evalunit_{w}_{a}").exists()] + [(w, "RICH") for w in ids if not _cache(f"evalunit_{w}_RICH").exists()]
    lock = threading.Lock(); claimed = set()
    def worker() -> None:
        while True:
            with lock:
                nxt = next((t for t in tasks if t not in claimed), None); left = total_s - (time.time() - t0)
                if nxt is None or left < 25: return
                if nxt[1] not in R.RESUMABLE and left < 70: return
                claimed.add(nxt)
            w, a = nxt; ts = time.time()
            r = subprocess.run([sys.executable, str(HERE / "phase2.py"), "unit", w, a, str(max(10.0, left - 20))], capture_output=True, text=True)
            print(w, a, r.returncode, (r.stdout.strip().splitlines() or [""])[-1], r.stderr.strip()[-300:], flush=True)
            if r.stdout.strip().endswith("partial"):
                note_chunk(w, a, time.time() - ts)
                with lock: claimed.discard(nxt)
                return
    ths = [threading.Thread(target=worker) for _ in range(2)]; [t.start() for t in ths]; [t.join() for t in ths]
    done = sum(_cache(f"evalunit_{w}_{a}").exists() for w in ids for a in ARMS); print("units complete:", done, "of", len(ids) * len(ARMS)); return 0 if done == len(ids) * len(ARMS) else 3


# ====================================================================== assembly
def finalize() -> None:
    op = eval_guard(); ents = eval_entries(); ids = [e["workload_id"] for e in ents]
    if not all(_cache(f"evalunit_{w}_{a}").exists() for w in ids for a in ARMS): raise P2Error("not all 24 units are complete")
    hc = jread(_cache("hashcheck")); units = {w: {a: jread(_cache(f"evalunit_{w}_{a}")) for a in ARMS} for w in ids}
    arms = {w: {a: units[w][a]["decode"]["miss_bytes"] for a in ARMS} for w in ids}; tokens = {w: units[w]["A"]["decode"]["tokens"] for w in ids}
    vres = jread(P1D / "VALIDATION_RESULTS.json"); vdec = jread(P1D / "VALIDATION_DECISION.json"); vagg = vres["aggregate_all_12"]
    val = {p: Fraction(vagg[p]["capture"]) for p in POLS}
    integ = {"frozen_identities_match": True, "eval_trace_hashes_match": all(v["match"] for v in hc.values()), "budget_violations": sum(units[w][a]["budget_violations"] for w in ids for a in ARMS), "pooled_denominator": sum(arms[w]["A"] - arms[w]["BELADY"] for w in ids),
             "validation_imported_exactly": {"cheap": vagg["CHEAP"]["capture"], "rich": vagg["RICH"]["capture"], "nue_x": vagg["NUE_X"]["capture"], "decayed_count": vagg["DECAYED_COUNT"]["capture"]}, "one_result_per_arm_per_trace": all(len(units[w]) == 6 for w in ids)}
    integ["ok"] = bool(integ["eval_trace_hashes_match"] and integ["budget_violations"] == 0 and integ["pooled_denominator"] > 0 and integ["one_result_per_arm_per_trace"])
    agg = {a: P1.pooled(arms, ids, a) for a in POLS}; ev = {a: Fraction(agg[a]["capture"]) if agg[a]["capture"] is not None else Fraction(0) for a in POLS}
    perw = {}
    for w in ids:
        den = arms[w]["A"] - arms[w]["BELADY"]; row = {"category": next(e["nominal_category"] for e in ents if e["workload_id"] == w), "tokens": tokens[w], "B_A": arms[w]["A"], "B_BELADY": arms[w]["BELADY"], "denominator": den, "denominator_defined": den > 0}
        for a in POLS:
            d = arms[w]["A"] - arms[w][a]; u = units[w][a]; c = capture(arms[w]["A"], arms[w][a], arms[w]["BELADY"])
            row[a] = {"B_P": arms[w][a], "avoided_vs_A": d, "capture": str(c) if c is not None else None, "capture_float": float(c) if c is not None else None, "sign": "+" if d > 0 else "-" if d < 0 else "0", "zero_cost_ms_per_token": KAPPA_LOW * d / tokens[w] / 1e6,
                      "misses_per_token": u["decode"]["misses"] / tokens[w], "miss_bytes_per_token": u["decode"]["miss_bytes"] / tokens[w], "decode_evictions": u["decode"]["evictions"], "decode_reloads": u["decode_reloads"], "decode_misses_per_layer": u["decode_misses_per_layer"], "result_sha256": u["result_sha256"]}
        perw[w] = row
    cheap_pw = {w: (Fraction(perw[w]["CHEAP"]["capture"]) if perw[w]["CHEAP"]["capture"] is not None else None) for w in ids}
    cl = classify(integ["ok"], val, ev, cheap_pw)
    best = max(("NUE_X", "DECAYED_COUNT"), key=lambda a: ev[a])
    beats = [w for w in ids if perw[w]["CHEAP"]["capture"] is not None and all(perw[w][a]["capture"] is not None for a in ("NUE_X", "DECAYED_COUNT")) and Fraction(perw[w]["CHEAP"]["capture"]) > max(Fraction(perw[w]["NUE_X"]["capture"]), Fraction(perw[w]["DECAYED_COUNT"]["capture"]))]
    sb = {w: {a: {int(s): int(b) for s, b in units[w][a]["step_miss_bytes"]} for a in ("A", "BELADY") + tuple(POLS)} for w in ids}
    boot = {a: block_bootstrap({w: {"A": sb[w]["A"], a: sb[w][a], "BELADY": sb[w]["BELADY"]} for w in ids}, a) for a in POLS}
    raw = {"artifact": "R4-02-EVAL-RAW-RESULTS", "opening_commit": git("log", "--format=%H", "-n", "1", "--", rel("EVAL_OPENED.json")), "eval_trace_hash_check": hc, "execution_units": {w: {a: {"result_sha256": units[w][a]["result_sha256"], "chunks": units[w][a].get("chunks", 1), "seconds": units[w][a].get("seconds"), "backend": units[w][a].get("backend")} for a in ARMS} for w in ids},
           "unit_count": len(ids) * len(ARMS), "eval_executions": len(ids) * len(ARMS), "reruns": 0, "results": {w: {a: {k: v for k, v in units[w][a].items() if k not in ("seconds", "chunk_seconds", "backend", "step_miss_bytes")} for a in ARMS} for w in ids}, "step_miss_bytes": {w: {a: units[w][a]["step_miss_bytes"] for a in ARMS} for w in ids}}
    results = {"artifact": "R4-02-EVAL-RESULTS", "split": "EVAL (4 canonical R2-09 traces)", "budget_bytes": G.BUDGET, "integrity": integ, "reference_A_eval_miss_bytes": sum(arms[w]["A"] for w in ids), "belady_eval_miss_bytes": sum(arms[w]["BELADY"] for w in ids), "belady_headroom_bytes": integ["pooled_denominator"],
               "aggregate": agg, "miss_bytes_per_trace": {w: {a: arms[w][a] for a in ARMS} for w in ids}, "cheap_absolute_miss_byte_delta_vs_A": agg["CHEAP"]["avoided_vs_A"], "best_causal_comparator": best,
               "nongating": {"eval_workloads_where_cheap_beats_best_comparator": {"count": len(beats), "of": len(ids), "workloads": beats, "definition": "CHEAP per-workload capture strictly above max(NUE_X, DECAYED_COUNT) on that workload"},
                             "block_bootstrap_90pct_pooled_capture": boot, "validation_comparator_with_without_r2_11_coding_v1": {"imported_from": "R4-01 VALIDATION_RESULTS.json", "all_12": {p: vagg[p]["capture_float"] for p in POLS}, "excluding_coding_v1": {p: vres["sensitivity_excluding_r2-11-coding-v1_NONGATING"][p]["capture_float"] for p in POLS}},
                             "replicate_check_seed43": "NOT COMPUTED", "teacher_agreement_shift_eval": "NOT COMPUTED (TRAIN diagnostic is in Phase 1)"}}
    dapp = {"artifact": "R4-02-DECISION-APPLICATION", "validation_captures_imported": {p: vagg[p]["capture"] for p in POLS}, "eval_captures": {p: agg[p]["capture"] for p in POLS}, "eval_captures_float": {p: agg[p]["capture_float"] for p in POLS},
            "rule_A": {"cheap_validation_ge_25": bool(val["CHEAP"] >= Q), "cheap_eval_ge_25": bool(ev["CHEAP"] >= Q), "passed": cl["rule_A"]}, "rule_B": {"required_capture": str(cl["required_capture"]), "required_capture_float": float(cl["required_capture"]), "best_comparator": best, "best_comparator_capture_float": float(ev[best]), "cheap_reaches_required": bool(ev["CHEAP"] >= cl["required_capture"]), "passed": cl["rule_B"], "note": "cannot upgrade a failed VALIDATION"},
            "rule_C": {"per_workload_cheap_capture": {w: perw[w]["CHEAP"]["capture_float"] for w in ids}, "per_workload_sign": {w: perw[w]["CHEAP"]["sign"] for w in ids}, "passed": cl["rule_C"]},
            "rich_gate": {"rich_validation_ge_25": bool(val["RICH"] >= Q), "rich_eval_ge_25": bool(ev["RICH"] >= Q), "passed": cl["rich_gate"]}, "comparator_reaches_viability": {"NUE_X": [bool(val["NUE_X"] >= Q), bool(ev["NUE_X"] >= Q)], "DECAYED_COUNT": [bool(val["DECAYED_COUNT"] >= Q), bool(ev["DECAYED_COUNT"] >= Q)], "label_applies": cl["comparator_reaches_viability"]},
            "classification": cl["classification"], "precedence": "frozen DECISION_RULES.classification_order applied top to bottom", "integrity_ok": integ["ok"]}
    boundary = {"CHEAP": "the frozen cheap causal rule was (not) demonstrated viable under the preregistered rules", "RICH": "the frozen causal feature representation did (not) show sufficient generalizable information for the preregistered target",
                "not_claimed": ["that all causal eviction is impossible", "that all MoE retention is impossible", "that all learned eviction is impossible", "that other hardware/regimes behave identically"], "scope": "tested Argine regime, frozen feature space, frozen prospective decision rule"}
    fin = {"artifact": "R4-02-FINAL-CLASSIFICATION", "classification": cl["classification"], "companion_label": "COMPARATOR_REACHES_VIABILITY" if cl["comparator_reaches_viability"] else None, "r4_cfdc_state": STATE_CLOSED, "eval_state": "CLOSED", "claim_boundary": boundary,
           "phase3_authorized": cl["classification"] == CLASSES[1], "rescue_authorized": False, "next_scientific_step": ("standalone CHEAP policy-cost microbenchmark (Phase 3 gate)" if cl["classification"] == CLASSES[1] else "none inside Research 4: CFDC is closed; any different idea needs its own Phase 0")}
    jwrite("EVAL_RAW_RESULTS.json", raw); jwrite("EVAL_RESULTS.json", results); jwrite("PER_WORKLOAD_RESULTS.json", {"artifact": "R4-02-PER-WORKLOAD-RESULTS", "per_workload": perw}); jwrite("DECISION_APPLICATION.json", dapp); jwrite("FINAL_CLASSIFICATION.json", fin)
    (HERE / "CLAIM_BOUNDARIES.md").write_text(claim_md(fin), encoding="utf-8", newline="\n")


def claim_md(fin: Dict[str, Any]) -> str:
    b = fin["claim_boundary"]
    return ("# Claim boundaries\n\nFinal classification: `%s`%s. Research 4 CFDC state: `%s`; EVAL state: `CLOSED`.\n\n## What the result means\n\n- CHEAP: %s.\n- RICH: %s.\n\n## What it does not mean\n\n%s\n\n## Scope\n\nThe claim is bounded to the %s.\n\n"
            "No rescue, new candidate, tuning, new corpus or EVAL re-opening follows. Phase 3 (policy-cost microbenchmark) is %s.\n") % (fin["classification"], (" with companion label `%s`" % fin["companion_label"]) if fin["companion_label"] else "", fin["r4_cfdc_state"], b["CHEAP"], b["RICH"],
                                                                                                                  "\n".join("- It does not show " + x + "." for x in b["not_claimed"]), b["scope"], "authorized" if fin["phase3_authorized"] else "not authorized")


def provenance() -> Dict[str, Any]:
    raw = jread(HERE / "EVAL_RAW_RESULTS.json")
    return {"artifact": "R4-02-PROVENANCE", "starting_head": jread(HERE / "EVAL_OPENED.json")["starting_head"], "opening_commit": raw["opening_commit"], "identity": jread(HERE / "EVAL_OPENED.json")["identity"], "python": sys.version.split()[0], "numpy": np.__version__, "platform": platform.platform(),
            "eval_sources": [{"id": e["workload_id"], "sha256": e["trace_sha256"], "path": P1.sanitize(e["trace_path_host"])} for e in eval_entries()], "r2_09_eval_folder_access": "read-only; no source file modified or copied into the repository", "raw_traces_copied": False, "model_inference_runs": 0, "eval_executions": raw["eval_executions"], "reruns": 0,
            "execution_scripts": {f: lf_sha(HERE / f) for f in CODE_FILES}}


def freeze() -> Dict[str, Any]:
    jwrite("PROVENANCE.json", provenance()); fz1 = jread(P1D / "FREEZE.json"); op = jread(HERE / "EVAL_OPENED.json")
    files = PRE + POST + CODE_FILES + TESTS
    fz = {"artifact": "R4-02-FREEZE", "state": STATE_CLOSED, "eval_state": "CLOSED", "phase0_contract_digest": P1.PHASE0_DIGEST, "phase1_contract_digest": fz1["contract_digest"], "phase1_freeze_sha256": fz1["freeze_sha256"], "rich_model_digest": op["identity"]["rich_model_digest"], "cheap_tree_hash": op["identity"]["cheap_tree_hash"],
          "files_sha256": {f: lf_sha(HERE / f) for f in files}, "final_classification": jread(HERE / "FINAL_CLASSIFICATION.json")["classification"], "decision_application_lf_sha256": lf_sha(HERE / "DECISION_APPLICATION.json")}
    fz["contract_digest"] = sha_bytes(canon({k: v for k, v in fz.items() if k != "freeze_sha256"})); fz["freeze_sha256"] = freeze_sha(fz); jwrite("FREEZE.json", fz); return fz


def verify() -> List[str]:
    p: List[str] = []
    for nm, d in (("phase0", P1.P0), ("phase1", P1D)):
        r = subprocess.run([sys.executable, str(d / (nm + ".py")), "verify"], capture_output=True, text=True)
        if r.returncode != 0: p.append(nm + " verify failed")
    p += identity_problems(identity())
    for rev, paths in ((P1.SOURCE_R3_HEAD, ["experiments/R3-*", "paper/research3"]), (P1.PHASE0_COMMIT, ["experiments/R4-00-causal-eviction-distillation"]), (PHASE1_COMMIT_C, ["experiments/R4-01-cfdc-training-validation"])):
        r = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", rev, "--", *paths], capture_output=True, text=True)
        if r.stdout.strip(): p.append(f"files differ from {rev[:7]}: {r.stdout.split()[:4]}")
    if (HERE / "FREEZE.json").exists():
        fz = jread(HERE / "FREEZE.json")
        if fz["freeze_sha256"] != freeze_sha(fz): p.append("freeze sha mismatch")
        for f, h in fz["files_sha256"].items():
            if lf_sha(HERE / f) != h: p.append(f"{f} differs from the freeze")
    if (HERE / "EVAL_OPENED.json").exists():
        op = jread(HERE / "EVAL_OPENED.json")
        for f, h in op["scorer_code_lf_sha256"].items():
            if lf_sha(HERE / f) != h: p.append(f"{f} changed after the opening record")
    for f in sorted(HERE.rglob("*")):
        if f.is_file() and f.suffix in (".json", ".md") and any(s in f.read_text(encoding="utf-8", errors="ignore") for s in ("alpel", "/sessions/")): p.append(f"personal path in {f.name}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["plan", "open", "hashcheck", "unit", "run", "finalize", "freeze", "verify"]); ap.add_argument("args", nargs="*"); ap.add_argument("--budget", type=float, default=150.0); a = ap.parse_args()
    if a.cmd == "plan": jwrite("EVAL_PLAN.json", plan()); print("plan written"); return 0
    if a.cmd == "open": jwrite("EVAL_OPENED.json", open_record()); print("opening record written (no EVAL read)"); return 0
    if a.cmd == "hashcheck": print(json.dumps(hashcheck(), indent=1)); return 0
    if a.cmd == "unit": print(unit(a.args[0], a.args[1], float(a.args[2]))); return 0
    if a.cmd == "run": return run(a.budget)
    if a.cmd == "finalize": finalize(); print("finalized"); return 0
    if a.cmd == "freeze": fz = freeze(); print("FREEZE", fz["freeze_sha256"], fz["contract_digest"]); return 0
    pr = verify(); print("VERIFY OK" if not pr else "VERIFY FAILED: " + "; ".join(pr)); return 0 if not pr else 1


if __name__ == "__main__":
    raise SystemExit(main())
