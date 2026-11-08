#!/usr/bin/env python3
r"""Generate Research2 paper tables (LaTeX fragments, \input{}-ready) from
the frozen canonical data snapshot under paper/research2/data/.

II-A tables (preserved):
  table1_configuration.tex, table2_r2_02_comparison.tex,
  table3_r2_03_sweep.tex, table4_r2_04_synthesis.tex

II-B tables (II-B freeze):
  table_b1_iib_map.tex
  table_b2_r2_05_concentration.tex
  table_b3_r2_06_transitions.tex
  table_b4_r2_07_reuse_locality.tex
  table_b5_r2_08_scale.tex

Usage: python3 generate_tables.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
GIB = 1 << 30


def _load(experiment: str, fname: str) -> dict:
    return json.loads((DATA / experiment / fname).read_text(encoding="utf-8"))


def _fmt_bytes_gb(n: int) -> str:
    return f"{n / 1e9:.2f}"


def _fmt_pct(x) -> str:
    return "---" if x is None else f"{x * 100:.2f}\\%"


def _f(x, d=4) -> str:
    if x is None:
        return "---"
    return f"{float(x):.{d}f}"


def _short(h: str, n: int = 12) -> str:
    return h[:n]


# ---------------------------------------------------------------------------
# II-A (preserved)
# ---------------------------------------------------------------------------

def table1() -> str:
    _load("r2_01", "summary.json")
    r2_03_prov = _load("r2_03", "provenance.json")
    budgets_gib = [b / GIB for b in r2_03_prov["fixed_budgets_bytes"]]
    budgets_str = ", ".join(f"{b:g}" for b in budgets_gib)
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Experimental configuration for the II-A tranche.}",
        r"\label{tab:configuration}",
        r"\small",
        r"\begin{tabular}{@{}lp{7.9cm}@{}}",
        r"\toprule",
        r"Workload trace source & \texttt{run-00-4G} (coding workload, 30B-A3B MoE) \\",
        r"Phase analyzed & decode \\",
        rf"Expert-memory budgets tested (GiB) & {budgets_str} \\",
        r"Reproduction (anchor) budget & 4~GiB \\",
        r"Causal baselines & Runtime-Sharded-LRU, Global-LRU, LFU \\",
        r"Clairvoyant comparator ($O_B$) & \textsc{Oracle-Global-Belady} / \texttt{BeladyGlobal}: Belady-style farthest-next-use, offline full-future; not proven byte-optimal under variable expert sizes (CASE~C) \\",
        r"Replay semantics & byte-budgeted residency, per-expert-per-layer granularity \\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    return "\n".join(lines) + "\n"


def table2() -> str:
    policies = _load("r2_02", "policy_results.json")["policies"]
    order = ["Runtime-Sharded-LRU", "Global-LRU", "LFU", "Oracle-Global-Belady"]
    rows_by_label = {r["policy"]: r for r in policies}
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Policy comparison at the 4~GiB anchor budget (R2-02).}",
        r"\label{tab:r2-02-comparison}",
        r"\small",
        r"\begin{tabular}{@{}lrrrr@{}}",
        r"\toprule",
        r"Policy & Causal & Misses & Miss rate & Miss bytes (GB) \\",
        r"\midrule",
    ]
    for label in order:
        r = rows_by_label[label]
        causal = "yes" if r["policy_metadata"]["causal"] else "no"
        lines.append(
            f"{label} & {causal} & {r['misses']:,} & {r['miss_rate']:.4f} & "
            f"{_fmt_bytes_gb(r['miss_bytes'])} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def table3() -> str:
    budget_results = _load("r2_03", "budget_results.json")
    gaps = _load("r2_03", "comparison.json")["oracle_gaps_by_budget"]
    budgets = sorted(int(b) for b in budget_results)
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Global-LRU vs.\ clairvoyant comparator $O_B$ "
        r"(\textsc{Oracle-Global-Belady}) across the tested expert-memory "
        r"budget sweep (R2-03).}",
        r"\label{tab:r2-03-sweep}",
        r"\footnotesize",
        r"\setlength{\tabcolsep}{4pt}",
        r"\begin{tabular}{@{}rrrrr@{}}",
        r"\toprule",
        r"Budget (GiB) & Global-LRU misses & $O_B$ misses & "
        r"Rel.\ reduction & Abs.\ reduction (GB) \\",
        r"\midrule",
    ]
    for b in budgets:
        rows = {r["policy"]: r for r in budget_results[str(b)]}
        glru, oracle = rows["Global-LRU"], rows["Oracle-Global-Belady"]
        gap = gaps[str(b)]["Global-LRU"]
        label = b / GIB
        lines.append(
            f"{label:g} & {glru['misses']:,} & {oracle['misses']:,} & "
            f"{_fmt_pct(gap['relative_miss_byte_reduction'])} & "
            f"{_fmt_bytes_gb(gap['miss_byte_reduction'])} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def table3b_lfu_auxiliary() -> str:
    """Canonical auxiliary R2-03 evidence: LFU vs Global-LRU across sweep.

    Values are derived mechanically from frozen paper/research2/data/r2_03
    artifacts; no independent numeric source is introduced.
    """
    budget_results = _load("r2_03", "budget_results.json")
    budgets = sorted(int(b) for b in budget_results)
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Canonical auxiliary R2-03 evidence: LFU vs.\ Global-LRU "
        r"across the tested expert-memory budget sweep. LFU and "
        r"Runtime-Sharded-LRU were replayed as part of the declared R2-02 "
        r"policy set at every budget; the preregistered primary R2-03 "
        r"comparison remains Global-LRU vs.\ $O_B$ "
        r"(Table~\ref{tab:r2-03-sweep}).}",
        r"\label{tab:r2-03-lfu-aux}",
        r"\footnotesize",
        r"\setlength{\tabcolsep}{4pt}",
        r"\begin{tabular}{@{}rrrrrl@{}}",
        r"\toprule",
        r"Budget (GiB) & Global-LRU miss bytes & LFU miss bytes & "
        r"LFU $-$ Global-LRU (bytes) & Rel.\ diff. & Result \\",
        r"\midrule",
    ]
    for b in budgets:
        rows = {r["policy"]: r for r in budget_results[str(b)]}
        glru, lfu = rows["Global-LRU"], rows["LFU"]
        diff = lfu["miss_bytes"] - glru["miss_bytes"]
        rel = (diff / glru["miss_bytes"]) if glru["miss_bytes"] else 0.0
        label = b / GIB
        if diff == 0:
            rel_s = "0.00\\%"
            result = "tie"
        elif diff > 0:
            rel_s = f"+{rel * 100:.2f}\\%"
            result = "LFU loses"
        else:
            rel_s = f"{rel * 100:.2f}\\%"
            result = "LFU beats"
        lines.append(
            f"{label:g} & {glru['miss_bytes']:,} & {lfu['miss_bytes']:,} & "
            f"{diff:+,} & {rel_s} & {result} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def table4() -> str:
    margin = _load("r2_04", "recoverable_margin.json")
    summary = _load("r2_04", "summary.json")
    metrics = summary["summary_metrics"]
    peak = margin["peak_opportunity"]
    eff = margin["effective_residency_budget"]
    curve_shape = margin["curve_shape"]["classification"]
    persistence = margin["headroom_persistence"]["oracle_strictly_improves_at_every_capacity_constrained_point"]

    def gib(bb):
        return f"{bb / GIB:g}" if bb is not None else "---"

    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Headline II-A synthesis (R2-04): clairvoyant-comparator "
        r"gap decomposition summary.}",
        r"\label{tab:r2-04-synthesis}",
        r"\small",
        r"\begin{tabular}{@{}lr@{}}",
        r"\toprule",
        r"Quantity & Value \\",
        r"\midrule",
        rf"Belady comparator gap at 4~GiB (anchor) & "
        rf"{_fmt_pct(metrics['information_headroom_fraction_at_reproduction_budget'])} \\",
        rf"Maximum tested relative Belady comparator gap & "
        rf"{_fmt_pct(peak['max_relative_miss_byte_headroom']['value'])} "
        rf"(at {gib(peak['max_relative_miss_byte_headroom']['budget_bytes'])}~GiB) \\",
        rf"Maximum tested absolute miss-byte comparator gap (GB) & "
        rf"{_fmt_bytes_gb(peak['max_absolute_miss_byte_headroom']['value'])} "
        rf"(at {gib(peak['max_absolute_miss_byte_headroom']['budget_bytes'])}~GiB) \\",
        rf"First tested policy-indifference budget (GiB) & {gib(eff)} \\",
        rf"Comparator miss bytes lower at every capacity-constrained tested point & "
        rf"{'yes' if persistence else 'no'} \\",
        rf"Observed comparator-gap curve shape & "
        rf"{curve_shape.replace('_', ' ')} \\",
        r"Causal recovery relative to comparator gap & \textsc{not yet measured} (\S~II-C) \\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# II-B
# ---------------------------------------------------------------------------

def table_b1_map() -> str:
    s5 = _load("r2_05", "summary.json")
    s6 = _load("r2_06", "summary.json")
    s7 = _load("r2_07", "summary.json")
    s8 = _load("r2_08", "summary.json")
    rows = [
        ("R2-05", "Expert frequency and concentration",
         "normalized entropy, effective experts, top-8 share, 80\\% mass",
         _short(s5["run_id"]), "PASS"),
        ("R2-06", "Routing transition structure",
         "same-layer NMI, conditional entropy, self-transition lift",
         _short(s6["run_id"]), "PASS"),
        ("R2-07", "Reuse distance and working-set structure",
         "reuse fractions, gaps, distinct-intervening distance, working set, shuffled null",
         _short(s7["run_id"]), "PASS"),
        ("R2-08", "Routing drift and workload dependence",
         "temporal/replicate/cross-workload JSD, top-8 Jaccard distance, regime",
         _short(s8["run_id"]), "PASS"),
    ]
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Research II-B experiment map (canonical PASS evidence only).}",
        r"\label{tab:iib-map}",
        r"\small",
        r"\begin{tabular}{@{}p{1.0cm}p{3.2cm}p{5.0cm}ll@{}}",
        r"\toprule",
        r"Exp. & Question & Primary metrics & Run & Outcome \\",
        r"\midrule",
    ]
    for r in rows:
        lines.append(f"{r[0]} & {r[1]} & {r[2]} & \\texttt{{{r[3]}\\ldots}} & {r[4]} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def table_b2_r2_05() -> str:
    h = _load("r2_05", "summary.json")["headline"]
    rows = [
        ("Total routing requests (token-expert assignments)", f"{h['total_routing_requests']:,}"),
        ("Layers / nominal experts per layer / top-$k$", f"{h['layer_count']} / {h['nominal_experts_per_layer']} / {h['top_k_recorded_in_trace_meta']}"),
        ("Median normalized entropy", _f(h["median_normalized_entropy"], 4)),
        ("Median effective expert fraction", _f(h["median_effective_expert_fraction"], 4)),
        (r"Median effective experts ($\approx$)", f"{h['median_effective_expert_fraction'] * h['nominal_experts_per_layer']:.1f} / {h['nominal_experts_per_layer']}"),
        ("Median top-8 traffic share", _f(h["median_top_8_share"], 4)),
        ("Top-8 share range (min--max)", f"{_f(h['min_top_8_share'],4)}--{_f(h['max_top_8_share'],4)}"),
        ("Median experts for 80\\% traffic", f"{h['experts_for_80pct_median']:.0f}"),
        ("80\\% mass range (min--max experts)", f"{h['experts_for_80pct_min']}--{h['experts_for_80pct_max']}"),
        ("Most / least concentrated layer", f"{h['most_concentrated_layer']} / {h['least_concentrated_layer']}"),
    ]
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{R2-05 expert frequency and concentration summary (descriptive, layer-local).}",
        r"\label{tab:r2-05-summary}",
        r"\small",
        r"\begin{tabular}{@{}lr@{}}",
        r"\toprule",
        r"Quantity & Value \\",
        r"\midrule",
    ]
    for k, v in rows:
        lines.append(f"{k} & {v} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def table_b3_r2_06() -> str:
    h = _load("r2_06", "summary.json")["headline"]
    rows = [
        ("Pairwise set-to-set transition observations", f"{h['total_transition_observations']:,}"),
        ("Layers / nominal experts per layer", f"{h['layer_count']} / {h['nominal_experts_per_layer']}"),
        ("Median same-layer NMI", _f(h["median_nmi"], 4)),
        ("NMI range (min--max)", f"{_f(h['min_nmi'],4)}--{_f(h['max_nmi'],4)}"),
        ("Layers with positive NMI", _f(h["fraction_layers_with_nmi_positive"], 2)),
        ("Median conditional entropy", _f(h["median_conditional_entropy"], 4)),
        ("Median self-transition lift", _f(h["median_self_transition_lift"], 4)),
        ("Median top-1 outgoing probability", _f(h["median_top_1_outgoing_probability"], 4)),
        ("Median outgoing effective-target fraction", _f(h["median_outgoing_effective_target_fraction"], 4)),
        ("Maximum supported transition lift (layer)", f"{_f(h['max_supported_transition_lift'],2)} (L{h['max_supported_transition_lift_layer']})"),
        ("Strongest / weakest NMI layer", f"{h['strongest_layer_by_nmi']} / {h['weakest_layer_by_nmi']}"),
    ]
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{R2-06 routing transition structure summary (same-layer, pairwise set-to-set observations).}",
        r"\label{tab:r2-06-summary}",
        r"\small",
        r"\begin{tabular}{@{}lr@{}}",
        r"\toprule",
        r"Quantity & Value \\",
        r"\midrule",
    ]
    for k, v in rows:
        lines.append(f"{k} & {v} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def table_b4_r2_07() -> str:
    h = _load("r2_07", "summary.json")["headline"]
    ovn = h["observed_vs_null"]
    ws = h["median_working_set_size_by_window"]
    rows = [
        ("Reuse events / first observations", f"{h['total_reuse_events']:,} / {h['total_first_observations']:,}"),
        ("Median immediate reuse (gap=1)", _f(h["median_immediate_reuse_fraction"], 4)),
        ("Median reuse $\\le 4$ / $\\le 16$ positions", f"{_f(h['median_reuse_within_4_fraction'],4)} / {_f(h['median_reuse_within_16_fraction'],4)}"),
        ("Median token reuse gap / p90 gap", f"{_f(h['median_token_reuse_gap'],0).rstrip('0').rstrip('.')} / {_f(h['median_p90_token_reuse_gap'],0).rstrip('0').rstrip('.')}"),
        ("Median distinct-intervening reuse distance", _f(h["median_distinct_intervening_reuse_distance"], 0).rstrip("0").rstrip(".")),
        ("Median working set $W_8/W_{32}/W_{128}/W_{256}$", f"{ws['8']}/{ws['32']}/{ws['128']}/{ws['256']}"),
        ("Frequency--reuse-gap Spearman (descriptive)", _f(h["frequency_vs_reuse_gap_spearman_correlation"], 4)),
        ("Immediate reuse: observed / null / ratio", f"{_f(ovn['immediate_reuse_fraction']['median_observed'],4)} / {_f(ovn['immediate_reuse_fraction']['median_null'],4)} / {_f(ovn['immediate_reuse_fraction']['ratio_observed_to_null'],2)}$\\times$"),
        ("Reuse $\\le 4$: observed / null / ratio", f"{_f(ovn['reuse_within_4_fraction']['median_observed'],4)} / {_f(ovn['reuse_within_4_fraction']['median_null'],4)} / {_f(ovn['reuse_within_4_fraction']['ratio_observed_to_null'],2)}$\\times$"),
        ("Reuse $\\le 16$: observed / null / ratio", f"{_f(ovn['reuse_within_16_fraction']['median_observed'],4)} / {_f(ovn['reuse_within_16_fraction']['median_null'],4)} / {_f(ovn['reuse_within_16_fraction']['ratio_observed_to_null'],2)}$\\times$"),
        ("Median gap: observed / null", f"{_f(ovn['median_token_reuse_gap']['median_observed'],0).rstrip('0').rstrip('.')} / {_f(ovn['median_token_reuse_gap']['median_null'],0).rstrip('0').rstrip('.')}"),
        ("Distinct-intervening: observed / null", f"{_f(ovn['median_distinct_intervening_reuse_distance']['median_observed'],0).rstrip('0').rstrip('.')} / {_f(ovn['median_distinct_intervening_reuse_distance']['median_null'],0).rstrip('0').rstrip('.')}"),
    ]
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{R2-07 reuse distance and working-set summary (observed vs deterministic shuffled null).}",
        r"\label{tab:r2-07-summary}",
        r"\small",
        r"\begin{tabular}{@{}lr@{}}",
        r"\toprule",
        r"Quantity & Value \\",
        r"\midrule",
    ]
    for k, v in rows:
        lines.append(f"{k} & {v} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def table_b5_r2_08() -> str:
    s = _load("r2_08", "summary.json")
    h = s["headline"]
    comp = s["comparison"]
    pair = h["strongest_workload_pair"]
    rows = [
        ("Matrix", f"{h['workload_count']} workloads $\\times$ 2 replicates = {h['canonical_capture_count']} cells"),
        ("Reused / new captures", f"{h['reused_capture_count']} / {h['newly_performed_capture_count']}"),
        ("Matched decode horizon $H$", f"{h['matched_decode_horizon']}"),
        ("Temporal windows / window length", f"{h['matched_decode_horizon'] // h['temporal_window_length']} / {h['temporal_window_length']}"),
        ("Median temporal JSD", _f(h["median_temporal_jsd"], 6)),
        ("Median same-workload replicate JSD", _f(h["median_same_workload_replicate_jsd"], 1) + r"\,\footnotemark[1]"),
        ("Median cross-workload JSD", _f(h["median_cross_workload_jsd"], 6)),
        ("Cross-workload / temporal ratio", _f(h["cross_workload_to_temporal_ratio"], 4) + "$\\times$"),
        ("Median top-8 temporal Jaccard distance", _f(h["median_top8_temporal_jaccard_distance"], 6)),
        ("Predeclared regime", comp["regime"].replace("_", "\\_")),
        ("Most / least stable workload", f"{h['most_stable_workload']} / {h['least_stable_workload']}"),
        ("Most / least stable layer", f"{h['most_stable_layer']} / {h['least_stable_layer']}"),
        ("Strongest workload pair (median layer JSD)", f"{pair['workload_a']} (s{pair['seed_a']}) vs {pair['workload_b']} (s{pair['seed_b']}), {_f(pair['median_layer_frequency_jsd'],6)}"),
        ("Configuration compatibility", "true" if h["all_configuration_fingerprints_compatible"] else "false"),
    ]
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{R2-08 routing drift and workload-dependence scale comparison.}",
        r"\label{tab:r2-08-summary}",
        r"\small",
        r"\begin{tabular}{@{}lr@{}}",
        r"\toprule",
        r"Quantity & Value \\",
        r"\midrule",
    ]
    for k, v in rows:
        lines.append(f"{k} & {v} \\\\")
    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\vspace{2pt}",
        r"\par\footnotesize\textsuperscript{1}Under the deterministic decoding protocol (temperature~$=0.0$, top-$k=1$); interpreted as a reproducibility check at the reported resolution, not as a general claim of zero stochastic run-to-run variability.",
        r"\end{table}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    # II-A
    (HERE / "table1_configuration.tex").write_text(table1(), encoding="utf-8")
    (HERE / "table2_r2_02_comparison.tex").write_text(table2(), encoding="utf-8")
    (HERE / "table3_r2_03_sweep.tex").write_text(table3(), encoding="utf-8")
    (HERE / "table3b_r2_03_lfu_auxiliary.tex").write_text(table3b_lfu_auxiliary(), encoding="utf-8")
    (HERE / "table4_r2_04_synthesis.tex").write_text(table4(), encoding="utf-8")
    # II-B
    (HERE / "table_b1_iib_map.tex").write_text(table_b1_map(), encoding="utf-8")
    (HERE / "table_b2_r2_05_concentration.tex").write_text(table_b2_r2_05(), encoding="utf-8")
    (HERE / "table_b3_r2_06_transitions.tex").write_text(table_b3_r2_06(), encoding="utf-8")
    (HERE / "table_b4_r2_07_reuse_locality.tex").write_text(table_b4_r2_07(), encoding="utf-8")
    (HERE / "table_b5_r2_08_scale.tex").write_text(table_b5_r2_08(), encoding="utf-8")
    print("wrote II-A table fragments (5) and II-B table fragments (5)")


if __name__ == "__main__":
    main()
