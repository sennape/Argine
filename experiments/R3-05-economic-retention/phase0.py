"""R3-05 Phase 0: economic envelope generator, executable decision rules, offline verifier. No model, no network, no runtime.

  python phase0.py --build     recompute ECONOMIC_ENVELOPE.json from hashed frozen sources, then write FREEZE.json
  python phase0.py --verify    offline: re-hash sources, recompute the envelope, check PROTOCOL.json, check the freeze

Every number in the envelope is DERIVED from frozen repository artifacts (paths + sha256 recorded); none is typed in.
"""
from __future__ import annotations

import argparse, csv, hashlib, importlib.util, json, math, re, sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

SOURCES = {
    "r3_04_arm_statistics": "experiments/R3-04-final-evidence/ARM_STATISTICS.json",
    "r3_04_official_analysis": "experiments/R3-04-final-evidence/OFFICIAL_ANALYSIS.json",
    "r3_04_final_freeze": "experiments/R3-04-final-evidence/FREEZE.json",
    "r3_04_claim_boundaries": "experiments/R3-04-net-runtime-value/CLAIM_BOUNDARIES.md",
    "gate2_mechanism_metrics": "experiments/R3-04-net-runtime-value/gate2-mechanism-evidence/MECHANISM_METRICS.json",
    "r3_03b_microbench": "experiments/R3-03B-microbench-evidence/MICROBENCH_RESULT.json",
    "r2_09a_tournament": "experiments/R2-09-prediction-benchmark/r2_09a/tournament_results.csv",
    "r2_03_sweep_table": "paper/research2/tables/table3_r2_03_sweep.tex",
    "retention_policy_cpp": "patches/argine-retention-policy.cpp",
    "retention_policy_h": "patches/argine-retention-policy.h",
}
R3_04_FINAL_FREEZE = "71fca58e65853c325ba7622d7565e51be85a54f46394de8d4f4ee2e0753adfa2"
FROZEN_FILES = ["PROTOCOL.json", "ECONOMIC_ENVELOPE.json", "COST_MAP.json", "DECISION_REPORT.md", "CLAIM_BOUNDARIES.md", "phase0.py", "tests/test_phase0.py"]


def sha(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True) + "\n"
def freeze_sha(o: Dict[str, Any]) -> str: return sha(json.dumps({k: v for k, v in o.items() if k != "freeze_sha256"}, sort_keys=True, separators=(",", ":")).encode())
def src(k: str) -> Path: return REPO / SOURCES[k]
def jload(k: str) -> Any: return json.loads(src(k).read_text(encoding="utf-8"))


def t975(df: int) -> float:
    p = REPO / "experiments" / "R3-03B-final-evidence" / "validate_final.py"
    spec = importlib.util.spec_from_file_location("_r3_03b_final_validate", p); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)  # type: ignore
    return m.t975(df)


