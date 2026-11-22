"""R3-05 rung R2: clairvoyant (Belady-style) ceiling + K1 gate on the frozen R1 canonical stream. Model-free, additive. No candidate is evaluated.

  python r2.py run <arm A|BELADY> <budget 1.5|4|8> <out.json>   replay the canonical stream once
  python r2.py finish <dir>                                      assemble RESULTS / ECONOMIC_HEADROOM / K1_DECISION from the six run files (PASS-independent: K1 may kill)
  python r2.py build <dir>                                       write the additive evidence files + FREEZE.json
  python r2.py verify                                            offline re-verification (inputs, hashes, derived numbers; add --replay to also re-run 1.5 GiB)
"""
from __future__ import annotations

import argparse, hashlib, importlib.util, json, sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
P0 = HERE.parent
R1D = P0 / "r1-fidelity"
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tools"))

PHASE0_FREEZE_SHA = "415f78a850a2917a2457797106589433f944c07a863f80b14cef88fde6232ae3"
R1_FREEZE_SHA = "8d025fdfb3d2a48c54e4d905f3a0e3aad2dd70ed9fdabf602b21ec2b600c3382"
STREAM_SHA = "24fcc3e31e800826acb353c627326d4665af7b05c88a3138b18b79c5c435e776"
TOKEN_DIGEST = "ac28efbe13c80f363f508a6f19d4b1d3af189eb26d6e9c81309376033075cdfa"
N_EVENTS, N_PASSES, N_DECODE, FIRST_DECODE = 24672, 514, 512, 96
GIB = 2**30
BUDGETS = {"1.5": 1536 * 2**20, "4": 4 * GIB, "8": 8 * GIB}
PRIMARY = "1.5"
# frozen B_selected(M) parameters per budget (R2-09A freeze; argine-retention-policy.h FROZEN_B_SELECTED_*)
A_PARAMS = {"1.5": (0.999, 1), "4": (0.99, 64), "8": (0.999, 16)}
# Pre-registered BEFORE any R2 number was computed:
KAPPA_ESTIMABLE = {"1.5": True, "8": True, "4": False}   # kappa is the 1.5<->8 GiB A-wall vs A-miss-bytes association; 4 GiB has no measured A wall time in R3-04 (dropped by the amendment)
CONSISTENCY_PP = {"CONSISTENT": 5.0, "PARTIALLY_CONSISTENT": 15.0}   # |new - prior| relative miss reduction, percentage points, decode phase
FILES = ["README.md", "SOURCE_IDENTITY.json", "BELADY_DEFINITION.md", "RESULTS.json", "ECONOMIC_HEADROOM.json", "K1_DECISION.json", "CLAIM_BOUNDARIES.md", "r2.py", "tests/test_r2.py"]


class R2Error(Exception):
    pass


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True) + "\n"
def freeze_sha(o: Dict[str, Any]) -> str: return sha_bytes(json.dumps({k: v for k, v in o.items() if k != "freeze_sha256"}, sort_keys=True, separators=(",", ":")).encode())
def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)   # type: ignore
    return m


# ------------------------------------------------------------------ inputs
def verify_inputs(stream_path: Path | None = None) -> Dict[str, Any]:
    p0 = _load(P0 / "phase0.py", "_r3_05_p0")
    if p0.verify(): raise R2Error("Phase 0 verification failed")
    if json.loads((P0 / "FREEZE.json").read_text())["freeze_sha256"] != PHASE0_FREEZE_SHA: raise R2Error("Phase 0 freeze sha differs")
    r1 = _load(R1D / "r1.py", "_r3_05_r1")
    pr = r1.verify(replay_too=False)
    if pr: raise R2Error(f"R1 verification failed: {pr[:3]}")
    fz = json.loads((R1D / "FREEZE.json").read_text())
    if fz["freeze_sha256"] != R1_FREEZE_SHA or fz["classification"] != "R3_05_R1_FIDELITY_PASS": raise R2Error("R1 freeze/classification differs")
    sm = json.loads((R1D / "STREAM_MANIFEST.json").read_text())
    if sm["canonical_stream_sha256"] != STREAM_SHA or (sm["events"], sm["passes"]) != (N_EVENTS, N_PASSES): raise R2Error("R1 stream manifest identity differs")
    src = json.loads((R1D / "SOURCE_MANIFEST.json").read_text())["sources"]
    if {v["token_digest"] for v in src.values()} != {TOKEN_DIGEST}: raise R2Error("token digest differs")
    return {"phase0_freeze_sha256": PHASE0_FREEZE_SHA, "r1_freeze_sha256": R1_FREEZE_SHA, "r1_classification": fz["classification"], "canonical_stream_sha256": STREAM_SHA, "events": N_EVENTS, "passes": N_PASSES, "token_digest": TOKEN_DIGEST}


