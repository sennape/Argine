"""Research 3 final scientific closure: binds and re-verifies the frozen R3 evidence chain. No new research, no model, no runtime change.

  python closure.py build     write EVIDENCE_CHAIN.json, FINAL_RESULT.json, RESEARCH_3_CLOSURE.json, FREEZE.json
  python closure.py verify    re-verify every bound freeze, its commit, the key numbers and the claim boundaries (fails closed)
"""
from __future__ import annotations

import hashlib, importlib.util, json, subprocess, sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EXP = REPO / "experiments"

# (stage, evidence dir relative to repo, commit that froze it, expected freeze_sha256 or None, expected classification)
CHAIN = [
    ("R3-03A", "experiments/R3-03A-runtime-semantic-conformance", "3c37870c2b1ed08609f61cee05674b38960be3a7", None, "R3_03A_RUNTIME_SEMANTIC_CONFORMANCE_PASS"),
    ("R3-03B", "experiments/R3-03B-final-evidence", "46cab36cf4777856b658ad776a537df0bede2be3", "1846b00954e033bc9a4cb136cd05373a411d7a8de699923235f31dbfd2f53448", "R3_03B_EXPLAINED"),
    ("R3-04-final-evidence", "experiments/R3-04-final-evidence", "0f7242798cea2e45d0cd9c5d89653327583d19b2", "71fca58e65853c325ba7622d7565e51be85a54f46394de8d4f4ee2e0753adfa2", "NET_LOSS"),
    ("R3-05-Phase0", "experiments/R3-05-economic-retention", "402cf6a515a12377a55dfae6aecf44607cd41410", "415f78a850a2917a2457797106589433f944c07a863f80b14cef88fde6232ae3", None),
    ("R3-05-R1", "experiments/R3-05-economic-retention/r1-fidelity", "12e3347321ba806049825b131eed6523ac82bd5b", "8d025fdfb3d2a48c54e4d905f3a0e3aad2dd70ed9fdabf602b21ec2b600c3382", "R3_05_R1_FIDELITY_PASS"),
    ("R3-05-R2", "experiments/R3-05-economic-retention/r2-belady-ceiling", "fc8e6313c3b7d6a9231b29b51c06fa4b29dc2728", "8b22e41505e3c14b78b6c924b703f1da4b26643123a9240f7bd9ad7d232afa44", "R3_05_K1_HEADROOM_PASS"),
    ("R3-05-R3", "experiments/R3-05-economic-retention/r3-ces-pp-curve", "f6e77806e51565075eb6d095b707c7772d9077ce", "1993adfa510bc792878fadf26ff3044fb5d7cc1c88d8ab37211271c19266e7b6", "R3_05_K2_RETIRE_CURRENT_PP"),
    ("R3-05-R4", "experiments/R3-05-economic-retention/r4-nue-candidate-gate", "612900731b4dcce79fd9ee18dc3d30b866deb286", "3f52a06eeec6f581be8d0659071b0de0dc9412849de3d10b6bd175cf8231b478", "R3_05_PREDICTIVE_RETENTION_KILLED"),
]
R3_04_CLOSURE = ("experiments/R3-04-closure/CLOSURE.json", "91aa9b48a317934fcd985d63ca6e4bcab7ee9741", "R3_04_CLOSED_NET_LOSS")

FINAL_CLASSIFICATION = "R3_CLOSED_NEGATIVE_PREDICTIVE_RETENTION_NOT_VIABLE_IN_TESTED_REGIME"
UNTESTED = {
    "retention": ["oracle-distilled causal eviction", "general economic eviction rules", "true admission / bypass policies"],
    "non_retention": ["prefetch", "compute / I/O overlap", "scheduling", "adaptive allocation", "general policy switching / adaptive control"],
}
OLD_STAGES = ["R3-06", "R3-07", "R3-08", "R3-09", "R3-10", "R3-11"]
SUPERSEDED = "SUPERSEDED_BY_NEGATIVE_R3_05_RESULT"
FORBIDDEN_CLAIMS = ["expert usage is unpredictable", "Research 2 was wrong", "all retention policies are impossible", "Belady headroom is causally reachable",
                    "all memory-management approaches fail", "all predictive caching fails universally", "other models or hardware would behave identically"]
