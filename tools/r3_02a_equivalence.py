"""R3-02A offline equivalence harness: predicted_protect_sham vs B_selected.

Runs the MSVC-built retention-conformance binaries on deterministic synthetic
event streams. No model, no runtime, no acquisition.

Comparisons
-----------
A. sham vs b_selected          -> stdout byte-identical, same counters, policy id distinct,
                                  predictor_decisions > 0
B. b_selected regression       -> baseline build vs sham build, byte-identical
C. PredictedProtect regression -> baseline build vs sham build, byte-identical
D. fail-closed                 -> invalid sham parameters rejected

Outputs a deterministic JSON report (sorted keys) to the given path.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

BUDGETS = {"b_selected": 4 * 1024 ** 3, "pp": 4 * 1024 ** 3, "sham": 4 * 1024 ** 3}


def gen_case(name: str) -> Tuple[List[str], int]:
    """Return (event lines, budget_bytes) for one deterministic case."""
    rng = random.Random(f"r3-02a::{name}")
    lines: List[str] = []

    if name == "no_pressure":
        budget, bytes_of_layer, layers, steps = 10_000, 100, 2, 40
        for s in range(steps):
            for l in range(layers):
                experts = sorted(rng.sample(range(8), 2))
                lines.append(json.dumps({"step": s, "layer": l, "experts": experts,
                                         "bytes_of_layer": bytes_of_layer}))
    elif name == "repeated_reuse":
        budget, bytes_of_layer, layers, steps = 500, 100, 1, 120
        for s in range(steps):
            experts = [1, 2, 3] if s % 3 else [1, 2]
            lines.append(json.dumps({"step": s, "layer": 0, "experts": experts,
                                     "bytes_of_layer": bytes_of_layer}))
    elif name == "eviction_heavy":
        budget, bytes_of_layer, layers, steps = 800, 100, 1, 200
        for s in range(steps):
            experts = sorted(rng.sample(range(64), 4))
            lines.append(json.dumps({"step": s, "layer": 0, "experts": experts,
                                     "bytes_of_layer": bytes_of_layer}))
    elif name == "multi_layer":
        budget, bytes_of_layer, layers, steps = 1200, 100, 4, 150
        for s in range(steps):
            for l in range(layers):
                experts = sorted(rng.sample(range(32), 3))
                lines.append(json.dumps({"step": s, "layer": l, "experts": experts,
                                         "bytes_of_layer": bytes_of_layer}))
    elif name == "ties":
        budget, bytes_of_layer = 500, 100
        lines.append(json.dumps({"step": 0, "layer": 0,
                                 "experts": list(range(10)), "bytes_of_layer": bytes_of_layer}))
        for s in range(1, 60):
            lines.append(json.dumps({"step": s, "layer": 0,
                                     "experts": [s % 10, (s + 3) % 10],
                                     "bytes_of_layer": bytes_of_layer}))
    elif name == "budget_boundary":
        budget, bytes_of_layer, steps = 200, 100, 80
        for s in range(steps):
            experts = [0] if s % 2 == 0 else [1]
            lines.append(json.dumps({"step": s, "layer": 0, "experts": experts,
                                     "bytes_of_layer": bytes_of_layer}))
    else:
        raise ValueError(name)
    return lines, budget


def run(exe: Path, args: List[str], stream: List[str]) -> Tuple[str, str, int]:
    proc = subprocess.run([str(exe), *args], input="\n".join(stream) + "\n",
                          capture_output=True, text=True)
    return proc.stdout, proc.stderr, proc.returncode


def parse_final(stderr: str) -> Dict[str, object]:
    for line in stderr.splitlines():
        if line.startswith("FINAL "):
            out: Dict[str, object] = {}
            for tok in line[6:].split():
                if "=" not in tok:
                    continue
                k, v = tok.split("=", 1)
                out[k] = v if k == "policy_id" else int(v)
            return out
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-exe", required=True)
    ap.add_argument("--sham-exe", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    baseline, sham = Path(args.baseline_exe), Path(args.sham_exe)

    b_sel = ["b_selected", "0.99", "64", str(BUDGETS["b_selected"])]
    b_pp = ["predicted_protect", "0.99", "64", "0.99", "8", "128", str(BUDGETS["pp"])]
    b_sham = ["predicted_protect_sham", "0.99", "64", "0.99", "8", "128", str(BUDGETS["sham"])]

    cases = ["no_pressure", "repeated_reuse", "eviction_heavy", "multi_layer", "ties",
             "budget_boundary"]
    results = []
    all_pass = True

    for name in cases:
        stream, budget = gen_case(name)
        sel_args = ["b_selected", "0.99", "64", str(budget)]
        pp_args = ["predicted_protect", "0.99", "64", "0.99", "8", "128", str(budget)]
        sham_args = ["predicted_protect_sham", "0.99", "64", "0.99", "8", "128", str(budget)]
        out_base_sel, err_base_sel, rc1 = run(baseline, sel_args, stream)
        out_sham_sel, err_sham_sel, rc2 = run(sham, sel_args, stream)
        out_base_pp, err_base_pp, rc3 = run(baseline, pp_args, stream)
        out_sham_pp, err_sham_pp, rc4 = run(sham, pp_args, stream)
        out_sham_sham, err_sham_sham, rc5 = run(sham, sham_args, stream)

        f_base_sel, f_sham_sel = parse_final(err_base_sel), parse_final(err_sham_sel)
        f_base_pp, f_sham_pp = parse_final(err_base_pp), parse_final(err_sham_pp)
        f_sham_sham = parse_final(err_sham_sham)

        checks = {
            "sham_vs_b_selected_stdout_identical": out_sham_sham == out_base_sel,
            "sham_vs_b_selected_counters_identical": all(
                f_sham_sham.get(k) == f_base_sel.get(k)
                for k in ("hits", "misses", "evictions", "resident_bytes")),
            "sham_policy_id_distinct": f_sham_sham.get("policy_id") == "predicted_protect_sham",
            "sham_predictor_decisions_positive": int(f_sham_sham.get("predictor_decisions", 0)) > 0,
            "b_selected_regression_identical": out_sham_sel == out_base_sel and f_sham_sel == f_base_sel,
            "predicted_protect_regression_identical": out_sham_pp == out_base_pp and f_sham_pp == f_base_pp,
            "all_runs_exit_zero": all(rc == 0 for rc in (rc1, rc2, rc3, rc4, rc5)),
        }
        ok = all(checks.values())
        all_pass = all_pass and ok
        results.append({
            "case": name,
            "events": len(stream),
            "checks": checks,
            "pass": ok,
            "sham_final": f_sham_sham,
            "b_selected_final": f_base_sel,
        })

    # Fail-closed checks
    stream, _ = gen_case("repeated_reuse")
    _, err_missing, rc_missing = run(sham, ["predicted_protect_sham", "0.99", "64"], stream)
    _, err_topk0, rc_topk0 = run(
        sham, ["predicted_protect_sham", "0.99", "64", "0.99", "0", "128", str(BUDGETS["sham"])], stream)
    _, err_unknown, rc_unknown = run(sham, ["not_a_policy", "0.99", "64", "1000"], stream)
    fail_closed = {
        "sham_missing_params_rejected": rc_missing != 0,
        "sham_top_k_zero_rejected": rc_topk0 != 0,
        "unknown_policy_rejected": rc_unknown != 0,
        "all_rejected": rc_missing != 0 and rc_topk0 != 0 and rc_unknown != 0,
    }
    all_pass = all_pass and fail_closed["all_rejected"]

    report = {
        "artifact": "R3-02A-EQUIVALENCE",
        "cases": results,
        "fail_closed": fail_closed,
        "summary": {
            "cases": len(results),
            "passed": sum(1 for r in results if r["pass"]),
            "all_pass": all_pass,
        },
    }
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    print(f"equivalence: {report['summary']['passed']}/{report['summary']['cases']} cases, all_pass={all_pass}")
    for f, v in fail_closed.items():
        print(f"  {f}: {v}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