def protocol() -> Dict[str, Any]: return json.loads((HERE / "PROTOCOL.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ envelope (derived only)
def envelope() -> Dict[str, Any]:
    P = protocol()["constants"]
    arm, off, g2, mb = jload("r3_04_arm_statistics"), jload("r3_04_official_analysis"), jload("gate2_mechanism_metrics"), jload("r3_03b_microbench")
    if jload("r3_04_final_freeze")["freeze_sha256"] != R3_04_FINAL_FREEZE:
        raise ValueError("R3-04 final evidence freeze differs from the frozen value")
    w15 = off["budgets"]["1.5"]["wall_ms_per_token"]
    C0, C0_lo, C0_hi = w15["DELTA_COST"]["mean"], w15["DELTA_COST"]["lo"], w15["DELTA_COST"]["hi"]
    sd_net = w15["DELTA_NET"]["sd"]
    A15, A8 = arm["1.5_A"]["ms_per_token"]["mean"], arm["8_A"]["ms_per_token"]["mean"]
    L = {(r["arm"], r["budget"], r["policy_timing"]): r for r in g2["per_launch"]}
    mbA15, mbA8, mbP15 = L[("A", "1.5", "ON")]["decode_miss_bytes_per_token"], L[("A", "8", "ON")]["decode_miss_bytes_per_token"], L[("P", "1.5", "ON")]["decode_miss_bytes_per_token"]
    missA15 = L[("A", "1.5", "ON")]["decode_misses_per_token"]
    nsA, nsS = L[("A", "1.5", "ON")]["policy_ns_per_call"], L[("S", "1.5", "ON")]["policy_ns_per_call"]
    pol_A, pol_S = L[("A", "1.5", "ON")]["policy_ms_per_token_ns_total_over_512"], L[("S", "1.5", "ON")]["policy_ms_per_token_ns_total_over_512"]
    events_per_token = round((pol_S * 1e6) / nsS)
    c = mb["coefficients"]["c_call_ns"]; mb_bsel, mb_sham, mb_pred = c["b_selected"]["mean"], c["predicted_protect_sham"]["mean"], mb["coefficients"]["c_predictor"]["ns_per_call"]
    m = re.search(r"<= ~([\d.]+) ms/token at 1\.5 GiB", src("r3_04_claim_boundaries").read_text(encoding="utf-8")); B_ceiling = float(m.group(1))
    rows = list(csv.DictReader(src("r2_09a_tournament").open(encoding="utf-8")))
    bsel = [r for r in rows if r["budget_gib"] == "1.5" and r["dev_instance_id"] == "coding-v1__seed-42" and r["policy_config_id"] == "aged_frequency/decay=0.999,interval=1"][0]
    lru = [r for r in rows if r["budget_gib"] == "1.5" and r["dev_instance_id"] == "coding-v1__seed-42" and r["policy_family"] == "global_lru"][0]
    tex = re.search(r"^1\.5 & ([\d,]+) & ([\d,]+) &", src("r2_03_sweep_table").read_text(encoding="utf-8"), re.M)
    lru_tex, belady = int(tex.group(1).replace(",", "")), int(tex.group(2).replace(",", ""))
    if lru_tex != int(lru["misses"]):
        raise ValueError("R2-03 table and R2-09A tournament disagree on the Global-LRU trace identity")

    kappa = (A15 - A8) / ((mbA15 - mbA8) / 1e6)                       # ms per MB of decode miss bytes (two-point budget slope; association, not causal)
    kappa_low = kappa * P["kappa_low_fraction"]
    B_P = (mbA15 - mbP15) / 1e6 * kappa
    belady_frac = 1 - belady / int(bsel["misses"])
    ceiling_ms = belady_frac * mbA15 / 1e6 * kappa
    margin = P["success_net_margin_ms_per_token"]

    inv = {}
    for f in P["invocation_fractions"]:
        keep = min(1.0, B_ceiling / (f * C0))
        inv[str(f)] = {"if_benefit_fully_retained": {"max_per_call_cost_fraction": keep, "required_per_call_cost_reduction": 1 - keep},
                       "if_benefit_proportional_to_f": {"required_per_call_cost_reduction": 1 - B_ceiling / C0}}
    ben = {str(r): {"policy_cost_ms": C0 / r, "required_benefit_ms": C0 / r, "required_benefit_multiple_of_ceiling": C0 / (r * B_ceiling)} for r in P["cost_reduction_factors"]}
    mat = {}
    for r in P["matrix_cost_reduction_factors"]:
        for mm in P["matrix_benefit_multiples"]:
            b, cc = mm * B_ceiling, C0 / r; net = b - cc
            mat[f"cost/{r}|benefit x{mm}"] = {"net_ms": net, "ratio": b / cc, "label": "WIN_WITH_MARGIN" if (net >= margin and b / cc >= P["success_benefit_cost_ratio"]) else ("WIN" if net > 0 else "LOSS")}
    det = {str(n): t975(n - 1) * sd_net / math.sqrt(n) for n in (6, 12, 18, 36)}
    return {
        "artifact": "R3-05-ECONOMIC-ENVELOPE",
        "inputs": {"C0_wall_cost_ms": C0, "C0_ci": [C0_lo, C0_hi], "A_1.5_ms": A15, "A_8_ms": A8, "A_1.5_decode_miss_bytes_per_token": mbA15, "A_8_decode_miss_bytes_per_token": mbA8,
                   "P_1.5_decode_miss_bytes_per_token": mbP15, "A_1.5_decode_misses_per_token": missA15, "runtime_ns_per_event": {"A": nsA, "S": nsS}, "policy_ms_per_token": {"A": pol_A, "S": pol_S},
                   "events_per_token": events_per_token, "microbench_ns_per_call": {"b_selected": mb_bsel, "sham": mb_sham, "predictor_component": mb_pred},
                   "B_ceiling_ms_frozen_R3_04": B_ceiling, "r2_coding_v1_1.5GiB_misses": {"global_lru": int(lru["misses"]), "b_selected": int(bsel["misses"]), "belady": belady},
                   "R3_04_DELTA_NET_block_sd_ms": sd_net},
        "derived": {
            "kappa_ms_per_MB_point": kappa, "kappa_ms_per_MB_low": kappa_low, "ms_per_average_miss_point": kappa * mbA15 / missA15 / 1e6,
            "P_benefit_ms_from_mechanism_x_kappa": B_P,
            "max_allowable_incremental_policy_cost_ms_breakeven": B_ceiling,
            "required_cost_reduction_if_benefit_unchanged": 1 - B_ceiling / C0,
            "max_invocation_fraction_per_call_cost_unchanged_benefit_fully_retained": B_ceiling / C0,
            "max_invocation_fraction_with_2x_margin": B_ceiling / C0 / P["success_benefit_cost_ratio"],
            "runtime_over_microbench_transfer_factor_predictor_path": nsS / mb_sham, "runtime_over_microbench_transfer_factor_b_selected": nsA / mb_bsel,
            "incremental_runtime_ns_per_event_predictor": nsS - nsA,
            "belady_fraction_of_b_selected_misses_removed_r2_coding_v1": belady_frac,
            "clairvoyant_ceiling_ms_prior_point": ceiling_ms, "clairvoyant_ceiling_ms_prior_low": ceiling_ms * P["kappa_low_fraction"],
            "current_P_capture_of_clairvoyant_ceiling": B_P / ceiling_ms,
            "detectability_ci_halfwidth_ms_by_blocks": det,
        },
        "invocation_fraction_table": inv, "benefit_required_by_cost_reduction": ben, "break_even_matrix": mat,
        "notes": ["kappa is the 1.5 vs 8 GiB association between A wall time and A decode miss bytes; it is NOT a causal per-miss cost. kappa_low halves it by pre-registration.",
                  "the clairvoyant ceiling is a PRIOR from the R2 coding-v1 trace (non-causal Belady); Phase 1 rung R2 re-measures it on the R3-04 stream.",
                  "B_ceiling is R3-04's frozen replay upper bound for the CURRENT mechanism, not for retention in general."],
    }


# ------------------------------------------------------------------ executable Phase 1 rules (pre-registered; no candidate result exists at freeze time)
def classify_phase1(x: Dict[str, Any], P: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """x: fidelity_pass(bool), kappa_low_ms_per_MB, events_per_token, belady_bytes_avoided_per_token,
    candidates: {name: {dev_bytes_avoided_per_token, eval_bytes_avoided_per_token, microbench_incremental_ns_per_event}},
    pp_bytes_avoided_per_token, ces_bytes_avoided_per_token, pp_curve_retained_at_f010 (fraction of PP benefit kept at 10% refresh)."""
    P = P or protocol()["constants"]
    margin, ratio_min, tf = P["success_net_margin_ms_per_token"], P["success_benefit_cost_ratio"], P["microbench_to_runtime_transfer_factor"]
    out: Dict[str, Any] = {"kills": [], "candidates": {}}
    if not x["fidelity_pass"]:
        return {"outcome": "R3_05_PHASE1_INVALID", "reason": "replay fidelity gate failed: no candidate result may be interpreted", "kills": [], "candidates": {}}
    k = x["kappa_low_ms_per_MB"]
    if x["belady_bytes_avoided_per_token"] / 1e6 * k < margin:
        out["kills"].append("K1_CEILING: clairvoyant headroom x kappa_low < margin -> stop predictive retention as the primary R3 path")
        out["outcome"] = "R3_05_INFEASIBLE"; return out
    if x["ces_bytes_avoided_per_token"] >= P["k2_ces_fraction"] * x["pp_bytes_avoided_per_token"]:
        out["kills"].append("K2_PP_RETIRED: zero-cost current-event shield reaches >= 80% of predicted_protect's effect")
    if x["pp_curve_retained_at_f010"] <= P["k3_amortization_max_retained_at_f010"]:
        out["kills"].append("K3_NO_AMORTIZATION: <=20% of PP benefit kept at 10% refresh rate -> invocation-reduction family dropped")
    best, best_net = None, -math.inf
    for name, c in x["candidates"].items():
        b = c["eval_bytes_avoided_per_token"] / 1e6 * k
        cost = c["microbench_incremental_ns_per_event"] * x["events_per_token"] * tf / 1e6
        net, ratio = b - cost, (b / cost if cost > 0 else math.inf)
        dev_ok = c["dev_bytes_avoided_per_token"] > 0 and c["eval_bytes_avoided_per_token"] > 0
        lab = "FEASIBLE" if (dev_ok and net >= margin and ratio >= ratio_min) else ("MARGINAL" if net > 0 and dev_ok else "INFEASIBLE")
        if b < margin: lab = "INFEASIBLE"; out["kills"].append(f"K4_BENEFIT_FLOOR[{name}]: benefit at zero cost < margin")
        out["candidates"][name] = {"benefit_low_ms": b, "cost_high_ms": cost, "net_ms": net, "ratio": ratio, "dev_eval_sign_consistent": dev_ok, "label": lab}
        if lab != "INFEASIBLE" and net > best_net: best, best_net = name, net
    sur, pri = x["candidates"].get(P["surrogate_candidate"]), x["candidates"].get(P["primary_candidate"])
    if sur and pri and out["candidates"][P["primary_candidate"]]["net_ms"] > 0 and out["candidates"][P["surrogate_candidate"]]["net_ms"] >= P["k5_surrogate_fraction"] * out["candidates"][P["primary_candidate"]]["net_ms"]:
        out["kills"].append("K5_PREFER_SURROGATE: cheap surrogate keeps >= 80% of primary net value -> surrogate becomes the R3-05 candidate")
        best = P["surrogate_candidate"] if out["candidates"][P["surrogate_candidate"]]["label"] != "INFEASIBLE" else best
    labs = [v["label"] for v in out["candidates"].values()]
    out["selected"] = best
    out["outcome"] = "R3_05_FEASIBLE" if best and out["candidates"][best]["label"] == "FEASIBLE" else ("R3_05_MARGINAL" if "MARGINAL" in labs else "R3_05_INFEASIBLE")
    return out


# ------------------------------------------------------------------ build / verify
def close(a: Any, b: Any) -> bool:
    if isinstance(a, dict): return isinstance(b, dict) and set(a) == set(b) and all(close(a[k], b[k]) for k in a)
    if isinstance(a, list): return isinstance(b, list) and len(a) == len(b) and all(close(u, v) for u, v in zip(a, b))
    if isinstance(a, float) or isinstance(b, float): return math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-12)
    return a == b


REQUIRED = ["research_question", "hypotheses", "candidates", "primary_endpoint", "secondary_endpoints", "break_even_threshold", "success_rule", "failure_rule", "kill_rules",
            "dev_eval_separation", "frozen_data_allowed", "new_data_required", "analysis_plan", "evidence_ladder", "runtime_realization_gate", "conformance_gate", "benchmark_gate",
            "compute_budget", "r3_r4_boundary", "constants", "claim_boundaries", "parents"]


def verify() -> List[str]:
    p: List[str] = []
    pr = protocol()
    for k in REQUIRED:
        if k not in pr: p.append(f"PROTOCOL.json missing {k}")
    if pr.get("constants", {}).get("success_benefit_cost_ratio", 0) < 2 or pr.get("constants", {}).get("success_net_margin_ms_per_token", 0) < 10:
        p.append("success margin weaker than pre-registered minimum")
    ev = json.loads((HERE / "ECONOMIC_ENVELOPE.json").read_text(encoding="utf-8"))
    if not close(ev, envelope()): p.append("ECONOMIC_ENVELOPE.json does not match recomputation from frozen sources")
    fr = json.loads((HERE / "FREEZE.json").read_text(encoding="utf-8"))
    if fr["freeze_sha256"] != freeze_sha(fr): p.append("freeze sha mismatch")
    for n, h in fr["files_sha256"].items():
        if sha((HERE / n).read_bytes()) != h: p.append(f"{n} differs from freeze")
    for k, h in fr["sources_sha256"].items():
        if sha(src(k).read_bytes()) != h: p.append(f"frozen source {k} changed")
    return p


def build() -> None:
    (HERE / "ECONOMIC_ENVELOPE.json").write_text(dumps(envelope()), encoding="utf-8")
    fr = {"artifact": "R3-05-PHASE0-PROTOCOL-FREEZE", "files_sha256": {n: sha((HERE / n).read_bytes()) for n in FROZEN_FILES},
          "sources_sha256": {k: sha(src(k).read_bytes()) for k in SOURCES}, "sources": SOURCES, "r3_04_final_evidence_freeze": R3_04_FINAL_FREEZE,
          "authorizes": "NOTHING model-bearing. Phase 1 offline rungs R1-R6 only; any runtime step needs a separate gate."}
    fr["freeze_sha256"] = freeze_sha(fr)
    (HERE / "FREEZE.json").write_text(dumps(fr), encoding="utf-8"); print("freeze", fr["freeze_sha256"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--build", action="store_true"); ap.add_argument("--verify", action="store_true"); ap.add_argument("--print-envelope", action="store_true"); a = ap.parse_args()
    if a.print_envelope: print(dumps(envelope()))
    if a.build: build()
    if a.verify:
        r = verify(); print("VERIFY", "OK" if not r else r); sys.exit(1 if r else 0)