DOCS = ["README.md", "CLAIM_BOUNDARIES.md", "UNTESTED_DIRECTIONS.md", "ROADMAP_IMPACT.md"]
OUTPUTS = ["EVIDENCE_CHAIN.json", "FINAL_RESULT.json", "RESEARCH_3_CLOSURE.json"]
FILES = DOCS + OUTPUTS + ["closure.py", "tests/test_closure.py"]


class ClosureError(Exception):
    pass


def sha(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True) + "\n"
def freeze_sha(o: Dict[str, Any]) -> str: return sha(json.dumps({k: v for k, v in o.items() if k != "freeze_sha256"}, sort_keys=True, separators=(",", ":")).encode())
def jl(rel: str) -> Any: return json.loads((REPO / rel).read_text(encoding="utf-8"))


def git_blob(commit: str, rel: str) -> bytes:
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{commit}:{rel}"], capture_output=True, check=False)   # list args, no shell
    if r.returncode != 0: raise ClosureError(f"{rel} not present at commit {commit}")
    return r.stdout


def check_freeze(stage: str, d: str, commit: str, fz_expected, cls_expected) -> Dict[str, Any]:
    p = REPO / d / "FREEZE.json"; raw = p.read_bytes(); fz = json.loads(raw)
    if git_blob(commit, f"{d}/FREEZE.json") != raw: raise ClosureError(f"{stage}: FREEZE.json differs from the copy committed at {commit}")
    if fz_expected is not None:
        if fz.get("freeze_sha256") != fz_expected or freeze_sha(fz) != fz_expected: raise ClosureError(f"{stage}: freeze_sha256 mismatch")
    for name, h in fz.get("files_sha256", {}).items():
        f = next((c for c in (REPO / d / name, REPO / name) if c.exists()), None)
        if f is None or sha(f.read_bytes()) != h: raise ClosureError(f"{stage}: bound file {name} missing or changed")
    for key, name in (("final_result_sha256", "FINAL_RESULT.json"), ("validation_manifest_sha256", "VALIDATION_MANIFEST.json")):
        if key in fz and sha((REPO / d / name).read_bytes()) != fz[key]: raise ClosureError(f"{stage}: {name} changed")
    cls = fz.get("classification") or fz.get("overall") or fz.get("k2")
    if stage == "R3-04-final-evidence": cls = jl("experiments/R3-04-final-evidence/OFFICIAL_ANALYSIS.json").get("primary_classification", jl(R3_04_CLOSURE[0])["primary_classification"])
    if cls_expected is not None and cls != cls_expected: raise ClosureError(f"{stage}: classification {cls} != {cls_expected}")
    return {"stage": stage, "dir": d, "commit": commit, "freeze_sha256": fz.get("freeze_sha256"), "freeze_file_sha256": sha(raw), "bound_files": len(fz.get("files_sha256", {})), "classification": cls}


