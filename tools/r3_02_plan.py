"""R3-02 Phase 0 planner: deterministic calibration/mechanism protocol.

Generates PREREGISTRATION.json, ACQUISITION_PLAN.json and FREEZE.json, and
validates the frozen invariants (launch counts, matrix, ordering, identities,
parameters, absence of results, protocol digest).

No model is loaded, no acquisition happens here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[1]
PHASE = REPO / "experiments" / "R3-02-calibration-mechanism"

RUNTIME_SHA = "3f094f5b5f4963f488b63722f365068f09e2aa3b12e2096969da99f471bbd644"
R2_RUNTIME_SHA = "f4d93061b0d4ce41bc4c5f0abce3420e16768ff5dba5ce53212b49ca25dcf087"
MODEL = {"path": r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf",
         "sha256": "a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32",
         "bytes": 18632184480}
STORE = {"path": r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs",
         "sha256": "fef943ae0bc017864a47d41c1fc90d422016045f7e8705b1d138b7defd8c5146",
         "bytes": 17553387520}
WORKLOAD = {"id": "r2-11-coding-v1",
            "path": "research2/workloads/r2_11/r2-11-coding-v1.json",
            "sha256": "c02f9657d12d874cc43523e2b609bb604a68a6a8b267b2ffac6d3420c509b730"}
DECODING = {"seed": 42, "temperature": 0.0, "top_k": 1, "context": 4096,
            "n_predict_scientific": 512, "n_predict_preflight": 32}
CONTRACT_DIGEST = "99a68c73c5a3caba4fd53937f27907375734f3866c84e18d4ecb944013efa073"
BUDGETS = {"1.5": 1610612736, "4": 4294967296, "8": 8589934592}
BASE_PARAMS = {"1.5": {"decay_factor": 0.999, "aging_interval_steps": 1},
               "4": {"decay_factor": 0.99, "aging_interval_steps": 64},
               "8": {"decay_factor": 0.999, "aging_interval_steps": 16}}
PREDICTOR = {"predictor_decay": 0.99, "top_k": 8, "n_experts": 128}

CONTROLS = {
    "A": {"policy_id": "b_selected", "role": "baseline"},
    "B": {"policy_id": "predicted_protect_sham", "role": "sham predictive control"},
    "C": {"policy_id": "predicted_protect", "role": "predictive-policy mechanism reference"},
}


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def configurations() -> List[Dict[str, Any]]:
    out = []
    for control in ("A", "B"):
        for label, budget in BUDGETS.items():
            params = dict(BASE_PARAMS[label])
            if control == "B":
                params.update(PREDICTOR)
            out.append({"config_id": f"{control}-{label}GiB", "control": control,
                        "policy_id": CONTROLS[control]["policy_id"], "budget_label": label,
                        "budget_bytes": budget, "params": params})
    params = dict(BASE_PARAMS["8"], **PREDICTOR)
    out.append({"config_id": "C-8GiB", "control": "C", "policy_id": CONTROLS["C"]["policy_id"],
                "budget_label": "8", "budget_bytes": BUDGETS["8"], "params": params})
    return out


def build_plan() -> Dict[str, Any]:
    configs = configurations()
    by_id = {c["config_id"]: c for c in configs}
    launches: List[Dict[str, Any]] = []
    seq = 0

    def add(kind: str, config_id: str, *, n_predict: int, counted: bool,
            repetition: int | None, slot: int | None, cooldown: int, phase: str) -> None:
        nonlocal seq
        seq += 1
        cfg = by_id[config_id]
        launches.append({
            "seq": seq, "phase": phase, "kind": kind, "config_id": config_id,
            "control": cfg["control"], "policy_id": cfg["policy_id"],
            "budget_label": cfg["budget_label"], "budget_bytes": cfg["budget_bytes"],
            "repetition": repetition, "slot": slot, "counted": counted,
            "scientific_cell": counted,
            "n_predict": n_predict, "cooldown_before_s": cooldown,
        })

    # 3 non-scientific integration preflights at 8 GiB
    for control in ("A", "B", "C"):
        add("PREFLIGHT", f"{control}-8GiB", n_predict=DECODING["n_predict_preflight"],
            counted=False, repetition=None, slot=None, cooldown=60, phase="PREFLIGHT")

    # Scientific blocks
    orders = {
        "1.5": [["A", "B"], ["B", "A"], ["A", "B"], ["B", "A"], ["A", "B"]],
        "4": [["A", "B"], ["B", "A"], ["A", "B"], ["B", "A"], ["A", "B"]],
        "8": [["A", "B", "C"], ["B", "C", "A"], ["C", "A", "B"], ["A", "B", "C"], ["B", "C", "A"]],
    }
    for label in ("1.5", "4", "8"):
        configs_in_block = ["A", "B"] if label != "8" else ["A", "B", "C"]
        # one TRACE_COUNT per configuration
        for i, control in enumerate(configs_in_block):
            add("TRACE_COUNT", f"{control}-{label}GiB", n_predict=DECODING["n_predict_scientific"],
                counted=False, repetition=None, slot=None, cooldown=60 if i == 0 else 60,
                phase="SCIENTIFIC")
        # one TIMING warmup per configuration
        for control in configs_in_block:
            add("TIMING_WARMUP", f"{control}-{label}GiB", n_predict=DECODING["n_predict_scientific"],
                counted=False, repetition=None, slot=None, cooldown=60, phase="SCIENTIFIC")
        # 5 counted repetitions, balanced order
        for rep, order in enumerate(orders[label], start=1):
            for slot, control in enumerate(order, start=1):
                add("TIMING_COUNTED", f"{control}-{label}GiB",
                    n_predict=DECODING["n_predict_scientific"], counted=True,
                    repetition=rep, slot=slot, cooldown=60 if slot == 1 else 15,
                    phase="SCIENTIFIC")

    plan = {
        "artifact": "R3-02-ACQUISITION-PLAN",
        "phase": "R3-02",
        "stage": "PHASE_0",
        "status": "FROZEN",
        "runtime": {"path": r"C:\Users\alpel\Desktop\Argine4-r3build\llama.cpp\build\bin\llama-server.exe",
                    "sha256": RUNTIME_SHA, "new_r3_identity": True,
                    "r2_runtime_sha256": R2_RUNTIME_SHA,
                    "r2_timing_comparisons_forbidden": True},
        "model": MODEL,
        "store": STORE,
        "workload": WORKLOAD,
        "decoding": DECODING,
        "contract_digest": CONTRACT_DIGEST,
        "controls": CONTROLS,
        "configurations": configs,
        "launches": launches,
        "trace_reuse": {
            "adopted": True,
            "one_trace_per_configuration": True,
            "cardinality": "one TRACE_COUNT backs exactly the 5 counted TIMING repetitions of its own configuration",
            "conditions": ["identical workload id/hash", "identical seed", "identical budget",
                           "identical policy", "identical full policy parameters",
                           "identical runtime/model/store identities", "identical schema version",
                           "identical contract digest", "identical output_token_digest"],
            "never_across": ["policies", "budgets", "workloads", "seeds", "runtime identities",
                             "model identities", "store identities"],
            "fallback_on_digest_mismatch": "none; the TIMING run's cell is INADMISSIBLE",
        },
        "counts": {
            "scientific_configurations": 7,
            "trace_count_launches": 7,
            "timing_warmups": 7,
            "timing_counted_launches": 35,
            "scientific_cells": 35,
            "scientific_matrix_launches": 49,
            "preflight_launches": 3,
            "total_host_process_launches": 52,
        },
        "cooldowns": {"between_adjacent_controls_within_repetition_s": 15,
                      "between_cells_blocks_and_non_paired_launches_s": 60,
                      "source": "R2-12 frozen cooldown semantics (15 s paired / 60 s cell)"},
        "environment": {
            "operator_attested_clean_environment": "REQUIRED",
            "browsers_closed": "REQUIRED (Chrome and every other browser fully closed)",
            "streaming_video_closed": "REQUIRED (no YouTube or other streaming playback)",
            "heavy_background_workloads": "FORBIDDEN",
            "power_mode": "fixed, never Power Saver",
            "free_ram_feasibility_gate": "REQUIRED (argine.planner.plan())",
            "thermal_control": "fixed cooldown pauses only; no numeric temperature threshold",
            "ac_connection_gate": "NOT a gate",
            "battery_percentage_gate": "NOT a gate",
            "tzs0_gate": "NOT a gate",
        },
        "runtime_identity_rule": "all 52 planned launches must use the frozen R3 runtime SHA; a different binary stops the phase",
        "failure_rule": {
            "reruns_permitted": 1,
            "same_configuration": True, "same_repetition_id": True, "same_decoding": True,
            "same_ordering_slot": True, "same_identities": True,
            "second_failure": "cell INADMISSIBLE; no imputation, no replacement repetitions, no reordering",
        },
        "measurements_included": False,
    }
    return plan


def build_preregistration() -> Dict[str, Any]:
    return {
        "artifact": "R3-02-PREREGISTRATION",
        "phase": "R3-02",
        "stage": "PHASE_0",
        "status": "FROZEN",
        "questions": {
            "Q1": "isolated runtime cost of predictive bookkeeping when retention is held equivalent to B_selected",
            "Q2": "expert-memory traffic at 1.5 / 4 / 8 GiB under the real runtime",
            "Q3": "budgets with enough measured traffic and runtime signal to justify a later cost-aware hypothesis",
            "Q4": "can real TRACE_COUNT+TIMING CELL records be generated under the frozen R3-00A/R3-01 contracts",
            "Q5": "does the sham remain behaviourally equivalent to B_selected through the real model/store path",
        },
        "scope": "mechanism attribution only; not a cost-aware policy evaluation and not a policy success/failure",
        "primary_contrast": "A (b_selected) vs B (predicted_protect_sham), paired within budget",
        "secondary_control": "C (predicted_protect) at 8 GiB only, as a predictive-policy mechanism reference; not an overhead control",
        "budgets_bytes": {k: v for k, v in BUDGETS.items()},
        "parameters": {"base_by_budget": BASE_PARAMS, "predictor": PREDICTOR},
        "endpoints": {
            "step_time": {"formula": "decode_duration_seconds / tokens_generated", "source": "TIMING",
                          "unit": "seconds/token"},
            "delta_overhead_time": {"formula": "median over paired repetitions of step_time(B) - step_time(A)",
                                    "unit": "seconds/token", "aggregation": "per budget"},
            "delta_overhead_relative": {"formula": "(step_time(B) - step_time(A)) / step_time(A)",
                                        "unit": "dimensionless", "aggregation": "per budget"},
            "delta_overhead_cpu": {"formula": "median over paired repetitions of cpu_per_token(B) - cpu_per_token(A)",
                                   "source": "process_cpu_seconds / tokens_generated", "unit": "seconds/token"},
            "traffic": {"miss_bytes_per_token": "expert_miss_bytes / tokens_generated (TRACE_COUNT)",
                        "bytes_read_per_token": "expert_bytes_read / tokens_generated",
                        "reads_per_token": "expert_read_count / tokens_generated",
                        "evictions_per_decision_step": "evictions / decision_rows",
                        "resident_bytes_peak": "max cache_resident_bytes_after"},
            "precision": {"name": "OVERHEAD_BOUNDED",
                          "criterion": "95% CI half-width of delta_overhead_time <= 0.01 * median step_time(A) at the 4 GiB budget",
                          "applies_to": "4 GiB (primary mechanism budget)"},
        },
        "statistics": {
            "paired_unit": "(workload, budget, control pair, repetition)",
            "repetitions": 5,
            "aggregation": "median of per-repetition paired differences",
            "bootstrap": {"resamples": 10000, "seed": 20261116, "ci": "95% percentile"},
            "p_value": None,
            "outlier_removal": None,
            "per_token_pooling": None,
            "individual_values": "all five per-repetition values reported",
        },
        "sham_equivalence_rule": {
            "compared": ["requested expert sequence where semantically required", "victim sequence",
                         "evicted expert sequence", "resident-set trajectory", "resident bytes after every event",
                         "eviction total", "final resident set", "output_token_digest"],
            "requires_positive": "predictor_decisions > 0 for B",
            "on_failure": "MECHANISM_INSUFFICIENT and A-vs-B timing is not interpreted as isolated overhead",
        },
        "classification": {
            "MECHANISM_OBSERVABLE": [
                "all 7 TRACE_COUNT records admissible",
                "all 35 counted CELL records admissible",
                "sham vs B_selected retention trajectories identical at all three budgets",
                "sham predictive activity > 0",
                "expert traffic measured and > 0 at at least one tested budget",
                "sham overhead satisfies the frozen precision criterion at 4 GiB",
                "all frozen identities and acquisition invariants hold",
            ],
            "otherwise": "MECHANISM_INSUFFICIENT, reporting the exact failing condition(s)",
            "must_not_report": "policy success/failure",
        },
        "next_phase_gate": {
            "authorizes": "cost-model protocol design",
            "requires": ["MECHANISM_OBSERVABLE",
                         "at least one prospective runtime-cost quantity that is available before a retention decision or derivable from historical data",
                         "not derived from reserved holdout data, not based on TRACE_COUNT timing, not an unidentified causal claim",
                         "measurable or calibratable under the real runtime"],
            "otherwise": "do not authorize cost-model synthesis",
        },
        "claim_boundaries": {
            "allowed": ["predictive bookkeeping has measurable/bounded wall-time overhead",
                        "expert-memory traffic is present at budget X",
                        "A/B real-runtime retention trajectories are equivalent",
                        "traffic and timing vary across budgets"],
            "not_allowed": ["I/O caused the timing difference", "policy overhead caused the R2 result",
                            "cost-aware retention will improve throughput", "R3 achieved runtime speedup"],
        },
        "measurements_included": False,
    }


def protocol_digest() -> str:
    return digest_of({"preregistration": build_preregistration(), "acquisition_plan": build_plan()})


def validate() -> List[str]:
    failures: List[str] = []
    plan = build_plan()
    launches = plan["launches"]
    counts = plan["counts"]

    def n(kind: str, phase: str | None = None) -> int:
        return sum(1 for l in launches if l["kind"] == kind and (phase is None or l["phase"] == phase))

    if n("TRACE_COUNT") != 7: failures.append("trace_count != 7")
    if n("TIMING_WARMUP") != 7: failures.append("warmups != 7")
    if n("TIMING_COUNTED") != 35: failures.append("counted != 35")
    if n("PREFLIGHT") != 3: failures.append("preflights != 3")
    if len(launches) != 52: failures.append("total launches != 52")
    if counts["scientific_matrix_launches"] != 49: failures.append("matrix != 49")
    if len(plan["configurations"]) != 7: failures.append("configurations != 7")
    if counts["scientific_cells"] != 35: failures.append("cells != 35")

    # every counted TIMING has exactly one TC for its config, 5 reuses each
    reuse = {}
    for l in launches:
        if l["kind"] == "TIMING_COUNTED":
            reuse[l["config_id"]] = reuse.get(l["config_id"], 0) + 1
    if set(reuse.values()) != {5}: failures.append(f"trace reuse cardinality wrong: {reuse}")
    if len(reuse) != 7: failures.append("reuse configs != 7")

    # ordering balance
    for label, controls in (("1.5", ["A", "B"]), ("4", ["A", "B"])):
        reps = [l for l in launches if l["budget_label"] == label and l["kind"] == "TIMING_COUNTED"]
        for rep in range(1, 6):
            order = [l["control"] for l in reps if l["repetition"] == rep]
            if order not in (["A", "B"], ["B", "A"]):
                failures.append(f"{label}GiB rep{rep} order {order} not A/B alternating")
        firsts = [l["control"] for l in reps if l["repetition"] % 2 == 1 and l["slot"] == 1]
        if len(set(firsts)) != 1: failures.append(f"{label}GiB not alternating across reps")
    reps8 = [l for l in launches if l["budget_label"] == "8" and l["kind"] == "TIMING_COUNTED"]
    positions = {c: [0, 0, 0] for c in "ABC"}
    for rep in range(1, 6):
        order = [l["control"] for l in reps8 if l["repetition"] == rep]
        if sorted(order) != ["A", "B", "C"]:
            failures.append(f"8GiB rep{rep} not a permutation of ABC")
        for slot, c in enumerate(order):
            positions[c][slot] += 1
    for c, p in positions.items():
        if max(p) - min(p) > 1:
            failures.append(f"8GiB position imbalance for {c}: {p}")

    # identities
    if plan["runtime"]["sha256"] != RUNTIME_SHA: failures.append("runtime sha mismatch")
    if plan["model"]["sha256"] != MODEL["sha256"]: failures.append("model sha mismatch")
    if plan["store"]["sha256"] != STORE["sha256"]: failures.append("store sha mismatch")
    if plan["workload"]["sha256"] != WORKLOAD["sha256"]: failures.append("workload sha mismatch")
    if plan["contract_digest"] != CONTRACT_DIGEST: failures.append("contract digest mismatch")

    # policy parameters exact
    for c in plan["configurations"]:
        base = BASE_PARAMS[c["budget_label"]]
        for k, v in base.items():
            if c["params"].get(k) != v: failures.append(f"{c['config_id']} base param {k} mismatch")
        if c["control"] in ("B", "C"):
            for k, v in PREDICTOR.items():
                if c["params"].get(k) != v: failures.append(f"{c['config_id']} predictor {k} mismatch")
        if c["control"] == "A" and any(k in c["params"] for k in PREDICTOR):
            failures.append(f"{c['config_id']} must not carry predictor params")

    # no EVAL reference, no result metrics
    blob = canonical_json(plan) + canonical_json(build_preregistration())
    for bad in ("EVAL", "held-out", "heldout"):
        if bad in blob: failures.append(f"forbidden token {bad}")
    for bad in ("observed", "tps", "measured_value", "decode_tokens_per_second"):
        if bad in blob: failures.append(f"result-like content {bad}")

    # determinism
    if protocol_digest() != protocol_digest(): failures.append("digest not deterministic")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    sub.add_parser("digest")
    sub.add_parser("validate")
    fr = sub.add_parser("freeze")
    fr.add_argument("--created-utc", required=True)
    fr.add_argument("--anchor-commit", required=True)
    args = ap.parse_args()

    if args.cmd == "plan":
        for name, obj in (("PREREGISTRATION.json", build_preregistration()),
                          ("ACQUISITION_PLAN.json", build_plan())):
            (PHASE / name).write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
            print("wrote", name)
        return 0
    if args.cmd == "digest":
        print(protocol_digest())
        return 0
    if args.cmd == "validate":
        failures = validate()
        print("failures:", failures if failures else "none")
        return 0 if not failures else 1
    if args.cmd == "freeze":
        failures = validate()
        if failures:
            print("REFUSING TO FREEZE:", failures)
            return 1
        digest = protocol_digest()
        arts = {}
        for name in ("README.md", "PROTOCOL.md", "PREREGISTRATION.json", "ACQUISITION_PLAN.json",
                     "tests/test_protocol.py"):
            p = PHASE / name
            if p.exists():
                arts[name] = hashlib.sha256(p.read_bytes()).hexdigest()
        freeze = {
            "artifact": "R3-02-FREEZE", "phase": "R3-02", "stage": "PHASE_0",
            "status": "PASS_R3_02_PROTOCOL_FROZEN",
            "created_utc": args.created_utc, "anchor_commit": args.anchor_commit,
            "protocol_digest": digest,
            "contract_digest": CONTRACT_DIGEST,
            "runtime_sha256": RUNTIME_SHA,
            "frozen_scope": ["calibration questions", "controls A/B/C", "27-budget/control matrix",
                             "acquisition modes and counts", "trace reuse rule", "ordering",
                             "cooldowns", "environment", "endpoints", "statistics",
                             "sham equivalence rule", "mechanism classification", "next-phase gate"],
            "not_frozen": ["cost-aware policy", "cost-model coefficients", "primary scientific endpoint",
                           "DEV policy selection", "EVAL rules", "runtime benchmark endpoint"],
            "counts": build_plan()["counts"],
            "artifacts": arts,
            "measurements_included": False,
        }
        (PHASE / "FREEZE.json").write_text(json.dumps(freeze, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
        print("wrote FREEZE.json; digest", digest)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
