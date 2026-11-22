"""Claims registry for the Research 4 manuscript.

Every numerical or identity claim printed in research4.tex is recomputed here from the frozen artifacts. An entry carries the
meaning, the artifact, the field, the calculation, the displayed precision, and a regular expression that must match the
manuscript (or its generated tables). Internal identifiers are allowed in this file and in SOURCE_MAP.md only."""
from __future__ import annotations
import re
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
import frozen
from frozen import big, P0, P1, P2

VAL, EVA = f"{P1}/VALIDATION_RESULTS.json", f"{P2}/EVAL_RESULTS.json"
DEC, CHM, TMAN = f"{P2}/DECISION_APPLICATION.json", f"{P1}/CHEAP_MODEL.json", f"{P1}/TEACHER_MANIFEST.json"
R3HC = "experiments/R3-05-economic-retention/r4-nue-candidate-gate/HEADROOM_CAPTURE.json"
R3CL = "experiments/R3-04-closure/CLOSURE.json"
NAME = {"NUE_X": "NUE-X", "DECAYED_COUNT": "decayed count", "RICH": "rich policy", "CHEAP": "distilled cheap policy"}
CONST_PCT = {"25", "24.999", "90", "95", "100"}  # protocol constants that may appear as percentages in the text


def rnd(x, d):
    """Exact decimal rounding (half up) of a Fraction/float/int to d places, as a string."""
    q = Decimal(Fraction(x).numerator) / Decimal(Fraction(x).denominator)
    return str(q.quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))


def spct(x, d=2):
    return rnd(Fraction(x) * 100, d)


def _neg(s):
    return s.startswith("-")


def p_pct(s):  # signed percentage token as printed: "-36.04\%" or "7.42\%"
    return (r"(?<![\d.])" if _neg(s) else r"(?<![-\d.])") + re.escape(s) + r"\\%"


def p_num(s):
    return (r"(?<![\d.])" if _neg(s) else r"(?<![-\d.])") + re.escape(s) + r"(?![\d])"


def p_big(n):
    return r"(?<![\d{,])" + re.escape(big(n)) + r"(?![\d])"


def p_txt(s):
    return re.escape(s)


class Reg:
    def __init__(self):
        self.rows = []

    def add(self, cid, shown, meaning, art, field, calc, prec, pat, tokens=()):
        self.rows.append(dict(id=cid, shown=shown, meaning=meaning, artifact=art, field=field, calc=calc, precision=prec, pattern=pat, tokens=list(tokens)))

    def pct(self, cid, x, meaning, art, field, calc, d=2):
        s = spct(x, d)
        self.add(cid, s + "%", meaning, art, field, calc, f"{d} dp, % of 1", p_pct(s), [s])
        return s

    def big(self, cid, n, meaning, art, field, calc):
        self.add(cid, big(n).replace("{,}", ","), meaning, art, field, calc, "exact integer (bytes)", p_big(n))

    def num(self, cid, s, meaning, art, field, calc, prec="2 dp"):
        self.add(cid, s, meaning, art, field, calc, prec, p_num(s))

    def fact(self, cid, shown, ok, meaning, art, field, calc, pat):
        if not ok:
            raise AssertionError(f"claim {cid} is false for the frozen evidence: {shown}")
        self.add(cid, shown, meaning, art, field, calc, "exact count/predicate", pat)


def leaves(tree):
    """Depth-first leaves of the fitted CART: (rank, mean_y, n_rows)."""
    out = []
    def walk(i):
        if tree["left"][i] == -1:
            ns = tree["node_stats"][i]; out.append((tree["leaf_rank"][i], ns["mean_y"], ns["n_rows"]))
        else:
            walk(tree["left"][i]); walk(tree["right"][i])
    walk(0); return out