def key_numbers() -> Dict[str, Any]:
    c = jl(R3_04_CLOSURE[0]); pr = c["primary_1.5GiB"]; arm = pr["arm_stats"]
    a3 = jl("experiments/R3-03A-runtime-semantic-conformance/CONFORMANCE_RESULT.json")["event_counts"]
    b3 = jl("experiments/R3-03B-final-evidence/FINAL_RESULT.json")
    r2 = jl("experiments/R3-05-economic-retention/r2-belady-ceiling/RESULTS.json")["budgets"]["1.5"]
    k2 = jl("experiments/R3-05-economic-retention/r3-ces-pp-curve/K2_DECISION.json")
    k3 = jl("experiments/R3-05-economic-retention/r3-ces-pp-curve/K3_DECISION.json")
    dt = jl("experiments/R3-05-economic-retention/r3-ces-pp-curve/DECISION_TABLE.json")["rows"]
    hc = jl("experiments/R3-05-economic-retention/r4-nue-candidate-gate/HEADROOM_CAPTURE.json")
    k4 = jl("experiments/R3-05-economic-retention/r4-nue-candidate-gate/K4_DECISION.json")
    k5 = jl("experiments/R3-05-economic-retention/r4-nue-candidate-gate/K5_DECISION.json")
    am, abytes = r2["A"]["decode"]["misses"], r2["A"]["decode"]["miss_bytes"]
    bm, bb = r2["mechanism"]["misses_avoided_decode"], r2["mechanism"]["miss_bytes_avoided_decode"]
    return {
        "R3_03A": {"old_runtime_policy_calls_per_decode_token": a3["old_runtime_policy_calls_per_decode_token"], "corrected_policy_calls_per_decode_token": a3["policy_calls_per_decode_token"],
                   "old_over_corrected": a3["old_runtime_policy_calls_per_decode_token"] / a3["policy_calls_per_decode_token"], "passes": a3["passes"], "policy_event_rows_per_launch": a3["policy_event_rows_per_launch"]},
        "R3_03B": {"classification": b3["classification"], "rho": b3["rho"], "fieller_95": [b3["fieller"]["lo"], b3["fieller"]["hi"]], "equivalence_interval": b3["equivalence_interval"], "n_blocks": b3["n_blocks"]},
        "R3_04": {"status": c["status"], "A_ms_per_token": arm["A"]["ms_per_token"]["mean"], "S_ms_per_token": arm["S"]["ms_per_token"]["mean"], "P_ms_per_token": arm["P"]["ms_per_token"]["mean"],
                  "A_tokens_per_second": arm["A"]["tokens_per_second"]["mean"], "P_minus_A_ms_per_token": pr["P_minus_A_ms_per_token"]["mean"],
                  "P_minus_A_ci95": [pr["P_minus_A_ms_per_token"]["lo"], pr["P_minus_A_ms_per_token"]["hi"]], "S_minus_A_ms_per_token": pr["S_minus_A"]["mean"],
                  "P_minus_S_ms_per_token": pr["P_minus_S"]["mean"], "P_minus_S_ci95": [pr["P_minus_S"]["lo"], pr["P_minus_S"]["hi"]],
                  "throughput_change_P_vs_A_pct": 100 * (arm["P"]["tokens_per_second"]["mean"] / arm["A"]["tokens_per_second"]["mean"] - 1), "n_blocks": pr["P_minus_A_ms_per_token"]["n_blocks"]},
        "R3_05": {"A_misses_per_token": am / 512, "belady_misses_per_token": (am - bm) / 512, "belady_miss_reduction_pct": 100 * bm / am, "belady_byte_reduction_pct": 100 * bb / abytes,
                  "belady_conservative_ceiling_ms_per_token": hc["conservative_ceiling_ms_per_token"],
                  "K2": k2["classification"], "CES_over_PP_byte_ratio": k2["ratio"], "K3": k3["classification"],
                  "PP_fraction_of_belady_byte_headroom": dt["PP N=1"]["fraction_of_belady_headroom"]["miss_bytes"], "CES_fraction_of_belady_byte_headroom": dt["CES"]["fraction_of_belady_headroom"]["miss_bytes"],
                  "NUE_LO_fraction_of_belady_byte_headroom": hc["captured"]["NUE_LO"]["fraction_of_belady_byte_headroom"], "NUE_X_fraction_of_belady_byte_headroom": hc["captured"]["NUE_X"]["fraction_of_belady_byte_headroom"],
                  "NUE_LO_gross_ms_per_token": hc["captured"]["NUE_LO"]["ms_per_token"], "NUE_X_gross_ms_per_token": hc["captured"]["NUE_X"]["ms_per_token"],
                  "K4_threshold_ms_per_token": k4["per_candidate"]["NUE_X"]["threshold_ms_per_token"], "K4_NUE_LO": k4["per_candidate"]["NUE_LO"]["classification"], "K4_NUE_X": k4["per_candidate"]["NUE_X"]["classification"],
                  "K5": k5["classification"], "final": k4["overall"]},
    }


