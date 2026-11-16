

# ---------------------------------------------------------------------------
# II-C figures (added for the Research 2 final closure / Paper II freeze)
# ---------------------------------------------------------------------------

def _load_r2_10_eval() -> dict:
    return _load_json("r2_10", "PHASE3D-EVAL-RESULTS.json")


def figure_c1_predictor_comparison() -> None:
    """Figure C1: held-out EVAL h=1 mean recall@k, selected predictor vs
    trivial baselines (R2-09/R2-10)."""
    d = _load_r2_10_eval()
    cells = d["per_cell_recall_at_k"]
    labels = {
        "decayed_transition/decay=0.99": "decayed_transition\n(selected)",
        "last_observation/default": "last_observation",
        "global_frequency/default": "global_frequency",
    }
    names, vals = [], []
    for key, label in labels.items():
        h1 = cells[key]["h1"]
        names.append(label)
        vals.append(sum(h1.values()) / len(h1))
    fig, ax = plt.subplots()
    colors = ["#2a6f97", "#a9a9a9", "#a9a9a9"]
    bars = ax.bar(names, vals, color=colors, width=0.55)
    for b, v in zip(bars, vals):
        ax.annotate(f"{v:.4f}", (b.get_x() + b.get_width() / 2, v),
                    textcoords="offset points", xytext=(0, 5), ha="center", fontsize=8.5)
    ax.set_ylabel("held-out EVAL mean recall@k (h=1)")
    ax.set_title("R2-09/R2-10: predictor vs. trivial baselines")
    fig.tight_layout()
    fig.savefig(HERE / "figure_c1_r2_10_predictor_comparison.pdf")
    fig.savefig(HERE / "figure_c1_r2_10_predictor_comparison.png", dpi=200)
    plt.close(fig)


def figure_c2_bridge_miss_bytes() -> None:
    """Figure C2: R2-BRIDGE-01 mean miss_bytes_per_decode_step, B_selected
    vs PredictedProtect, primary 4 GiB budget."""
    d = _load_json("r2_bridge01", "BRIDGE_AGGREGATE_COMPARISON.json")
    r = d["primary_result"]
    names = ["B_selected(M)", "PredictedProtect"]
    vals = [r["mean_b_selected_miss_bytes_per_decode_step"],
            r["mean_predicted_protect_miss_bytes_per_decode_step"]]
    fig, ax = plt.subplots()
    bars = ax.bar(names, vals, color=["#a9a9a9", "#2a6f97"], width=0.5)
    for b, v in zip(bars, vals):
        ax.annotate(f"{v:,.0f}", (b.get_x() + b.get_width() / 2, v),
                    textcoords="offset points", xytext=(0, 5), ha="center", fontsize=8)
    ax.set_ylabel("mean miss_bytes_per_decode_step (4 GiB)")
    ax.set_title(f"R2-BRIDGE-01: {r['relative_difference_pct']:.4f}% relative difference")
    fig.tight_layout()
    fig.savefig(HERE / "figure_c2_r2_bridge01_miss_bytes.pdf")
    fig.savefig(HERE / "figure_c2_r2_bridge01_miss_bytes.png", dpi=200)
    plt.close(fig)


def figure_c3_r2_11_replay_effect() -> None:
    """Figure C3: R2-11 mean paired delta in miss_bytes_per_decode_step by
    budget, with the frozen primary-budget 95% CI."""
    d = _load_json("r2_11", "PER-BUDGET-COMPARISON.json")
    boot = _load_json("r2_11", "BOOTSTRAP-RESULT.json")
    budgets = ["1.5 GiB\n(stress)", "4 GiB\n(primary)", "8 GiB\n(null-control)"]
    vals = [d["mean_delta_1_5gib"], d["mean_delta_4gib_primary"], d["mean_delta_8gib"]]
    fig, ax = plt.subplots()
    bars = ax.bar(budgets, vals, color=["#8fb8d6", "#2a6f97", "#8fb8d6"], width=0.55)
    ax.errorbar([1], [d["mean_delta_4gib_primary"]],
                yerr=[[d["mean_delta_4gib_primary"] - boot["ci_lower_95"]],
                      [boot["ci_upper_95"] - d["mean_delta_4gib_primary"]]],
                fmt="none", ecolor="black", capsize=4, linewidth=1)
    ax.axhline(0, color="#444444", linewidth=0.7)
    for b, v in zip(bars, vals):
        ax.annotate(f"{v:,.0f}", (b.get_x() + b.get_width() / 2, v),
                    textcoords="offset points", xytext=(0, 6 if v >= 0 else -14),
                    ha="center", fontsize=8)
    ax.set_ylabel("mean paired delta, miss_bytes/decode-step\n(PredictedProtect - B_selected)")
    ax.set_title("R2-11: replay-level retention effect by budget (R2_11_PASS)")
    fig.tight_layout()
    fig.savefig(HERE / "figure_c3_r2_11_replay_effect.pdf")
    fig.savefig(HERE / "figure_c3_r2_11_replay_effect.png", dpi=200)
    plt.close(fig)


def _load_r2_12(label: str) -> dict:
    return _load_json("r2_12", f"R2_12_{label}_FORMAL_ANALYSIS.json" if label == "4GIB_PRIMARY"
                       else f"R2_12_{label}_SECONDARY_FORMAL_ANALYSIS.json")