def load_events(stream_path: Path | None = None) -> List[Dict[str, Any]]:
    p = stream_path or (R1D / "CANONICAL_STREAM.jsonl"); data = p.read_bytes()
    if sha_bytes(data) != STREAM_SHA: raise R2Error("wrong stream: sha256 differs from the frozen R1 canonical stream")
    ev = [json.loads(l) for l in data.splitlines()]
    if len(ev) != N_EVENTS: raise R2Error("event count")
    return ev


# ------------------------------------------------------------------ replay (integer accounting)
def new_belady(events: List[Dict[str, Any]]):
    """The historical argine.research.oracle.BeladyGlobal._access logic, driven directly by the canonical stream (no Trace object is needed)."""
    from argine.research.oracle import BeladyGlobal, next_uses
    b = BeladyGlobal.__new__(BeladyGlobal)
    b.events = [(e["i"], e["layer"], tuple(sorted(set(e["req"])))) for e in events]
    b.nu = next_uses(b.events)
    return b


def new_a(budget_key: str):
    import r2_12_real_trace_conformance as rtc
    d, iv = A_PARAMS[budget_key]
    return rtc._build_policy("b_selected", {"decay_factor": d, "aging_interval_steps": iv, "predictor_decay": 0, "top_k": 0, "n_experts": 0})


def replay_policy(events: List[Dict[str, Any]], pol, budget: int) -> Dict[str, Any]:
    from argine.research.policies import Context
    sizes = {e["layer"]: e["bytes"] for e in events}
    pol.start(Context(layers=sorted(sizes), n_experts=128, size=sizes, budget=budget, top_k=0))
    z = lambda: {"misses": 0, "hits": 0, "miss_bytes": 0, "evictions": 0}
    allp, dec = z(), z(); peak = viol = 0
    for e in events:
        ev0 = pol.evictions; ov0 = pol.overflow_misses
        missed = pol.access(e["i"], e["layer"], e["req"])
        u = len(set(e["req"])); m = len(missed); evs = pol.evictions - ev0
        peak = max(peak, pol.resident_bytes); viol += pol.resident_bytes > budget
        for d, on in ((allp, True), (dec, e["i"] >= FIRST_DECODE)):
            if on: d["misses"] += m; d["hits"] += u - m; d["miss_bytes"] += m * e["bytes"]; d["evictions"] += evs
    return {"all_pass": allp, "decode": dec, "resident_peak_bytes": peak, "budget_bytes": budget, "budget_violations": viol, "overflow_events": pol.overflow_events, "overflow_misses": pol.overflow_misses, "events": len(events)}


