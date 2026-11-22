"""R3-04 prospective machine-time model for the UNTRACED timing campaign (pure functions; no I/O except the frozen protocol schedule).

Every input has a recorded provenance. Decode ms/token at 1.5 and 4 GiB has NO untraced corrected-runtime observation: the scenarios are explicit bounds, not data.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
PROTO = HERE.parent / "R3-04-net-runtime-value"
_spec = importlib.util.spec_from_file_location("r3_04_amend_validate_protocol", PROTO / "validate_protocol.py")
V = importlib.util.module_from_spec(_spec); sys.modules["r3_04_amend_validate_protocol"] = V; _spec.loader.exec_module(V)

SCENARIOS = ("low", "central", "high")
OLD_RUNTIME_R2_12 = {"1.5": 431.05400322896287, "4": 287.19263405088066}            # old runtime, untraced (R2-12); conservative upper bound for the corrected runtime
R3_03B_8G_A, R3_03B_8G_S = 100.84604619140626, 118.31241958007813                   # corrected runtime, untraced, 80 launches (R3-03B FINAL_RESULT)
RATIO_8G = R3_03B_8G_A / 151.761714481409                                            # corrected / old at 8 GiB (R3-03B vs R2-12) = 0.6645
COST_MS = {"low": 17.466373388671876, "central": 17.466373388671876, "high": 23.3}   # R3-03B sham overhead; high = Gate 2 in-run policy ns_total/512 (21.4-23.3 ms)
DECODE_A_MS = {"low": {"1.5": OLD_RUNTIME_R2_12["1.5"] * RATIO_8G, "4": OLD_RUNTIME_R2_12["4"] * RATIO_8G, "8": R3_03B_8G_A},
               "high": {"1.5": OLD_RUNTIME_R2_12["1.5"], "4": OLD_RUNTIME_R2_12["4"], "8": R3_03B_8G_A}}
DECODE_A_MS["central"] = {b: (DECODE_A_MS["low"][b] + DECODE_A_MS["high"][b]) / 2 for b in ("1.5", "4", "8")}
# Gate 2 real-host evidence (traced launches; trace-insensitive parts only): load 5.48-6.41 s, prefill 8.93-11.23 s, helper shutdown 0.86-2.07 s (mean 1.43)
STARTUP_S = {"low": 5.5, "central": 6.5, "high": 7.5}          # log 'model loaded' 5.48-6.41 s + <=1 s /health polling (driver loop sleeps 1 s)
PREFILL_S = {"low": 8.93, "central": 10.23, "high": 11.23}
SHUTDOWN_HELPER_S = {"low": 0.86, "central": 1.43, "high": 2.07}
TOOLING_S = {"low": 0.0, "central": 1.0, "high": 3.0}           # ASSUMPTION (no direct evidence): JSON/record/validation bookkeeping per launch
LEGACY_FLOOR_S = 300.8                                          # R3-03B: median launch 300.8 s, 71/80 server exits at 300.0 s uptime
N_PREDICT = 512


def decode_s(budget: str, arm: str, scen: str) -> float:
    return N_PREDICT * (DECODE_A_MS[scen][budget] + (COST_MS[scen] if arm in ("S", "P") else 0.0)) / 1000.0


def launch(budget: str, arm: str, scen: str, shutdown: str) -> Dict[str, float]:
    comp = {"startup_load": STARTUP_S[scen], "prefill": PREFILL_S[scen], "decode": decode_s(budget, arm, scen)}
    release = sum(comp.values())
    if shutdown == "helper":
        comp["shutdown"] = SHUTDOWN_HELPER_S[scen]
    else:                                                        # legacy R3-03B behaviour: event delivered ~300 s of uptime (floor), irrelevant if the request ends later
        comp["shutdown"] = max(LEGACY_FLOOR_S - release, SHUTDOWN_HELPER_S[scen])
    comp["model_s"] = release + comp["shutdown"]
    return comp


def design_schedule(blocks: Dict[str, int]) -> List[Dict[str, Any]]:
    """Frozen schedule restricted to the budgets with blocks > 0 (same per-budget slots, order and cooldowns; blocks must equal the frozen N per kept budget)."""
    out = []
    for s in V.timing_schedule():
        if blocks.get(s["budget"], 0) and (s["kind"] == "warmup" or s["block"] <= blocks[s["budget"]]):
            out.append(dict(s))
    return out


def hours(blocks: Dict[str, int], scen: str, shutdown: str) -> Dict[str, Any]:
    sched = design_schedule(blocks)
    model = cool = tool = 0.0
    per_budget: Dict[str, float] = {}
    comp_sum = {"startup_load": 0.0, "prefill": 0.0, "decode": 0.0, "shutdown": 0.0}
    for s in sched:
        l = launch(s["budget"], s["arm"], scen, shutdown)
        model += l["model_s"]; cool += s["cooldown_before_s"]; tool += TOOLING_S[scen]
        per_budget[s["budget"]] = per_budget.get(s["budget"], 0.0) + l["model_s"] + s["cooldown_before_s"] + TOOLING_S[scen]
        for k in comp_sum: comp_sum[k] += l[k]
    return {"physical_launches": len(sched), "counted": sum(1 for s in sched if s["kind"] == "counted"), "warmups": sum(1 for s in sched if s["kind"] == "warmup"),
            "model_h": model / 3600, "wall_h": (model + cool + tool) / 3600, "components_h": {**{k: v / 3600 for k, v in comp_sum.items()}, "cooldown": cool / 3600, "other_tooling": tool / 3600},
            "wall_h_by_budget": {b: v / 3600 for b, v in per_budget.items()}}