# claims stated in the prompt and README; each must recompute from the frozen artifacts (rounded as stated)
STATED = [("R3_03A", "old_over_corrected", 3.0, 1e-9), ("R3_03B", "rho", 0.9024, 5e-5), ("R3_04", "A_ms_per_token", 526.97, 5e-3), ("R3_04", "S_ms_per_token", 541.56, 5e-3),
          ("R3_04", "P_ms_per_token", 542.80, 5e-3), ("R3_04", "A_tokens_per_second", 1.8992, 5e-5), ("R3_04", "P_minus_A_ms_per_token", 15.827, 5e-4),
          ("R3_04", "throughput_change_P_vs_A_pct", -2.93, 5e-3), ("R3_05", "A_misses_per_token", 207.990234375, 0), ("R3_05", "belady_misses_per_token", 123.39453125, 0),
          ("R3_05", "belady_miss_reduction_pct", 40.673, 5e-4), ("R3_05", "belady_byte_reduction_pct", 40.653, 5e-4), ("R3_05", "belady_conservative_ceiling_ms_per_token", 92.216, 5e-4),
          ("R3_05", "PP_fraction_of_belady_byte_headroom", 0.00230, 5e-6), ("R3_05", "CES_fraction_of_belady_byte_headroom", 0.00559, 5e-6),
          ("R3_05", "NUE_LO_fraction_of_belady_byte_headroom", 0.0478, 5e-5), ("R3_05", "NUE_X_fraction_of_belady_byte_headroom", 0.0879, 5e-5),
          ("R3_05", "NUE_LO_gross_ms_per_token", 4.41, 5e-3), ("R3_05", "NUE_X_gross_ms_per_token", 8.11, 5e-3), ("R3_05", "K4_threshold_ms_per_token", 10.0, 0)]
STATED_LABELS = [("R3_03B", "classification", "R3_03B_EXPLAINED"), ("R3_04", "status", "R3_04_CLOSED_NET_LOSS"), ("R3_05", "K2", "R3_05_K2_RETIRE_CURRENT_PP"),
                 ("R3_05", "K3", "R3_05_K3_FREQUENCY_FAMILY_SURVIVES"), ("R3_05", "K4_NUE_LO", "K4_FAIL_INFEASIBLE"), ("R3_05", "K4_NUE_X", "K4_FAIL_INFEASIBLE"), ("R3_05", "final", "R3_05_PREDICTIVE_RETENTION_KILLED")]


def check_numbers(kn: Dict[str, Any]) -> List[str]:
    p = []
    for sec, k, v, tol in STATED:
        if abs(kn[sec][k] - v) > tol: p.append(f"stated {sec}.{k}={v} does not recompute ({kn[sec][k]})")
    for sec, k, v in STATED_LABELS:
        if kn[sec][k] != v: p.append(f"label {sec}.{k} != {v}")
    lo, hi = kn["R3_04"]["P_minus_A_ci95"]
    if not (abs(lo - 10.46) < 5e-3 and abs(hi - 21.19) < 5e-3 and lo > 0): p.append("R3-04 CI does not recompute / does not exclude 0")
    if not (kn["R3_05"]["NUE_X_gross_ms_per_token"] < kn["R3_05"]["K4_threshold_ms_per_token"] and kn["R3_05"]["NUE_LO_gross_ms_per_token"] < kn["R3_05"]["K4_threshold_ms_per_token"]): p.append("K4 failure does not recompute")
    if not (kn["R3_05"]["CES_fraction_of_belady_byte_headroom"] > kn["R3_05"]["PP_fraction_of_belady_byte_headroom"]): p.append("CES>PP does not recompute")
    return p


