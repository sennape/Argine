

# ---------------------------------------------------------------------------
# II-C tables (added for the Research 2 final closure / Paper II freeze)
# ---------------------------------------------------------------------------

def table_c1_milestone_summary() -> str:
    rows = [
        ("R2-09A/B", "Freeze benchmark contract; DEV causal-baseline tournament", "CLOSED"),
        ("R2-10", "Held-out EVAL of selected predictor (decayed\\_transition)", "CLOSED / Category B supported"),
        ("R2-BRIDGE-01", "Prospective replay retention feasibility check", "CLOSED / BRIDGE\\_POSITIVE"),
        ("R2-11", "Confirmatory replay retention evaluation, 72 cells", "CLOSED / R2\\_11\\_PASS"),
        ("R2-12", "Real-runtime validation, 1.5/4/8 GiB", "CLOSED / R2\\_12\\_FAIL (primary)"),
    ]
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Research II-C milestone summary.}",
        r"\label{tab:c1-milestones}",
        r"\small",
        r"\begin{tabular}{@{}lp{6.3cm}l@{}}",
        r"\toprule",
        r"Milestone & Purpose & Closure state \\",
        r"\midrule",
    ]
    for m, p, s in rows:
        lines.append(f"{m} & {p} & {s} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def table_c2_predictor_performance() -> str:
    d = _load("r2_10", "PHASE3D-EVAL-RESULTS.json")
    cells = d["per_cell_recall_at_k"]
    rows = []
    labels = [
        ("decayed_transition/decay=0.99", "decayed\\_transition (selected)"),
        ("last_observation/default", "last\\_observation"),
        ("global_frequency/default", "global\\_frequency"),
    ]
    for key, label in labels:
        h1 = cells[key]["h1"]
        h4 = cells[key]["h4"]
        h16 = cells[key]["h16"]
        m1 = sum(h1.values()) / len(h1)
        m4 = sum(h4.values()) / len(h4)
        m16 = sum(h16.values()) / len(h16)
        rows.append((label, f"{m1:.4f}", f"{m4:.4f}", f"{m16:.4f}"))
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{R2-09/R2-10 held-out EVAL mean recall@k by horizon (4 held-out instances, equal weight).}",
        r"\label{tab:c2-predictor}",
        r"\small",
        r"\begin{tabular}{@{}lccc@{}}",
        r"\toprule",
        r"Predictor & $h{=}1$ & $h{=}4$ & $h{=}16$ \\",
        r"\midrule",
    ]
    for label, m1, m4, m16 in rows:
        lines.append(f"{label} & {m1} & {m4} & {m16} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def table_c3_retention_replay() -> str:
    bridge = _load("r2_bridge01", "BRIDGE_AGGREGATE_COMPARISON.json")
    r11 = _load("r2_11", "PER-BUDGET-COMPARISON.json")
    boot = _load("r2_11", "BOOTSTRAP-RESULT.json")
    rows = [
        ("R2-BRIDGE-01 (prospective)", "4 GiB (primary)", f"{bridge['primary_result']['absolute_difference']:,.2f}",
         f"{bridge['primary_result']['relative_difference_pct']:.4f}\\%", "---", "BRIDGE\\_POSITIVE"),
        ("R2-11 (confirmatory)", "1.5 GiB (stress)", f"{r11['mean_delta_1_5gib']:,.2f}", "---", "---", "descriptive"),
        ("R2-11 (confirmatory)", "4 GiB (primary)", f"{r11['mean_delta_4gib_primary']:,.2f}", "---",
         f"[{boot['ci_lower_95']:,.2f}, {boot['ci_upper_95']:,.2f}]", "R2\\_11\\_PASS"),
        ("R2-11 (confirmatory)", "8 GiB (null-control)", f"{r11['mean_delta_8gib']:,.2f}", "0.0000\\%", "---", "exact tie"),
    ]
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Replay-level retention results: mean paired delta in miss\_bytes\_per\_decode\_step (PredictedProtect $-$ B\_selected(M)).}",
        r"\label{tab:c3-retention}",
        r"\small",
        r"\begin{tabular}{@{}llrrrl@{}}",
        r"\toprule",
        r"Stage & Budget & $\Delta$ (bytes/step) & Relative & 95\% CI & Status \\",
        r"\midrule",
    ]
    for stage, budget, delta, rel, ci, status in rows:
        lines.append(f"{stage} & {budget} & {delta} & {rel} & {ci} & {status} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def table_c4_runtime_results() -> str:
    primary = _load("r2_12", "R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json")
    sec_1_5 = _load("r2_12", "R2_12_1_5GIB_SECONDARY_FORMAL_ANALYSIS.json")
    sec_8 = _load("r2_12", "R2_12_8GIB_SECONDARY_FORMAL_ANALYSIS.json")

    def row(d, key, budget, status):
        stats = d[key]
        agg = stats["primary_aggregate_statistic"]
        ci = stats["bootstrap_95ci"]
        delta = agg["observed_mean_delta_tps"]
        return (budget, f"{delta:.6f}", f"[{ci['lower']:.6f}, {ci['upper']:.6f}]", status)

    rows = [
        row(sec_1_5, "official_secondary_statistics", "1.5 GiB (stress)", "descriptive only"),
        row(primary, "official_primary_statistics", "4 GiB (primary)", "R2\\_12\\_FAIL"),
        row(sec_8, "official_secondary_statistics", "8 GiB (null-control)", "descriptive only"),
    ]
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{R2-12 real-runtime results: mean paired delta in decode tokens/second (PredictedProtect $-$ B\_selected(M)), own 95\% CI per budget (not pooled).}",
        r"\label{tab:c4-runtime}",
        r"\small",
        r"\begin{tabular}{@{}lrll@{}}",
        r"\toprule",
        r"Budget & $\Delta$ TPS & 95\% CI & Classification \\",
        r"\midrule",
    ]
    for budget, delta, ci, status in rows:
        lines.append(f"{budget} & {delta} & {ci} & {status} \\\\")
    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\vspace{2pt}",
        r"\par\footnotesize\textsuperscript{1}Secondary budgets are descriptive only and cannot override the primary 4~GiB classification under the frozen decision rule.",
        r"\end{table}",
    ]
    return "\n".join(lines) + "\n"
