#!/usr/bin/env python3
"""Generate the II-A paper tables (LaTeX fragments, \\input{}-ready) from
the frozen canonical data snapshot under paper/research2/data/ -- never
hand-typed datapoints.

Usage: python3 generate_tables.py
Outputs: table1_configuration.tex
         table2_r2_02_comparison.tex
         table3_r2_03_sweep.tex
         table4_r2_04_synthesis.tex
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


def table1() -> str:
    r2_01_summary = _load("r2_01", "summary.json")
    r2_03_prov = _load("r2_03", "provenance.json")
    budgets_gib = [b / GIB for b in r2_03_prov["fixed_budgets_bytes"]]
    budgets_str = ", ".join(f"{b:g}" for b in budgets_gib)
    trace_sha = r2_01_summary["trace_source"]
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Experimental configuration for the II-A tranche.}",
        r"\label{tab:configuration}",
        r"\small",
        r"\begin{tabular}{@{}ll@{}}",
        r"\toprule",
        r"Workload trace source & \texttt{run-00-4G} (coding workload, 30B-A3B MoE) \\",
        r"Phase analyzed & decode \\",
        rf"Expert-memory budgets tested (GiB) & {budgets_str} \\",
        r"Reproduction (anchor) budget & 4~GiB \\",
        r"Causal baselines & Runtime-Sharded-LRU, Global-LRU, LFU \\",
        r"Non-causal upper bound & Oracle-Global-Belady (offline, full future knowledge) \\",
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
        r"\caption{Global-LRU vs.\ Oracle-Global-Belady across the tested "
        r"expert-memory budget sweep (R2-03).}",
        r"\label{tab:r2-03-sweep}",
        r"\small",
        r"\begin{tabular}{@{}rrrrr@{}}",
        r"\toprule",
        r"Budget (GiB) & Global-LRU misses & Oracle misses & "
        r"Rel.\ miss-byte reduction & Abs.\ miss-byte reduction (GB) \\",
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
        r"\caption{Headline II-A synthesis (R2-04): oracle-bounded "
        r"recoverable-envelope summary.}",
        r"\label{tab:r2-04-synthesis}",
        r"\small",
        r"\begin{tabular}{@{}lr@{}}",
        r"\toprule",
        r"Quantity & Value \\",
        r"\midrule",
        rf"Information headroom at 4~GiB (anchor) & "
        rf"{_fmt_pct(metrics['information_headroom_fraction_at_reproduction_budget'])} \\",
        rf"Maximum tested relative information headroom & "
        rf"{_fmt_pct(peak['max_relative_miss_byte_headroom']['value'])} "
        rf"(at {gib(peak['max_relative_miss_byte_headroom']['budget_bytes'])}~GiB) \\",
        rf"Maximum tested absolute miss-byte headroom (GB) & "
        rf"{_fmt_bytes_gb(peak['max_absolute_miss_byte_headroom']['value'])} "
        rf"(at {gib(peak['max_absolute_miss_byte_headroom']['budget_bytes'])}~GiB) \\",
        rf"First tested policy-indifference budget (GiB) & {gib(eff)} \\",
        rf"Oracle strictly improves at every capacity-constrained point & "
        rf"{'yes' if persistence else 'no'} \\",
        rf"Observed information-headroom curve shape & "
        rf"{curve_shape.replace('_', ' ')} \\",
        r"Causally recovered margin & \textsc{not yet measured} (\S~II-C) \\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    (HERE / "table1_configuration.tex").write_text(table1(), encoding="utf-8")
    (HERE / "table2_r2_02_comparison.tex").write_text(table2(), encoding="utf-8")
    (HERE / "table3_r2_03_sweep.tex").write_text(table3(), encoding="utf-8")
    (HERE / "table4_r2_04_synthesis.tex").write_text(table4(), encoding="utf-8")
    print("wrote 4 table fragments")


if __name__ == "__main__":
    main()