def nested_verify() -> List[str]:
    spec = importlib.util.spec_from_file_location("_r3_05_r4", EXP / "R3-05-economic-retention/r4-nue-candidate-gate/r4.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)   # type: ignore
    return [f"R3-05 nested verify: {x}" for x in m.verify(replay_too=False)]     # covers Phase 0, R1, R2, R3, R4 recomputation


def check_docs() -> List[str]:
    p = []
    text = {n: (HERE / n).read_text(encoding="utf-8") for n in DOCS}
    cb = text["CLAIM_BOUNDARIES.md"]
    for c in FORBIDDEN_CLAIMS:
        if c not in cb: p.append(f"CLAIM_BOUNDARIES.md does not explicitly disclaim: {c}")
    ut = text["UNTESTED_DIRECTIONS.md"]
    for d in UNTESTED["retention"] + UNTESTED["non_retention"]:
        if d not in ut: p.append(f"untested direction missing: {d}")
    if "NOT_TESTED" not in ut or "FAILED" in ut.replace("NOT presented as FAILED", ""): p.append("untested directions must be NOT_TESTED, never FAILED")
    rm = text["ROADMAP_IMPACT.md"]
    for s in OLD_STAGES:
        if f"{s}: {SUPERSEDED}" not in rm: p.append(f"roadmap status missing for {s}")
    if any("alpel" in t or "C:\\Users" in t for t in text.values()): p.append("host path in closure docs")
    return p


def build() -> str:
    chain = [check_freeze(*c) for c in CHAIN]
    cl = REPO / R3_04_CLOSURE[0]
    if git_blob(R3_04_CLOSURE[1], R3_04_CLOSURE[0]) != cl.read_bytes(): raise ClosureError("R3-04 CLOSURE.json differs from its commit")
    kn = key_numbers(); p = check_numbers(kn) + nested_verify() + check_docs()
    if p: raise ClosureError(f"closure refused: {p}")
    ev = {"artifact": "R3-CLOSURE-EVIDENCE-CHAIN", "stages": chain, "r3_04_closure": {"path": R3_04_CLOSURE[0], "commit": R3_04_CLOSURE[1], "file_sha256": sha(cl.read_bytes()), "status": R3_04_CLOSURE[2]},
          "key_numbers": kn, "verification": "each FREEZE.json is byte-identical to its committed copy; freeze_sha256 recomputes; every bound file hash matches; R3-05 numbers recompute via the R4 validator (which recomputes Phase 0/R1/R2/R3)"}
    fr = {"artifact": "R3-FINAL-RESULT", "classification": FINAL_CLASSIFICATION, "research_3_closed": True,
          "central_question": "Can the predictive expert information established in Research 2 be converted into useful real-runtime retention value?",
          "central_answer": "No, within the tested regime: corrected predictive retention produced a real runtime net loss (R3-04), and the best causal predictive retention family (NUE_X) failed the prospectively frozen viability threshold even before its computation cost was charged (R3-05).",
          "strongest_defensible_claim": ("Predictive structure in MoE expert routing does not necessarily translate into economically useful retention control. In the tested regime (Qwen3-30B-A3B Q4_K_M, the tested host, 1.5 GiB primary expert budget, the R3-04 workload stream), "
                                         "correcting semantic policy-event placement, isolating predictor cost, and replacing the original predicted_protect mechanism with a causal next-use predictive eviction policy still failed a prospectively defined viability threshold "
                                         "(10 ms/token at a conservative byte-to-time conversion) before candidate computation cost was charged."),
          "universal_claim_allowed": False, "r2_prediction_results_still_valid": True, "causally_recoverable_belady_headroom_known": False,
          "pp_status": "RETIRED (R3-04 net loss; R3-05 K2: a zero-cost current-event shield beats it)", "nue_family_status": "CLOSED (R3-05 K4: NUE_LO and NUE_X infeasible at zero cost)",
          "stage_status": {"R3-03A": "CLOSED / PASS", "R3-03B": "CLOSED / R3_03B_EXPLAINED", "R3-04": "CLOSED / R3_04_CLOSED_NET_LOSS", "R3-05": "CLOSED / R3_05_PREDICTIVE_RETENTION_KILLED"}, "key_numbers": kn}
    rc = {"artifact": "RESEARCH-3-CLOSURE", "status": "CLOSED", "classification": FINAL_CLASSIFICATION, "scope": "whether R2 predictive expert information can be converted into real-runtime retention value (not a general search over memory-management policies)",
          "untested_directions": {k: {d: "NOT_TESTED" for d in v} for k, v in UNTESTED.items()}, "old_roadmap": {s: SUPERSEDED for s in OLD_STAGES},
          "old_roadmap_note": "R3-06..R3-11 were nominal plan stages that assumed R3-05 would nominate a viable predictive policy; no definitions exist in the repository (R3-05 DECISION_REPORT.md section 12). None is executed.",
          "research_4_handoff": {"question": "How much of the clairvoyant memory-management optimum can be recovered by a cheap causal policy using only information available at decision time?",
                                 "possible_mechanisms": ["oracle-distilled eviction", "admission", "economic memory decisions"], "status": "NOT OPENED", "framing": "a new research question; not an extension or rescue of R3 predictive retention"},
          "model_runs_for_closure": 0, "runtime_modified_for_closure": False}
    for n, o in (("EVIDENCE_CHAIN.json", ev), ("FINAL_RESULT.json", fr), ("RESEARCH_3_CLOSURE.json", rc)):
        (HERE / n).write_text(dumps(o), encoding="utf-8")
    fz = {"artifact": "R3-CLOSURE-FREEZE", "classification": FINAL_CLASSIFICATION, "files_sha256": {n: sha((HERE / n).read_bytes()) for n in FILES},
          "bound_freezes": {c[0]: {"commit": c[2], "freeze_sha256": c[3]} for c in CHAIN}, "r3_04_closure_commit": R3_04_CLOSURE[1],
          "authorizes": "Nothing. Research 3 is closed; Research 4 is not opened."}
    fz["freeze_sha256"] = freeze_sha(fz); (HERE / "FREEZE.json").write_text(dumps(fz), encoding="utf-8"); return fz["freeze_sha256"]


def verify() -> List[str]:
    p: List[str] = []
    try:
        for c in CHAIN: check_freeze(*c)
        if git_blob(R3_04_CLOSURE[1], R3_04_CLOSURE[0]) != (REPO / R3_04_CLOSURE[0]).read_bytes(): p.append("R3-04 CLOSURE.json differs from its commit")
    except ClosureError as e:
        return [str(e)]
    fz = json.loads((HERE / "FREEZE.json").read_text())
    if fz["freeze_sha256"] != freeze_sha(fz): p.append("closure freeze sha mismatch")
    for n, h in fz["files_sha256"].items():
        if sha((HERE / n).read_bytes()) != h: p.append(f"{n} differs from freeze")
    kn = key_numbers(); p += check_numbers(kn)
    if json.loads((HERE / "EVIDENCE_CHAIN.json").read_text())["key_numbers"] != kn: p.append("recorded key numbers differ from the frozen artifacts")
    fr = json.loads((HERE / "FINAL_RESULT.json").read_text())
    if fr["universal_claim_allowed"] or fr["causally_recoverable_belady_headroom_known"] or not fr["r2_prediction_results_still_valid"]: p.append("FINAL_RESULT claim boundary flags invalid")
    rc = json.loads((HERE / "RESEARCH_3_CLOSURE.json").read_text())
    if rc["research_4_handoff"]["status"] != "NOT OPENED" or any(v != SUPERSEDED for v in rc["old_roadmap"].values()): p.append("closure record invalid")
    p += nested_verify() + check_docs()
    return p


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "build": print("freeze", build())
    else:
        pr = verify(); print("VERIFY", "OK" if not pr else pr); sys.exit(1 if pr else 0)