def registry(F):
    R = Reg(); V, X = F["V"], F["X"]; cV, cX = V["capture"], X["capture"]
    # --- captures (pooled, exact) -------------------------------------------------------------------------------------------
    for a in frozen.ARMS:
        R.pct(f"cap_val_{a}", cV[a], f"pooled headroom captured by {NAME[a]} on the 12 validation traces", VAL, f"aggregate_all_12.{a}.capture", "sum(B_A-B_P)/sum(B_A-B_BELADY), exact rational, recomputed from per_workload B_P")
        R.pct(f"cap_eval_{a}", cX[a], f"pooled headroom captured by {NAME[a]} on the 4 held-out traces", EVA, f"aggregate.{a}.capture", "sum(B_A-B_P)/sum(B_A-B_BELADY), exact rational, recomputed from miss_bytes_per_trace")
    # --- miss bytes and headroom ---------------------------------------------------------------------------------------------
    for tag, S, art, k in (("val", V, VAL, "per_workload"), ("eval", X, EVA, "miss_bytes_per_trace")):
        R.big(f"BA_{tag}", S["BA"], f"baseline A pooled decode miss bytes ({tag})", art, k, "sum over traces of A")
        R.big(f"BB_{tag}", S["BB"], f"clairvoyant reference pooled decode miss bytes ({tag})", art, k, "sum over traces of BELADY")
        R.big(f"HR_{tag}", S["headroom"], f"recoverable headroom ({tag})", art, k, "sum(A) - sum(BELADY)")
        R.pct(f"HRfrac_{tag}", Fraction(S["headroom"], S["BA"]), f"headroom as share of baseline miss bytes ({tag})", art, k, "headroom / sum(A)")
    R.big("cheap_excess_val", V["pooled"]["CHEAP"] - V["BA"], "extra miss bytes of the distilled cheap policy vs A (validation)", VAL, "aggregate_all_12.CHEAP.avoided_vs_A", "sum(B_P)-sum(A)")
    R.big("cheap_excess_eval", X["pooled"]["CHEAP"] - X["BA"], "extra miss bytes of the distilled cheap policy vs A (held-out)", EVA, "cheap_absolute_miss_byte_delta_vs_A", "sum(B_P)-sum(A)")
    R.pct("cheap_above_val", Fraction(V["pooled"]["CHEAP"] - V["BA"], V["BA"]), "cheap policy miss bytes above baseline (validation)", VAL, "aggregate_all_12.CHEAP", "(B_P-B_A)/B_A")
    R.pct("cheap_above_eval", Fraction(X["pooled"]["CHEAP"] - X["BA"], X["BA"]), "cheap policy miss bytes above baseline (held-out)", EVA, "aggregate.CHEAP", "(B_P-B_A)/B_A")
    R.big("rich_saved_eval", X["BA"] - X["pooled"]["RICH"], "miss bytes saved by the rich policy vs A (held-out)", EVA, "aggregate.RICH.avoided_vs_A", "sum(A)-sum(B_P)")
    R.pct("rich_saved_frac_eval", Fraction(X["BA"] - X["pooled"]["RICH"], X["BA"]), "rich policy saving as share of baseline miss bytes (held-out)", EVA, "aggregate.RICH", "(A-B_P)/A")
    R.pct("nue_saved_frac_eval", Fraction(X["BA"] - X["pooled"]["NUE_X"], X["BA"]), "NUE-X saving as share of baseline miss bytes (held-out)", EVA, "aggregate.NUE_X", "(A-B_P)/A")
    R.pct("dec_saved_frac_eval", Fraction(X["BA"] - X["pooled"]["DECAYED_COUNT"], X["BA"]), "decayed-count saving as share of baseline miss bytes (held-out)", EVA, "aggregate.DECAYED_COUNT", "(A-B_P)/A")
    R.big("rich_unrecovered_eval", X["headroom"] - (X["BA"] - X["pooled"]["RICH"]), "held-out headroom left unrecovered by the rich policy", EVA, "aggregate", "headroom - (A-B_P_rich)")
    R.pct("rich_unrecovered_frac_eval", 1 - cX["RICH"], "share of held-out headroom left unrecovered by the rich policy", EVA, "aggregate.RICH.capture", "1 - capture")
    R.pct("rich_unrecovered_frac_val", 1 - cV["RICH"], "share of validation headroom left unrecovered by the rich policy", VAL, "aggregate_all_12.RICH.capture", "1 - capture")
    # --- gaps to threshold and ratios ---------------------------------------------------------------------------------------
    R.num("gap_val", spct(Fraction(1, 4) - cV["RICH"]), "percentage points between the rich policy and the sufficiency level (validation)", VAL, "RICH.capture", "100*(1/4 - capture)", "2 dp")
    R.num("gap_eval", spct(Fraction(1, 4) - cX["RICH"]), "percentage points between the rich policy and the sufficiency level (held-out)", EVA, "RICH.capture", "100*(1/4 - capture)", "2 dp")
    R.num("ratio_eval", rnd(cX["RICH"] / cX["NUE_X"], 2), "rich capture divided by NUE-X capture (held-out)", EVA, "aggregate", "capture_rich/capture_nue", "2 dp")
    R.num("ratio_val", rnd(cV["RICH"] / cV["NUE_X"], 2), "rich capture divided by NUE-X capture (validation)", VAL, "aggregate_all_12", "capture_rich/capture_nue", "2 dp")
    R.num("cheap_vs_rich_val", spct(cV["RICH"] - cV["CHEAP"]), "percentage points of capture lost by distillation (validation)", VAL, "aggregate_all_12", "100*(rich - cheap)", "2 dp")
    R.num("cheap_vs_rich_eval", spct(cX["RICH"] - cX["CHEAP"]), "percentage points of capture lost by distillation (held-out)", EVA, "aggregate", "100*(rich - cheap)", "2 dp")
    # --- per-trace extremes and counts ---------------------------------------------------------------------------------------
    pv, px = V["per_trace"], X["per_trace"]
    allt = lambda a: list(pv[a].values()) + list(px[a].values())
    R.pct("nue_max_val", max(pv["NUE_X"].values()), "largest single-trace capture of NUE-X (validation)", VAL, "per_workload.*.NUE_X", "max over 12 traces of (A-P)/(A-BELADY)")
    R.pct("nue_max_all", max(allt("NUE_X")), "largest single-trace capture of NUE-X (all 16 traces)", f"{VAL}; {EVA}", "per-trace", "max over 16 traces")
    R.pct("rich_max_val", max(pv["RICH"].values()), "largest single-trace capture of the rich policy (validation)", VAL, "per_workload.*.RICH", "max over 12 traces")
    R.pct("rich_max_all", max(allt("RICH")), "largest single-trace capture of the rich policy (all 16 traces)", f"{VAL}; {EVA}", "per-trace", "max over 16 traces")
    R.pct("rich_min_eval", min(px["RICH"].values()), "smallest held-out per-workload capture of the rich policy", EVA, "miss_bytes_per_trace", "min over 4 traces")
    R.pct("rich_max_eval", max(px["RICH"].values()), "largest held-out per-workload capture of the rich policy", EVA, "miss_bytes_per_trace", "max over 4 traces")
    R.pct("cheap_min_val", min(pv["CHEAP"].values()), "most negative validation trace capture of the cheap policy", VAL, "per_workload.*.CHEAP", "min over 12 traces")
    R.pct("cheap_max_val", max(pv["CHEAP"].values()), "least negative validation trace capture of the cheap policy", VAL, "per_workload.*.CHEAP", "max over 12 traces")
    nneg = sum(1 for v in pv["DECAYED_COUNT"].values() if v < 0)
    R.fact("dec_neg_traces", "eight of the twelve", nneg == 8, "validation traces on which decayed count is worse than A", VAL, "per_workload.*.DECAYED_COUNT", "count(capture<0) == 8", p_txt("eight of the twelve"))
    R.fact("nue_pos16", "positive on all sixteen (NUE-X)", all(v > 0 for v in allt("NUE_X")), "NUE-X positive on every trace", f"{VAL}; {EVA}", "per-trace", "all(capture>0) over 16", p_txt("positive on all sixteen"))
    R.fact("rich_pos16", "positive on all sixteen (rich)", all(v > 0 for v in allt("RICH")), "rich policy positive on every trace", f"{VAL}; {EVA}", "per-trace", "all(capture>0) over 16", p_txt("is positive on all sixteen"))
    R.fact("cheap_neg16", "negative on all sixteen (cheap)", all(v < 0 for v in allt("CHEAP")), "cheap policy negative on every trace", f"{VAL}; {EVA}", "per-trace", "all(capture<0) over 16", p_txt("negative on all sixteen"))
    R.fact("cheap_neg12", "worse than baseline on all twelve", all(v < 0 for v in pv["CHEAP"].values()), "cheap policy negative on every validation trace", VAL, "per_workload.*.CHEAP.sign", "all(sign == '-')", p_txt("all twelve traces"))
    R.fact("cheap_neg_eval", "negative on every workload", all(v < 0 for v in px["CHEAP"].values()) and F["dec"]["rule_C"]["passed"] is False, "cheap policy negative on every held-out workload", f"{EVA}; {DEC}", "rule_C.per_workload_sign", "all(capture<0); rule C failed", p_txt("negative on every workload"))
    R.fact("none_reaches25", "no policy reaches 25% on any trace", all(v < Fraction(1, 4) for a in frozen.ARMS for v in allt(a)), "no single-trace capture reaches 25%", f"{VAL}; {EVA}", "per-trace", "max < 1/4 over all arms and traces", p_txt("No policy reaches $25\\%$ on any individual trace"))
    R.fact("cheap_beats_none", "cheap exceeds best comparator on none", F["ev"]["nongating"]["eval_workloads_where_cheap_beats_best_comparator"]["count"] == 0, "held-out workloads where cheap beats the better comparator", EVA, "nongating.eval_workloads_where_cheap_beats_best_comparator.count", "== 0", p_txt("on none of the four workloads"))
    # --- non-gating: bootstrap, sensitivity, diagnostics -----------------------------------------------------------------------
    bs = F["ev"]["nongating"]["block_bootstrap_90pct_pooled_capture"]
    R.fact("bs_params", "32 blocks, 2,000 resamples", all(bs[a]["n_blocks"] == 32 and bs[a]["resamples"] == 2000 for a in bs), "bootstrap design", EVA, "nongating.block_bootstrap_90pct_pooled_capture.*", "n_blocks == 32, resamples == 2000", r"32 contiguous 64-token decode blocks \(2\{,\}000 resamples")
    for a in frozen.ARMS:
        lo, hi = spct(bs[a]["p05"]), spct(bs[a]["p95"])
        R.add(f"bs_{a}", f"[{lo}%, {hi}%]", f"90% block-bootstrap interval of the held-out capture of {NAME[a]}", EVA, f"nongating.block_bootstrap_90pct_pooled_capture.{a}.p05/p95", "5th and 95th percentiles", "2 dp, % of 1", re.escape("[" + lo + "\\%, " + hi + "\\%]"), [lo, hi])
    sens = F["val"]["sensitivity_excluding_r2-11-coding-v1_NONGATING"]
    for a in frozen.ARMS:
        R.pct(f"sens_{a}", Fraction(sens[a]["capture"]), f"validation capture of {NAME[a]} excluding the canonical-stream trace (non-gating)", VAL, f"sensitivity_excluding_r2-11-coding-v1_NONGATING.{a}.capture", "stored exact rational")
    ta, ch = F["tm"], F["chm"]; sd = ch["train_shift_diagnostic_nongating"]
    nA = sum(x["agree"] for x in sd["agree_under_A_states"]); sA = sum(x["sampled"] for x in sd["agree_under_A_states"])
    nC = sum(x["agree"] for x in sd["agree_under_CHEAP_states"]); sC = sum(x["sampled"] for x in sd["agree_under_CHEAP_states"])
    R.pct("agree_A", Fraction(nA, sA), "victim = teacher victim, baseline trajectory, TRAIN (non-gating)", CHM, "train_shift_diagnostic_nongating.agree_under_A_states", "sum(agree)/sum(sampled)")
    R.pct("agree_C", Fraction(nC, sC), "victim = teacher victim, cheap policy on its own states, TRAIN (non-gating)", CHM, "train_shift_diagnostic_nongating.agree_under_CHEAP_states", "sum(agree)/sum(sampled)")
    R.big("n_dec_A", sA, "sampled decisions in the baseline-trajectory diagnostic", CHM, "train_shift_diagnostic_nongating.agree_under_A_states[*].sampled", "sum")
    R.big("n_dec_C", sC, "sampled decisions in the own-state diagnostic", CHM, "train_shift_diagnostic_nongating.agree_under_CHEAP_states[*].sampled", "sum")
    R.big("n_rows", ch["train_audit"]["n_train_rows"], "candidate rows used to fit the cheap tree", CHM, "train_audit.n_train_rows", "as stored")
    R.fact("n_train_dec", "6,951", ch["train_audit"]["n_train_decisions"] == ta["total_sampled_decisions"] == 6951 == sA, "labelled eviction decisions", f"{CHM}; {TMAN}", "train_audit.n_train_decisions; total_sampled_decisions", "equal and == 6951", r"6\{,\}951")
    tc = ch["tree"]["config"]
    R.fact("tree_cfg", "depth 3, 4 features, 1,000 rows, 50 decisions", tc["max_depth"] == 3 and tc["max_distinct_features"] == 4 and tc["min_samples_leaf"] == 1000 and tc["min_decisions_per_leaf"] == 50, "frozen cheap-tree constraints", CHM, "tree.config", "as stored", r"at least 1\{,\}000 rows from at least 50 decisions")
    R.fact("budget", "1,610,612,736", F["val"]["budget_bytes"] == F["ev"]["budget_bytes"] == 1610612736, "expert budget in bytes (1.5 GiB)", f"{VAL}; {EVA}", "budget_bytes", "== 1.5 * 2**30", r"1\{,\}610\{,\}612\{,\}736")
    R.fact("exec24", "24 executions", len(F["ev"]["miss_bytes_per_trace"]) * 6 == 24, "held-out (trace, arm) executions", f"{P2}/PROVENANCE.json", "eval_executions", "4 traces x 6 arms == 24", r"24 executions")
    # --- classification re-derivation -----------------------------------------------------------------------------------------
    q = Fraction(1, 4)
    A = cV["CHEAP"] >= q and cX["CHEAP"] >= q
    B = cX["CHEAP"] >= max(q, max(cX["NUE_X"], cX["DECAYED_COUNT"]) + Fraction(3, 100))
    C = all(v >= 0 for v in px["CHEAP"].values()); gate = cV["RICH"] >= q and cX["RICH"] >= q
    order = dict((k, v) for k, v in F["rules"]["classification_order"])
    cls = order["A and B and C"] if (A and B and C) else order["A and B and not C"] if (A and B) else order["A and not B"] if A else order["not A and RICH gate passes"] if gate else order["not A and RICH gate fails"]
    assert cls == F["fin"]["classification"] == F["dec"]["classification"], (cls, F["fin"]["classification"])
    R.fact("classification", cls, cls == "R4_CFDC_CAUSAL_STATE_INSUFFICIENT", "final classification re-derived from recomputed captures", f"{P2}/FINAL_CLASSIFICATION.json", "classification", "first matching row of DECISION_RULES.classification_order", p_txt("R4\\_CFDC\\_CAUSAL\\_STATE\\_INSUFFICIENT"))
    R.fact("state", F["fin"]["r4_cfdc_state"], F["fin"]["r4_cfdc_state"] == "R4_CFDC_CLOSED", "closure state", f"{P2}/FINAL_CLASSIFICATION.json", "r4_cfdc_state", "as stored", p_txt("R4\\_CFDC\\_CLOSED"))
    R.fact("threshold", "25%", F["rules"]["thresholds"]["viability"] == "1/4" and F["rules"]["thresholds"]["rich_gate"] == "1/4" and F["rules"]["thresholds"]["incremental_margin"] == "3/100", "frozen thresholds 1/4, 1/4, 3/100", f"{P0}/DECISION_RULES.json", "thresholds", "as stored", p_txt("\\tfrac{3}{100}"))
    R.fact("r3label", F["f0"]["research3"]["classification"], True, "preceding study's frozen label", f"{P0}/FREEZE.json", "research3.classification", "as stored", p_txt(F["f0"]["research3"]["classification"].replace("_", "\\_")))
    # --- preceding-study figures --------------------------------------------------------------------------------------------
    hc = frozen.jl(R3HC); cl = frozen.jl(R3CL)["primary_1.5GiB"]["P_minus_A_ms_per_token"]
    R.pct("r3_capture", hc["captured"]["NUE_X"]["fraction_of_belady_byte_headroom"], "preceding study: NUE-X share of clairvoyant byte headroom on its canonical stream", R3HC, "captured.NUE_X.fraction_of_belady_byte_headroom", "as stored")
    ms = rnd(cl["mean"], 2)
    R.add("r3_ms", f"{ms} ms/token", "preceding study: corrected predictive policy minus baseline, ms/token", R3CL, "primary_1.5GiB.P_minus_A_ms_per_token.mean", "as stored", "2 dp", re.escape(ms) + r"\$~ms/token", [])
    R.add("r3_ci", f"[{rnd(cl['lo'],2)}, {rnd(cl['hi'],2)}]", "preceding study: 95% CI of the paired difference", R3CL, "primary_1.5GiB.P_minus_A_ms_per_token.lo/hi", "as stored", "2 dp", re.escape(f"[{rnd(cl['lo'],2)}, {rnd(cl['hi'],2)}]"), [])
    # --- fitted tree (Appendix C) -------------------------------------------------------------------------------------------------
    for i, (rk, my, nr) in enumerate(leaves(ch["tree"])):
        R.add(f"leaf{i}", f"rank {rk}, mean y {my:.3f}, rows {nr}", f"fitted cheap tree, leaf {i} in depth-first order", CHM, "tree.leaf_rank/node_stats", "depth-first traversal", "3 dp", r"& " + str(rk) + r" & " + re.escape(f"{my:.3f}") + r" & " + re.escape(big(nr)) + r" \\\\")
    # --- identities (Appendix A) ----------------------------------------------------------------------------------------------------
    f0, f1, f2 = F["f0"], F["f1"], F["f2"]
    for cid, h, art, fld in (("dg0", f0["contract_digest"], f"{P0}/FREEZE.json", "contract_digest"), ("fz0", f0["freeze_sha256"], f"{P0}/FREEZE.json", "freeze_sha256"),
                             ("dg1", f1["contract_digest"], f"{P1}/FREEZE.json", "contract_digest"), ("fz1", f1["freeze_sha256"], f"{P1}/FREEZE.json", "freeze_sha256"),
                             ("im1", f1["implementation_commit"], f"{P1}/FREEZE.json", "implementation_commit"), ("dg2", f2["contract_digest"], f"{P2}/FREEZE.json", "contract_digest"),
                             ("fz2", f2["freeze_sha256"], f"{P2}/FREEZE.json", "freeze_sha256"), ("r3fz", frozen.jl("experiments/R3-final-closure/FREEZE.json")["freeze_sha256"], "experiments/R3-final-closure/FREEZE.json", "freeze_sha256")):
        R.add(cid, h, f"identity {cid}", art, fld, "as stored", "full 40/64 hex", re.escape(h))
    p0c = frozen.jl(f"{P1}/PROVENANCE.json")["phase0_commit"]
    R.add("c0", p0c, "protocol freeze commit", f"{P1}/PROVENANCE.json", "phase0_commit", "as stored", "full 40 hex", re.escape(p0c))
    p2 = frozen.jl(f"{P2}/PROVENANCE.json"); eo = F["opened"]
    R.add("open2", p2["opening_commit"], "evaluation opening commit", f"{P2}/PROVENANCE.json", "opening_commit", "as stored", "full 40 hex", re.escape(p2["opening_commit"]))
    R.add("lock1", eo["starting_head"], "commit at which validation was locked and evaluation opened", f"{P2}/EVAL_OPENED.json", "starting_head", "as stored", "full 40 hex", re.escape(eo["starting_head"]))
    for ident in eo["eval_trace_identities"]:
        R.add(f"trace_{ident['category']}", ident["expected_trace_sha256"], f"held-out {ident['category']} trace identity", f"{P2}/EVAL_OPENED.json", "eval_trace_identities[*].expected_trace_sha256", "as stored", "full 64 hex", re.escape(ident["category"] + ": ") + r"\\hsh\{" + re.escape(ident["expected_trace_sha256"]) + r"\}")
    return R.rows


def table_rows_text(rows):
    return rows