def figure_c4_r2_12_throughput_by_budget() -> None:
    """Figure C4: R2-12 mean decode TPS by policy and budget."""
    primary = _load_json("r2_12", "R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json")
    sec_1_5 = _load_json("r2_12", "R2_12_1_5GIB_SECONDARY_FORMAL_ANALYSIS.json")
    sec_8 = _load_json("r2_12", "R2_12_8GIB_SECONDARY_FORMAL_ANALYSIS.json")

    def centers(d):
        c = d["official_primary_statistics"]["descriptive_central_estimates"] if "official_primary_statistics" in d \
            else d["official_secondary_statistics"]["descriptive_central_estimates"]
        return c["predicted_protect_mean_tps"], c["b_selected_mean_tps"]

    budgets = ["1.5 GiB", "4 GiB\n(primary)", "8 GiB"]
    pp_vals, bs_vals = [], []
    for d in (sec_1_5, primary, sec_8):
        pp, bs = centers(d)
        pp_vals.append(pp)
        bs_vals.append(bs)

    x = range(len(budgets))
    width = 0.32
    fig, ax = plt.subplots()
    ax.bar([i - width / 2 for i in x], bs_vals, width=width, label="B_selected(M)", color="#a9a9a9")
    ax.bar([i + width / 2 for i in x], pp_vals, width=width, label="PredictedProtect", color="#2a6f97")
    ax.set_xticks(list(x))
    ax.set_xticklabels(budgets)
    ax.set_ylabel("mean decode tokens/second")
    ax.set_title("R2-12: real-runtime throughput by policy and budget")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(HERE / "figure_c4_r2_12_throughput_by_budget.pdf")
    fig.savefig(HERE / "figure_c4_r2_12_throughput_by_budget.png", dpi=200)
    plt.close(fig)


def figure_c5_r2_12_delta_by_budget_ci() -> None:
    """Figure C5: R2-12 PredictedProtect - B_selected TPS delta by budget,
    with each budget's own frozen 95% CI (not pooled)."""
    primary = _load_json("r2_12", "R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json")
    sec_1_5 = _load_json("r2_12", "R2_12_1_5GIB_SECONDARY_FORMAL_ANALYSIS.json")
    sec_8 = _load_json("r2_12", "R2_12_8GIB_SECONDARY_FORMAL_ANALYSIS.json")

    def delta_ci(d, primary_key):
        stats = d[primary_key]
        agg = stats["primary_aggregate_statistic"]
        ci = stats["bootstrap_95ci"]
        return agg["observed_mean_delta_tps"], ci["lower"], ci["upper"]

    rows = [
        ("1.5 GiB\n(stress)", delta_ci(sec_1_5, "official_secondary_statistics")),
        ("4 GiB\n(primary)", delta_ci(primary, "official_primary_statistics")),
        ("8 GiB\n(null-control)", delta_ci(sec_8, "official_secondary_statistics")),
    ]
    budgets = [r[0] for r in rows]
    deltas = [r[1][0] for r in rows]
    lo = [r[1][0] - r[1][1] for r in rows]
    hi = [r[1][2] - r[1][0] for r in rows]

    fig, ax = plt.subplots()
    colors = ["#8fb8d6", "#b83232", "#8fb8d6"]
    ax.bar(budgets, deltas, yerr=[lo, hi], color=colors, capsize=4, width=0.55)
    ax.axhline(0, color="#444444", linewidth=0.7)
    ax.set_ylabel("mean paired delta, decode TPS\n(PredictedProtect - B_selected)")
    ax.set_title("R2-12: TPS delta by budget, own 95% CI each (not pooled)")
    fig.tight_layout()
    fig.savefig(HERE / "figure_c5_r2_12_delta_by_budget_ci.pdf")
    fig.savefig(HERE / "figure_c5_r2_12_delta_by_budget_ci.png", dpi=200)
    plt.close(fig)


def figure_c6_evidence_chain() -> None:
    """Figure C6: schematic evidence chain, prediction -> replay retention
    -> real runtime, with each stage's sign."""
    fig, ax = plt.subplots(figsize=(5.2, 2.6))
    ax.axis("off")
    stages = [
        ("Prediction\n(R2-09/R2-10)", "+", "#2a6f97"),
        ("Replay Retention\n(R2-BRIDGE-01/R2-11)", "+", "#2a6f97"),
        ("Real Runtime\n(R2-12)", "−", "#b83232"),
    ]
    n = len(stages)
    xs = [0.12 + i * 0.38 for i in range(n)]
    for (label, sign, color), x in zip(stages, xs):
        ax.add_patch(plt.Rectangle((x - 0.14, 0.35), 0.28, 0.3, facecolor=color,
                                    alpha=0.15, edgecolor=color, linewidth=1.3))
        ax.text(x, 0.5, label, ha="center", va="center", fontsize=8.5)
        ax.text(x, 0.74, sign, ha="center", va="center", fontsize=15,
                color=color, fontweight="bold")
    for x0, x1 in zip(xs[:-1], xs[1:]):
        ax.annotate("", xy=(x1 - 0.15, 0.5), xytext=(x0 + 0.15, 0.5),
                    arrowprops=dict(arrowstyle="->", linewidth=1.2, color="#444444"))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_title("Research II-C evidence chain (sign of each stage's finding)", fontsize=9.5)
    fig.tight_layout()
    fig.savefig(HERE / "figure_c6_evidence_chain.pdf")
    fig.savefig(HERE / "figure_c6_evidence_chain.png", dpi=200)
    plt.close(fig)
