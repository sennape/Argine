"""Builds the R3-04 campaign-efficiency amendment artifacts (deterministic; reads only committed evidence). Writes JSON/MD next to itself, then FREEZE.json.
  python build_amendment.py            (rebuilds everything; the validator recomputes and compares)
"""
from __future__ import annotations

import json, statistics as st, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1] / "tools"))
import time_model as T  # noqa: E402
import r3_04_acquire as A  # noqa: E402

V = T.V
PROTO = HERE.parent / "R3-04-net-runtime-value"
G2 = PROTO / "gate2-mechanism-evidence"
DESIGNS = {"FULL": {"1.5": 18, "4": 6, "8": 6}, "PRIMARY_PLUS_8G": {"1.5": 18, "8": 6}, "PRIMARY_ONLY": {"1.5": 18}}
RECOMMENDED = "PRIMARY_PLUS_8G"


def j(p, o):
    (HERE / p).write_text(json.dumps(o, indent=1, sort_keys=True) + "\n")


def build():
    pf = json.loads((PROTO / "FREEZE.json").read_text()); g2 = json.loads((G2 / "FREEZE.json").read_text())
    g2t = json.loads((G2 / "LAUNCH_TIMING.json").read_text()); g2m = json.loads((G2 / "MECHANISM_METRICS.json").read_text())
    ss = json.loads((PROTO / "SAMPLE_SIZE.json").read_text())
    # ---- time model
    tm = {"inputs": {"decode_A_ms_per_token_scenarios": T.DECODE_A_MS, "cost_ms_per_token_scenarios": T.COST_MS, "startup_s": T.STARTUP_S, "prefill_s": T.PREFILL_S, "shutdown_helper_s": T.SHUTDOWN_HELPER_S,
                    "tooling_s_ASSUMPTION": T.TOOLING_S, "legacy_floor_s": T.LEGACY_FLOOR_S, "corrected_over_old_ratio_8GiB": T.RATIO_8G,
                    "provenance": {"decode 8 GiB": "R3-03B corrected runtime, 80 untraced counted launches (100.846 ms/token A; S adds 17.466)",
                                   "decode 1.5/4 GiB": "NO untraced corrected-runtime observation exists. high = R2-12 old-runtime untraced means (431.1/287.2, frozen planning values); low = those x 0.6645 (corrected/old ratio measured at 8 GiB); central = midpoint. Gate 2 traced decode is NOT used: it is non-monotone in budget (8 GiB traced slower than 4 GiB) and its trace overhead is not a usable factor.",
                                   "startup/prefill/shutdown": "Gate 2 real-host launch logs (10 launches): load 5.48-6.41 s (+<=1 s health polling), prefill 8.93-11.23 s, helper shutdown 0.86-2.07 s",
                                   "legacy shutdown": "R3-03B: 71/80 exits at 300.0 s of uptime, median launch 300.8 s, mean shutdown wait 218 s",
                                   "cooldown": "frozen schedule: 60 s before a block/warmup, 15 s within a block"}},
          "designs": {}}
    for n, b in DESIGNS.items():
        tm["designs"][n] = {"blocks": b, **{f"{sh}_{sc}": T.hours(b, sc, sh) for sh in ("helper", "legacy") for sc in T.SCENARIOS}}
    s = g2t["summary"]
    prior = json.loads((PROTO / "SAMPLE_SIZE.json").read_text())["hours"]
    tm["gate2_actual_vs_plan"] = {"plan_mechanism_hours_fixed_model": prior["fixed"]["mechanism_hours"], "plan_mechanism_hours_current_model": prior["current"]["mechanism_hours"],
                                  "actual_first_launch_start_to_last_record_h": s["first_launch_start_to_last_record_s"] / 3600, "actual_incl_initial_60s_cooldown_h": (s["first_launch_start_to_last_record_s"] + 60) / 3600,
                                  "actual_server_uptime_h": s["sum_server_uptime_s"] / 3600, "actual_post_launch_replay_analysis_h": s["sum_post_launch_analysis_s"] / 3600, "actual_pre_launch_gaps_h": s["sum_pre_launch_gaps_s"] / 3600,
                                  "user_reported_span_h": (1 * 3600 + 33 * 60 + 48) / 3600,
                                  "evidence_supported_discrepancies": ["Plan assumed traced decode = 5.05 x untraced planning value (2.2 s/token at 1.5 GiB); observed traced decode 0.56-0.61 s/token at 1.5 GiB.",
                                                                       "Plan scaled prefill with decode wall (231 s at 1.5 GiB); observed prefill 8.9-11.2 s at every budget.",
                                                                       "Plan omitted post-launch replay analysis (94-421 s per launch, 0.61 h total) and trace comparisons (pre-launch gaps up to 369 s): traced-phase overhead that does not exist in the untraced campaign.",
                                                                       "Cause of the non-monotone traced decode (4 GiB < 1.5 GiB < 8 GiB per token) is undetermined."]}
    j("UPDATED_TIME_MODEL.json", tm)
    # ---- design comparison
    H = lambda d, k: tm["designs"][d][k]
    sd_hw = {b: ss["per_budget"][b] for b in ("1.5", "4", "8")}
    marg4 = H("FULL", "helper_central")["wall_h"] - H("PRIMARY_PLUS_8G", "helper_central")["wall_h"]; marg8 = H("PRIMARY_PLUS_8G", "helper_central")["wall_h"] - H("PRIMARY_ONLY", "helper_central")["wall_h"]
    prec = lambda b, m: 1.0 / sd_hw[b]["frozen_half_width_ms_planning"] ** 2 / m
    dc = {"designs": {}, "marginal": {"4": {"extra_wall_h_central_helper": marg4, "planning_half_width_ms": sd_hw["4"]["frozen_half_width_ms_planning"], "planning_power_exclude_zero": sd_hw["4"]["frozen_power_planning"], "precision_per_hour_1_over_hw2": prec("4", marg4),
                                      "replay_implied_max_wall_benefit_ms": 0.10470225918282712, "gate2_P_vs_A_miss_byte_reduction_pct": g2m["P_vs_A_decode_miss_byte_reduction_pct"]["4"]},
                                "8": {"extra_wall_h_central_helper": marg8, "planning_half_width_ms": sd_hw["8"]["frozen_half_width_ms_planning"], "planning_power_exclude_zero": sd_hw["8"]["frozen_power_planning"], "precision_per_hour_1_over_hw2": prec("8", marg8),
                                      "retention_effect": "exactly zero in Gate 2 counters (P == A == S: hits, misses, miss bytes, evictions identical)"}}}
    info = {"FULL": {"answers": "primary Q (1.5), descriptive P-A/P-S/S-A at 4 and 8 GiB", "lost": "nothing vs the frozen design", "claim": "unchanged (three tested budgets)", "bridge": "4 GiB corrected-runtime timing at R2's primary budget"},
            "PRIMARY_PLUS_8G": {"answers": "primary Q (1.5) + zero-retention falsification control at 8 GiB for the three-arm untraced driver and the real P arm", "lost": "4 GiB descriptive timing (cannot classify; planning half-width 14.3 ms ~ 82% of the 17.5 ms cost scale)", "claim": "narrowed: two tested budgets (1.5, 8); 4 GiB untested for timing (mechanism-only from Gate 2)", "bridge": "4 GiB bridge to R2-12 lost; recoverable later as its own additive 37-launch protocol (about 1.8 h)"},
            "PRIMARY_ONLY": {"answers": "primary Q (1.5) only", "lost": "the only zero-retention instrument-falsification control for the real P arm; 4 and 8 GiB descriptive timing", "claim": "narrowed: one tested budget; no control in the same campaign", "bridge": "no budget gradient; R3-03B covers S-A at 8 GiB only (not the real P arm)"}}
    for n, b in DESIGNS.items():
        h = tm["designs"][n]
        dc["designs"][n] = {"blocks": b, "counted_launches": h["helper_central"]["counted"], "warmups": h["helper_central"]["warmups"], "physical_launches": h["helper_central"]["physical_launches"],
                            "model_h_low_central_high_helper": [h[f"helper_{s}"]["model_h"] for s in T.SCENARIOS], "wall_h_low_central_high_helper": [h[f"helper_{s}"]["wall_h"] for s in T.SCENARIOS],
                            "wall_h_central_legacy_shutdown_counterfactual": h["legacy_central"]["wall_h"], "shutdown_savings_h_central": h["legacy_central"]["wall_h"] - h["helper_central"]["wall_h"],
                            "primary_classification_capability": "YES (1.5 GiB, 18 blocks, frozen estimator and rule: unchanged)", "primary_classification_changed": False, **info[n],
                            "mechanistic_evidence_already_from_gate2": "all three budgets: A/S/P counters, protected-set effect, policy ns/call, decision equivalence S==A, P timing OFF==ON"}
    dc["recommended"] = RECOMMENDED
    dc["other_designs_considered"] = {"raise primary N": "rejected: would use Gate 2 effect information (Part D); N=18 stays", "lower primary N": "rejected: N=12 misses the frozen precision target (planning half-width 7.0 > 5.82 ms)",
                                      "8 GiB with fewer than 6 blocks": "rejected: the 6 permutations need a whole cycle to stay order-balanced; 3 blocks would break balance", "4 GiB only / 4+1.5": "rejected: 4 GiB has the noisiest planning SD and no best-case test (frozen budget selection)",
                                      "unbalanced extras": "rejected: no clear scientific advantage"}
    j("DESIGN_COMPARISON.json", dc)
    # ---- efficiency review
    drop = [x["seq"] for x in V.timing_schedule() if x["budget"] == "4"]
    er = {"artifact": "R3-04-EFFICIENCY-REVIEW", "classification": "FULL_DESIGN_OVERBUILT", "scope": "only the 4 GiB timing budget is unnecessary; the rest of the full design is justified",
          "unnecessary_launches": {"budget": "4", "frozen_seq_range": [min(drop), max(drop)], "count": len(drop), "warmups": 1, "counted": 36,
                                   "why": ["cannot classify R3-04 (protocol: descriptive only); protocol's own budget selection marks it droppable without changing the primary answer",
                                           "planning CI half-width 14.3 ms (N=6) is ~82% of the 17.5 ms cost scale, power 0.70: the least informative contrast per machine hour (precision per hour about 1/60 of the 8 GiB control)",
                                           "Gate 2 already supplies all three budgets' mechanism evidence incl. 4 GiB counters (P-A miss bytes -0.026%) and decision identity",
                                           "R2-12 bridge value is historical: R2-12 is closed and 'not reinterpreted'; the future HoPi gate is its own protocol"]},
          "kept_8gib": {"why": ["only zero-retention falsification control for the REAL P arm: Gate 2 shows P == A == S at the counter level at 8 GiB, so P-S and P-A timing should reflect bookkeeping only",
                                "exercises the new three-arm untraced driver + helper lifecycle end-to-end at the budget where launches are cheapest",
                                "R3-03B measured S-A at 8 GiB only, never the real P arm",
                                "cheap (about 1 h central) and precise (planning half-width 2.4 ms)"], "option_B_rejected": "Gate 2 counters + R3-03B cost do not test the untraced P-arm timing path, so 36 launches are not redundant"},
          "primary_n": {"changed": False, "value": 18, "reason": "N is fixed by the frozen precision rule, which does not use any retention effect; Gate 2 effect magnitude/sign was NOT used for any N decision"},
          "anti_bias": "The 4 GiB / 8 GiB decisions rest on protocol roles, planning precision, cost and structural facts (exactly-zero retention at 8 GiB), not on whether the 1.5 GiB retention effect looks strong or weak.",
          "r3_02g_comparison": {"full_design_wall_h_central_helper": H("FULL", "helper_central")["wall_h"], "recommended_wall_h_central_helper": H(RECOMMENDED, "helper_central")["wall_h"],
                                "verdict": "not an R3-02G-scale oversizing (~10 h, not ~64 h); the excess is confined to the 4 GiB budget, about 22% of the full wall time"},
          "assumptions": ["1.5/4 GiB untraced decode is bounded, not observed (see UPDATED_TIME_MODEL)", "per-launch tooling overhead 1 s is an assumption", "legacy-shutdown savings are a model (floor 300.8 s), not a measurement"]}
    j("EFFICIENCY_REVIEW.json", er)
    # ---- amendment
    sched = A.amended_schedule(DESIGNS[RECOMMENDED])
    am = {"artifact": "R3-04-CAMPAIGN-EFFICIENCY-AMENDMENT", "status": "AMENDMENT_PROPOSED_PROSPECTIVE_NOT_OPENED", "acquisition_opened": False, "opening_freeze_exists": False,
          "parents": {"protocol_freeze_sha256": pf["freeze_sha256"], "protocol_commit": "4a0aa2d", "gate1_freeze_sha256": "6379572091345e38f130a62a18d9fd5acdaef673f92439c44ff2bea8de8d6f42", "gate2_freeze_sha256": g2["freeze_sha256"], "gate2_evidence_commit": "7657934", "gate2_tooling_commit": "2345808"},
          "design": {"name": RECOMMENDED, "blocks": DESIGNS[RECOMMENDED], "counted_launches": 144, "warmups": 2, "physical_launches": len(sched), "dropped": {"4": "timing (descriptive budget); 37 launches"}},
          "frozen_schedule_sha256": pf["timing_schedule_sha256"], "amended_schedule_sha256": V.schedule_sha(sched), "schedule_rule": "frozen schedule restricted to budgets 1.5 and 8; slots, order, arms, cooldowns unchanged; seq renumbered 1..146 with frozen_seq retained",
          "unchanged": {"question": "R3-04 primary question", "primary_endpoint": "decode wall ms/token", "arms": V.ARMS, "primary_budget": "1.5", "primary_n_blocks": 18, "classification_rule": "NET_WIN: CI upper < 0; NET_LOSS: CI lower > 0; else INCONCLUSIVE (frozen V.classify_primary)",
                        "estimator": "frozen paired block t-interval, alpha 0.05", "runtime_sha256": V.RUNTIME_SHA256, "token_digest": V.TOKEN_DIGEST, "8GiB_role": "descriptive negative control, N=6, cannot override the primary classification"},
          "changed": {"4GiB_timing": "dropped (no timing launches)", "shutdown": "validated own_console_helper REQUIRED; helper failure/fallback fails the launch closed", "time_estimates": "updated (UPDATED_TIME_MODEL.json)"},
          "shutdown_method_required": "own_console_helper", "estimates_h_helper": {"model": [H(RECOMMENDED, f"helper_{s}")["model_h"] for s in T.SCENARIOS], "wall": [H(RECOMMENDED, f"helper_{s}")["wall_h"] for s in T.SCENARIOS]},
          "driver_requirements": ["fixed-N", "resume-safe", "no optional stopping", "no policy selection", "no silent replacement", "orphan-safe", "protocol/amendment SHA-gated", "runtime SHA-gated", "clean-environment gated", "opening freeze must name this amendment"],
          "opening_freeze_must_record": "amendment_freeze_sha256 (driver refuses a mismatch in either direction)"}
    j("AMENDMENT.json", am)
    (HERE / "CLAIM_IMPACT.md").write_text("""# Claim impact of the campaign-efficiency amendment (PRIMARY_PLUS_8G)

Unchanged: the primary question, endpoint (decode wall ms/token), arms, 1.5 GiB primary with N = 18 blocks (108 counted launches), the frozen estimator, alpha, NET_WIN / NET_LOSS / INCONCLUSIVE definitions, runtime identity, token digest.

Narrowed: R3-04 now has **two** timing budgets (1.5 primary, 8 descriptive negative control). The earlier boundary "the three tested budgets" becomes "the two tested budgets". Nothing can be said from R3-04 about **timing at 4 GiB** with the corrected runtime; R2-12's old-runtime 4 GiB result remains what it was (valid, old runtime, not reinterpreted). Gate 2 supplies mechanism counters (not timing) at 4 GiB.

Still never claimable: generalisation beyond this model/workload/machine/runtime/threads/context and the two tested budgets; any speedup from retention (better retention is not better throughput); anything about HoPi or original Argine.

Recoverable: a 4 GiB timing budget (1 warmup + 6 blocks, 37 launches, about 1.8 h) can be run later as its own additive, prospectively frozen protocol if a later milestone needs it.
""")
    (HERE / "README.md").write_text(f"""# R3-04 campaign-efficiency amendment (prospective, additive; acquisition NOT opened)

Written after Gate 2 (MECHANISM_PASS, freeze {g2['freeze_sha256'][:8]}...) and before OPENING_FREEZE and any counted timing launch. The protocol freeze {pf['freeze_sha256'][:8]}... is unchanged.

Recommended design **{RECOMMENDED}**: 1.5 GiB x 18 blocks (primary, unchanged) + 8 GiB x 6 blocks (negative control) + 1 warmup per budget = 146 physical launches (144 counted). The 4 GiB timing budget (37 launches) is dropped. Validated shutdown helper required.

Expected wall (helper, untraced): {H(RECOMMENDED,'helper_low')['wall_h']:.1f} / {H(RECOMMENDED,'helper_central')['wall_h']:.1f} / {H(RECOMMENDED,'helper_high')['wall_h']:.1f} h (low/central/high); the full design would be {H('FULL','helper_low')['wall_h']:.1f} / {H('FULL','helper_central')['wall_h']:.1f} / {H('FULL','helper_high')['wall_h']:.1f} h.

Files: EFFICIENCY_REVIEW.json, DESIGN_COMPARISON.json, UPDATED_TIME_MODEL.json, CLAIM_IMPACT.md, AMENDMENT.json, FREEZE.json; `validate_amendment.py` recomputes everything; `build_amendment.py` regenerates.
""")
    names = ["AMENDMENT.json", "CLAIM_IMPACT.md", "DESIGN_COMPARISON.json", "EFFICIENCY_REVIEW.json", "README.md", "UPDATED_TIME_MODEL.json", "build_amendment.py", "time_model.py", "validate_amendment.py"]
    fz = {"artifact": "R3-04-CAMPAIGN-EFFICIENCY-AMENDMENT-FREEZE", "status": "AMENDMENT_FROZEN_ACQUISITION_NOT_OPENED", "acquisition_opened": False, "opening_freeze_exists": False,
          "protocol_freeze_sha256": pf["freeze_sha256"], "gate2_freeze_sha256": g2["freeze_sha256"], "recommended_design": RECOMMENDED, "amended_schedule_sha256": am["amended_schedule_sha256"],
          "files_sha256_lf": {n: V.sha_lf(HERE / n) for n in names}}
    fz["freeze_sha256"] = V.freeze_sha(fz)
    j("FREEZE.json", fz)
    return fz


if __name__ == "__main__":
    print(build()["freeze_sha256"])