def run_one(arm: str, budget_key: str, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    pol = new_belady(events) if arm == "BELADY" else new_a(budget_key)
    r = replay_policy(events, pol, BUDGETS[budget_key]); r.update({"arm": arm, "budget": budget_key}); return r


# ------------------------------------------------------------------ derived numbers (exact integers -> Fractions)
def mech(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    A, B = a["decode"], b["decode"]
    out: Dict[str, Any] = {"decode_tokens": N_DECODE}
    for k in ("misses", "hits", "evictions", "miss_bytes"):
        out[f"A_decode_{k}"] = A[k]; out[f"BELADY_decode_{k}"] = B[k]
    out["misses_avoided_decode"] = A["misses"] - B["misses"]; out["miss_bytes_avoided_decode"] = A["miss_bytes"] - B["miss_bytes"]
    for nm, num, den in (("A_misses_per_token", A["misses"], N_DECODE), ("BELADY_misses_per_token", B["misses"], N_DECODE), ("misses_avoided_per_token", out["misses_avoided_decode"], N_DECODE),
                         ("A_miss_bytes_per_token", A["miss_bytes"], N_DECODE), ("BELADY_miss_bytes_per_token", B["miss_bytes"], N_DECODE), ("miss_bytes_avoided_per_token", out["miss_bytes_avoided_decode"], N_DECODE)):
        out[nm] = float(Fraction(num, den)); out[nm + "_exact"] = f"{num}/{den}"
    out["relative_miss_reduction_pct"] = float(Fraction(100 * out["misses_avoided_decode"], A["misses"])) if A["misses"] else 0.0
    out["relative_miss_byte_reduction_pct"] = float(Fraction(100 * out["miss_bytes_avoided_decode"], A["miss_bytes"])) if A["miss_bytes"] else 0.0
    return out


def k1_decide(value_ms: float, threshold: float = 10.0) -> str:
    return "R3_05_K1_KILL" if value_ms < threshold else "R3_05_K1_HEADROOM_PASS"


def economic(results: Dict[str, Any], kappa_low: float, kappa_point: float) -> Dict[str, Any]:
    out: Dict[str, Any] = {"kappa_low_ms_per_MB": kappa_low, "kappa_point_ms_per_MB_informational": kappa_point, "rule": "CLAIRVOYANT_VALUE_LOW = (A - BELADY decode miss bytes per token)/1e6 x kappa_low; kappa_low = 0.5 x the frozen Phase 0 two-point kappa", "budgets": {}}
    for b in ("1.5", "4", "8"):
        m = results["budgets"][b]["mechanism"]; mb = m["miss_bytes_avoided_per_token"]
        e = {"miss_bytes_avoided_per_token": mb, "kappa_estimable": KAPPA_ESTIMABLE[b]}
        if KAPPA_ESTIMABLE[b]:
            e["CLAIRVOYANT_VALUE_LOW_MS_PER_TOKEN"] = mb / 1e6 * kappa_low; e["clairvoyant_value_at_kappa_point_ms_informational"] = mb / 1e6 * kappa_point
        else:
            e["CLAIRVOYANT_VALUE_LOW_MS_PER_TOKEN"] = "NOT_ESTIMABLE"
            e["reason"] = "no measured A wall time at this budget in R3-04 (4 GiB removed by the efficiency amendment); kappa is a 1.5<->8 GiB association and is not validated here"
            e["descriptive_linear_value_unvalidated_ms"] = mb / 1e6 * kappa_low
        out["budgets"][b] = e
    return out


def historical(results: Dict[str, Any], p0_env: Dict[str, Any]) -> Dict[str, Any]:
    new = results["budgets"][PRIMARY]["mechanism"]["relative_miss_reduction_pct"]; prior = 100 * p0_env["derived"]["belady_fraction_of_b_selected_misses_removed_r2_coding_v1"]
    d = abs(new - prior)
    cls = "CONSISTENT" if d <= CONSISTENCY_PP["CONSISTENT"] else ("PARTIALLY_CONSISTENT" if d <= CONSISTENCY_PP["PARTIALLY_CONSISTENT"] else "INCONSISTENT")
    return {"prior_relative_miss_reduction_pct_R2_03_vs_R2_09A_b_selected": prior, "new_relative_miss_reduction_pct_decode": new, "abs_difference_pp": d, "classification": cls,
            "thresholds_pp_preregistered": CONSISTENCY_PP, "prior_ms_range_phase0_unconservative": [p0_env["derived"]["clairvoyant_ceiling_ms_prior_low"], p0_env["derived"]["clairvoyant_ceiling_ms_prior_point"]],
            "new_ms_range_same_kappas": [results.get("_ms_low"), results.get("_ms_point")],
            "not_apples_to_apples": "R2-03/R2-09A traces: a different generation (381 decode steps, 146,304 accesses); stream here: 512 decode tokens, 24,672 events, prefill state carried; Belady implementation is the same historical BeladyGlobal logic"}


# ------------------------------------------------------------------ CLI
def assemble(d: Path) -> Dict[str, Any]:
    runs = {(arm, b): json.loads((d / f"{arm}_{b}.json").read_text()) for arm in ("A", "BELADY") for b in BUDGETS}
    res: Dict[str, Any] = {"artifact": "R3-05-R2-RESULTS", "canonical_stream_sha256": STREAM_SHA, "primary_budget": PRIMARY, "budgets": {}}
    for b in BUDGETS:
        a, bel = runs[("A", b)], runs[("BELADY", b)]
        res["budgets"][b] = {"A": a, "BELADY": bel, "mechanism": mech(a, bel), "budget_violations_total": a["budget_violations"] + bel["budget_violations"],
                             "overflow_events": {"A": a["overflow_events"], "BELADY": bel["overflow_events"]}}
    return res


def r1_pp_reference() -> Dict[str, Any]:
    r = json.loads((R1D / "FIDELITY_RESULTS.json").read_text())["replay"]
    return {"A": r["A"]["replay"]["decode"], "PP": r["PP"]["replay"]["decode"]}


def finish(d: Path) -> Dict[str, Any]:
    inp = verify_inputs(); p0 = _load(P0 / "phase0.py", "_r3_05_p0"); env = p0.envelope()
    res = assemble(d); der = env["derived"]
    ref = r1_pp_reference(); m15 = res["budgets"][PRIMARY]["mechanism"]
    if any(ref["A"][k] != res["budgets"][PRIMARY]["A"]["decode"][k] for k in ("misses", "hits", "miss_bytes", "evictions")):
        raise R2Error("A replay at 1.5 GiB differs from the frozen R1 replay")
    pp_m = ref["A"]["misses"] - ref["PP"]["misses"]; pp_b = ref["A"]["miss_bytes"] - ref["PP"]["miss_bytes"]
    res["pp_capture_of_belady"] = {"pp_misses_avoided_decode": pp_m, "pp_miss_bytes_avoided_decode": pp_b, "belady_misses_avoided_decode": m15["misses_avoided_decode"], "belady_miss_bytes_avoided_decode": m15["miss_bytes_avoided_decode"],
                                   "fraction_of_miss_headroom": float(Fraction(pp_m, m15["misses_avoided_decode"])), "fraction_of_byte_headroom": float(Fraction(pp_b, m15["miss_bytes_avoided_decode"])),
                                   "exact": {"misses": f"{pp_m}/{m15['misses_avoided_decode']}", "bytes": f"{pp_b}/{m15['miss_bytes_avoided_decode']}"}}
    eco = economic(res, der["kappa_ms_per_MB_low"], der["kappa_ms_per_MB_point"])
    v = eco["budgets"][PRIMARY]["CLAIRVOYANT_VALUE_LOW_MS_PER_TOKEN"]
    res["_ms_low"] = v; res["_ms_point"] = eco["budgets"][PRIMARY]["clairvoyant_value_at_kappa_point_ms_informational"]
    hist = historical(res, env); res.pop("_ms_low"); res.pop("_ms_point")
    k1 = {"artifact": "R3-05-R2-K1-DECISION", "rule": "K1 (frozen Phase 0): clairvoyant bytes avoided x kappa_low < 10 ms/token at 1.5 GiB -> kill predictive retention as the primary R3 path", "threshold_ms_per_token": 10.0,
          "primary_budget": PRIMARY, "value_ms_per_token": v, "classification": k1_decide(v), "inputs": inp,
          "interpretation": "HEADROOM_PASS means only that enough theoretical future-retention value exists to justify testing causal candidates; it does not predict that any candidate works." if k1_decide(v) == "R3_05_K1_HEADROOM_PASS"
          else "even perfect future knowledge does not give enough conservative economic value under the tested regime", "r3_may_proceed": k1_decide(v) == "R3_05_K1_HEADROOM_PASS"}
    return {"results": res, "economic": eco, "historical": hist, "k1": k1, "inputs": inp}


def build(d: Path) -> str:
    f = finish(d)
    (HERE / "RESULTS.json").write_text(dumps({**f["results"], "historical_consistency": f["historical"]}), encoding="utf-8")
    (HERE / "ECONOMIC_HEADROOM.json").write_text(dumps(f["economic"]), encoding="utf-8"); (HERE / "K1_DECISION.json").write_text(dumps(f["k1"]), encoding="utf-8")
    (HERE / "SOURCE_IDENTITY.json").write_text(dumps({"artifact": "R3-05-R2-SOURCE-IDENTITY", **f["inputs"], "stream_file": "experiments/R3-05-economic-retention/r1-fidelity/CANONICAL_STREAM.jsonl"}), encoding="utf-8")
    fz = {"artifact": "R3-05-R2-FREEZE", "classification": f["k1"]["classification"], "files_sha256": {n: sha_bytes((HERE / n).read_bytes()) for n in FILES}, **f["inputs"],
          "authorizes": "R3 (CES, PP_CURVE) only if classification is R3_05_K1_HEADROOM_PASS and the user issues the R3 prompt; nothing model-bearing."}
    fz["freeze_sha256"] = freeze_sha(fz); (HERE / "FREEZE.json").write_text(dumps(fz), encoding="utf-8"); return fz["freeze_sha256"]


def verify(replay_too: bool = False) -> List[str]:
    p: List[str] = []
    try:
        verify_inputs()
    except R2Error as e:
        return [str(e)]
    fz = json.loads((HERE / "FREEZE.json").read_text())
    if fz["freeze_sha256"] != freeze_sha(fz): p.append("freeze sha mismatch")
    for n, h in fz["files_sha256"].items():
        if sha_bytes((HERE / n).read_bytes()) != h: p.append(f"{n} differs from freeze")
    if any(s in "".join((HERE / n).read_text(encoding="utf-8") for n in FILES[:7]) for s in ("alpel", "C:\\\\")): p.append("host path leaked")
    res = json.loads((HERE / "RESULTS.json").read_text()); eco = json.loads((HERE / "ECONOMIC_HEADROOM.json").read_text()); k1 = json.loads((HERE / "K1_DECISION.json").read_text())
    if res["canonical_stream_sha256"] != STREAM_SHA: p.append("results bound to a different stream")
    for b, r in res["budgets"].items():
        if mech(r["A"], r["BELADY"]) != r["mechanism"]: p.append(f"mechanism numbers at {b} do not recompute from the integer counters")
        if r["budget_violations_total"] != 0: p.append(f"budget violations at {b}")
    p0 = _load(P0 / "phase0.py", "_r3_05_p0"); der = p0.envelope()["derived"]
    if economic(res, der["kappa_ms_per_MB_low"], der["kappa_ms_per_MB_point"]) != eco: p.append("economic headroom does not recompute")
    v = eco["budgets"][PRIMARY]["CLAIRVOYANT_VALUE_LOW_MS_PER_TOKEN"]
    if k1["classification"] != k1_decide(v) or k1["value_ms_per_token"] != v or k1["threshold_ms_per_token"] != 10.0: p.append("K1 decision does not recompute")
    if replay_too:
        ev = load_events()
        for arm in ("A", "BELADY"):
            r = run_one(arm, PRIMARY, ev)
            if r != res["budgets"][PRIMARY][arm]: p.append(f"re-run of {arm} at 1.5 GiB differs from the recorded result")
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("args", nargs="*"); ap.add_argument("--replay", action="store_true"); a = ap.parse_args()
    if a.cmd == "run":
        arm, b, out = a.args[0], a.args[1], Path(a.args[2]); verify_inputs(); r = run_one(arm, b, load_events()); out.write_text(dumps(r)); print(arm, b, r["decode"]["misses"], r["decode"]["miss_bytes"], r["budget_violations"])
    elif a.cmd == "finish":
        f = finish(Path(a.args[0])); print(f["k1"]["classification"], f["k1"]["value_ms_per_token"])
    elif a.cmd == "build":
        print("freeze", build(Path(a.args[0])))
    elif a.cmd == "verify":
        pr = verify(a.replay); print("VERIFY", "OK" if not pr else pr); sys.exit(1 if pr else 0)
