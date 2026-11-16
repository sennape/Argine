#!/usr/bin/env python3
"""Generate Research2 paper figures from the frozen canonical data snapshot
under paper/research2/data/ (never from hand-typed datapoints).

II-A (existing, preserved):
  Figure 1: Global-LRU vs Oracle-Global-Belady miss rate by budget (R2-03).
  Figure 2: Relative Oracle miss-byte reduction over Global-LRU vs budget.

II-B (added in the II-B paper freeze):
  Figure B1: Layer-wise expert concentration (R2-05).
  Figure B2: Same-layer routing transition structure (R2-06).
  Figure B3: Observed vs shuffled reuse locality (R2-07).
  Figure B4: R2-08 temporal / replicate / cross-workload scale comparison.

Usage: python3 make_figures.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
GIB = 1 << 30

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linewidth": 0.5,
    "figure.figsize": (4.4, 3.2),
    "savefig.bbox": "tight",
})


def _load_json(*parts: str) -> dict:
    return json.loads((DATA.joinpath(*parts)).read_text(encoding="utf-8"))


def _load_csv(*parts: str) -> list[dict]:
    with (DATA.joinpath(*parts)).open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# II-A figures (preserved)
# ---------------------------------------------------------------------------

def _load_r2_03_comparison() -> dict:
    return _load_json("r2_03", "comparison.json")


def figure1(comparison: dict) -> None:
    glru = sorted(comparison["global_lru_miss_rate_vs_budget"], key=lambda p: p["budget_bytes"])
    oracle = sorted(comparison["oracle_miss_rate_vs_budget"], key=lambda p: p["budget_bytes"])
    x = [p["budget_bytes"] / GIB for p in glru]
    y_glru = [p["miss_rate"] for p in glru]
    y_oracle = [p["miss_rate"] for p in oracle]

    fig, ax = plt.subplots()
    ax.plot(x, y_glru, marker="o", linewidth=1.3, markersize=4, label="Global-LRU (causal)")
    ax.plot(x, y_oracle, marker="s", linewidth=1.3, markersize=4,
            label="Belady comparator (clairvoyant, $O_B$)")
    ax.set_xlabel("Expert-memory budget (GiB)")
    ax.set_ylabel("Miss rate")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{v:g}" for v in x])
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, loc="upper right")
    fig.savefig(HERE / "figure1_miss_rate_vs_budget.pdf")
    fig.savefig(HERE / "figure1_miss_rate_vs_budget.png", dpi=200)
    plt.close(fig)


def figure2(comparison: dict) -> None:
    curve = sorted(comparison["primary_curve_relative_miss_byte_reduction_vs_budget"],
                   key=lambda p: p["budget_bytes"])
    x = [p["budget_bytes"] / GIB for p in curve]
    y = [p["relative_miss_byte_reduction"] for p in curve]

    fig, ax = plt.subplots()
    ax.plot(x, y, marker="o", linewidth=1.3, markersize=4, color="#8a1f1f")
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_xlabel("Expert-memory budget (GiB)")
    ax.set_ylabel("Relative Belady comparator miss-byte reduction\n(vs. Global-LRU)")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{v:g}" for v in x])
    ax.set_ylim(-0.05, 1.0)
    for xv, yv in zip(x, y):
        ax.annotate(f"{yv:.2%}", (xv, yv), textcoords="offset points",
                    xytext=(0, 7), ha="center", fontsize=7.5)
    fig.savefig(HERE / "figure2_relative_headroom_vs_budget.pdf")
    fig.savefig(HERE / "figure2_relative_headroom_vs_budget.png", dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------------------
# II-B figures
# ---------------------------------------------------------------------------

def figure_b1_concentration() -> None:
    rows = _load_csv("r2_05", "layer_concentration.csv")
    layers = [int(r["layer_id"]) for r in rows]
    eff_frac = [float(r["effective_expert_fraction"]) for r in rows]
    top8 = None
    conc = _load_json("r2_05", "concentration.json")
    lm = conc.get("layer_metrics") if isinstance(conc, dict) else None
    if isinstance(lm, dict):
        vals = []
        for l in layers:
            m = lm.get(str(l), lm.get(l, {}))
            tk = m.get("top_k_share", {})
            if isinstance(tk, dict):
                vals.append(float(tk.get("8", tk.get(8, 0.0))))
            else:
                vals.append(float(m.get("top_8_share", 0.0)))
        top8 = vals
    elif isinstance(lm, list):
        by = {int(x["layer_id"]): x for x in lm}
        vals = []
        for l in layers:
            m = by.get(l, {})
            tk = m.get("top_k_share", {})
            vals.append(float(tk.get("8", tk.get(8, m.get("top_8_share", 0.0)))) if isinstance(tk, dict) else float(m.get("top_8_share", 0.0)))
        top8 = vals
    if top8 is None:
        fd = _load_json("r2_05", "figure_data.json")
        cfig = fd.get("figure_c_top_k_share_by_layer") if isinstance(fd, dict) else None
        if isinstance(cfig, list) and len(cfig) == len(layers):
            top8 = [float(r.get("top_8_share", r.get("8", 0.0))) for r in cfig]
        elif isinstance(cfig, dict):
            top8 = [float(cfig.get(str(l), {}).get("top_8_share", cfig.get(str(l), {}).get("8", 0.0))) for l in layers]

    fig, ax1 = plt.subplots(figsize=(5.2, 3.2))
    ax1.bar(layers, eff_frac, width=0.8, color="#4c72b0", alpha=0.65, label="Effective expert fraction")
    ax1.set_xlabel("Layer")
    ax1.set_ylabel("Effective expert fraction", color="#4c72b0")
    ax1.set_ylim(0, 1.0)
    ax1.set_xticks([0, 8, 16, 24, 32, 40, 47])
    if top8 and any(t > 0 for t in top8):
        ax2 = ax1.twinx()
        ax2.plot(layers, top8, color="#c44e52", marker=".", linewidth=1.0, markersize=3.0,
                 label="Top-8 traffic share")
        ax2.set_ylabel("Top-8 traffic share", color="#c44e52")
        ax2.set_ylim(0, 0.55)
        ax2.spines["top"].set_visible(False)
        h1, l1 = ax1.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax1.legend(h1 + h2, l1 + l2, frameon=False, loc="upper right", fontsize=7.5)
    else:
        ax1.legend(frameon=False, loc="upper right", fontsize=8)
    fig.savefig(HERE / "figure_b1_r2_05_concentration.pdf")
    fig.savefig(HERE / "figure_b1_r2_05_concentration.png", dpi=200)
    plt.close(fig)


def figure_b2_transitions() -> None:
    summary = _load_json("r2_06", "summary.json")
    h = summary["headline"]
    # Per-layer NMI/lift from figure_data if present, else layer_transition_summary/transition metrics.
    fig_data = _load_json("r2_06", "figure_data.json")
    # Try common shapes.
    layers = list(range(int(h["layer_count"])))
    nmi = lift = None
    if isinstance(fig_data, dict):
        for key in ("nmi_by_layer", "layer_nmi", "nmi"):
            if key in fig_data and isinstance(fig_data[key], list) and len(fig_data[key]) == len(layers):
                nmi = [float(x) for x in fig_data[key]]
        for key in ("self_transition_lift_by_layer", "self_lift_by_layer", "lift"):
            if key in fig_data and isinstance(fig_data[key], list) and len(fig_data[key]) == len(layers):
                lift = [float(x) for x in fig_data[key]]
        # nested per-layer records
        if nmi is None:
            recs = fig_data.get("layers") or fig_data.get("by_layer") or fig_data.get("per_layer")
            if isinstance(recs, list) and recs:
                nmi = [float(r.get("nmi", r.get("normalized_mutual_information", 0))) for r in recs]
                lift = [float(r.get("self_transition_lift", r.get("self_lift", 0))) for r in recs]
                layers = [int(r.get("layer_id", i)) for i, r in enumerate(recs)]
    if nmi is None:
        rows = _load_csv("r2_06", "layer_transition_summary.csv")
        layers = [int(r["layer_id"]) for r in rows]
        # find nmi/lift columns defensively
        def col(options, row):
            for o in options:
                if o in row and row[o] != "":
                    return float(row[o])
            return 0.0
        nmi = [col(["nmi", "normalized_mutual_information", "NMI"], r) for r in rows]
        lift = [col(["self_transition_lift", "self_lift", "median_self_transition_lift"], r) for r in rows]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(5.2, 4.2), sharex=True)
    ax1.bar(layers, nmi, width=0.8, color="#55a868")
    ax1.set_ylabel("NMI")
    ax1.axhline(float(h["median_nmi"]), color="black", linewidth=0.8, linestyle="--",
                label=f"median={h['median_nmi']:.3f}")
    ax1.legend(frameon=False, fontsize=8, loc="upper right")
    ax2.bar(layers, lift, width=0.8, color="#8172b2")
    ax2.set_ylabel("Self-transition lift")
    ax2.set_xlabel("Layer")
    ax2.axhline(float(h["median_self_transition_lift"]), color="black", linewidth=0.8, linestyle="--",
                label=f"median={h['median_self_transition_lift']:.2f}")
    ax2.legend(frameon=False, fontsize=8, loc="upper right")
    ax2.set_xticks([0, 8, 16, 24, 32, 40, 47])
    fig.tight_layout()
    fig.savefig(HERE / "figure_b2_r2_06_transitions.pdf")
    fig.savefig(HERE / "figure_b2_r2_06_transitions.png", dpi=200)
    plt.close(fig)


def figure_b3_reuse_locality() -> None:
    summary = _load_json("r2_07", "summary.json")
    ovn = summary["headline"]["observed_vs_null"]

    # Left panel: reuse mass at short horizons (observed vs null)
    labels = ["Immediate\n(gap=1)", "Reuse\n<= 4", "Reuse\n<= 16"]
    obs = [ovn["immediate_reuse_fraction"]["median_observed"],
           ovn["reuse_within_4_fraction"]["median_observed"],
           ovn["reuse_within_16_fraction"]["median_observed"]]
    nul = [ovn["immediate_reuse_fraction"]["median_null"],
           ovn["reuse_within_4_fraction"]["median_null"],
           ovn["reuse_within_16_fraction"]["median_null"]]

    # Right panel: working-set size by window (observed vs null)
    wins = [8, 32, 128, 256]
    ws_obs = [ovn["working_set_size"][str(w)]["median_observed"] for w in wins]
    ws_nul = [ovn["working_set_size"][str(w)]["median_null"] for w in wins]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.4, 3.2))
    x = list(range(len(labels)))
    w = 0.35
    ax1.bar([i - w/2 for i in x], obs, width=w, label="Observed", color="#4c72b0")
    ax1.bar([i + w/2 for i in x], nul, width=w, label="Shuffled null", color="#dd8452")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, fontsize=8)
    ax1.set_ylabel("Fraction of reuse events")
    ax1.set_ylim(0, 1.0)
    ax1.set_title("Short-horizon reuse", fontsize=9)
    ax1.legend(frameon=False, fontsize=7.5)

    x2 = list(range(len(wins)))
    ax2.bar([i - w/2 for i in x2], ws_obs, width=w, label="Observed", color="#4c72b0")
    ax2.bar([i + w/2 for i in x2], ws_nul, width=w, label="Shuffled null", color="#dd8452")
    ax2.set_xticks(x2)
    ax2.set_xticklabels([str(v) for v in wins])
    ax2.set_xlabel("Window (positions)")
    ax2.set_ylabel("Median working-set size")
    ax2.set_ylim(0, 128)
    ax2.set_title("Working-set growth", fontsize=9)
    ax2.legend(frameon=False, fontsize=7.5)

    fig.tight_layout()
    fig.savefig(HERE / "figure_b3_r2_07_reuse_locality.pdf")
    fig.savefig(HERE / "figure_b3_r2_07_reuse_locality.png", dpi=200)
    plt.close(fig)


def figure_b4_r2_08_scale() -> None:
    summary = _load_json("r2_08", "summary.json")
    h = summary["headline"]
    labels = ["Temporal\ndrift", "Same-workload\nreplicate", "Cross-workload"]
    vals = [float(h["median_temporal_jsd"]),
            float(h["median_same_workload_replicate_jsd"]),
            float(h["median_cross_workload_jsd"])]
    colors = ["#4c72b0", "#55a868", "#c44e52"]
    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    bars = ax.bar(labels, vals, color=colors, width=0.55)
    ax.set_ylabel("Median layer-frequency JSD")
    ax.set_ylim(0, max(vals) * 1.45)
    for b, v in zip(bars, vals):
        txt = f"{v:.3f}" if v > 0 else "0.000"
        ax.annotate(txt, (b.get_x() + b.get_width() / 2, v), textcoords="offset points",
                    xytext=(0, 5), ha="center", fontsize=8.5)
    ax.annotate("deterministic decoding\n(temp=0, top_k=1)",
                xy=(1.0, max(vals) * 0.03),
                xytext=(0.95, max(vals) * 0.62),
                fontsize=7.5, ha="center",
                arrowprops=dict(arrowstyle="->", linewidth=0.7, color="#444444"))
    ratio = float(h["cross_workload_to_temporal_ratio"])
    ax.text(0.98, 0.97, f"cross-workload / temporal = {ratio:.2f}×",
            transform=ax.transAxes, ha="right", va="top", fontsize=8,
            bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="#cccccc"))
    fig.tight_layout()
    fig.savefig(HERE / "figure_b4_r2_08_scale_comparison.pdf")
    fig.savefig(HERE / "figure_b4_r2_08_scale_comparison.png", dpi=200)
    plt.close(fig)


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
    fig, ax = plt.subplots(figsize=(4.9, 3.0))
    colors = ["#2a6f97", "#a9a9a9", "#a9a9a9"]
    bars = ax.bar(names, vals, color=colors, width=0.55)
    for b, v in zip(bars, vals):
        ax.annotate(f"{v:.4f}", (b.get_x() + b.get_width() / 2, v),
                    textcoords="offset points", xytext=(0, 5), ha="center", fontsize=8.5)
    ax.set_ylabel("held-out EVAL mean recall@k (h=1)", fontsize=8.5)
    ax.set_title("R-09/R-10: predictor vs. trivial baselines",
                 fontsize=9.5, pad=8)
    ax.tick_params(axis="x", labelsize=7.5)
    ax.tick_params(axis="y", labelsize=8)
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
    ax.set_ylabel("mean miss bytes per decode step (4 GiB)")
    ax.set_title(f"R-BRIDGE-01: {r['relative_difference_pct']:.4f}% relative difference")
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
    fig, ax = plt.subplots(figsize=(4.9, 3.4))
    bars = ax.bar(budgets, vals, color=["#8fb8d6", "#2a6f97", "#8fb8d6"], width=0.55)
    ax.errorbar([1], [d["mean_delta_4gib_primary"]],
                yerr=[[d["mean_delta_4gib_primary"] - boot["ci_lower_95"]],
                      [boot["ci_upper_95"] - d["mean_delta_4gib_primary"]]],
                fmt="none", ecolor="black", capsize=4, linewidth=1)
    ax.axhline(0, color="#444444", linewidth=0.7)
    ax.set_ylim(min(vals) * 1.32, 0)
    for i, (b, v) in enumerate(zip(bars, vals)):
        ax.annotate(f"{v:,.0f}", (b.get_x() + b.get_width() / 2, v),
                    textcoords="offset points",
                    xytext=(22 if i == 1 else 0, -8), va="top",
                    ha="left" if i == 1 else "center", fontsize=8)
    ax.set_ylabel("mean paired delta, miss bytes per decode step\n(PredictedProtect - B_selected)", fontsize=8)
    ax.set_title("R-11: replay retention effect by budget",
                 loc="left", fontsize=9.5, pad=8)
    ax.tick_params(axis="x", labelsize=8)
    ax.tick_params(axis="y", labelsize=8)
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
    ax.set_ylabel("mean decode tokens per second")
    ax.set_title("R-12: throughput by policy and budget", fontsize=9.5)
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
    ax.set_ylabel("mean paired delta, decode tokens per second\n(PredictedProtect - B_selected)", fontsize=8)
    ax.set_title("R-12: TPS delta by budget and 95% CI",
                 loc="left", fontsize=9.5, pad=8)
    ax.tick_params(axis="x", labelsize=8)
    ax.tick_params(axis="y", labelsize=8)
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
        ("Prediction\n(R-09/R-10)", "+", "#2a6f97"),
        ("Replay Retention\n(R-BRIDGE-01/R-11)", "+", "#2a6f97"),
        ("Real Runtime\n(R-12)", "−", "#b83232"),
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
    ax.set_title("Evidence chain: prediction to replay retention to real runtime", fontsize=9.5)
    fig.tight_layout()
    fig.savefig(HERE / "figure_c6_evidence_chain.pdf")
    fig.savefig(HERE / "figure_c6_evidence_chain.png", dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # II-A
    comparison = _load_r2_03_comparison()
    figure1(comparison)
    figure2(comparison)
    print("wrote figure1_miss_rate_vs_budget.{pdf,png}")
    print("wrote figure2_relative_headroom_vs_budget.{pdf,png}")
    # II-B
    figure_b1_concentration()
    print("wrote figure_b1_r2_05_concentration.{pdf,png}")
    figure_b2_transitions()
    print("wrote figure_b2_r2_06_transitions.{pdf,png}")
    figure_b3_reuse_locality()
    print("wrote figure_b3_r2_07_reuse_locality.{pdf,png}")
    figure_b4_r2_08_scale()
    print("wrote figure_b4_r2_08_scale_comparison.{pdf,png}")
    # II-C
    figure_c1_predictor_comparison()
    print("wrote figure_c1_r2_10_predictor_comparison.{pdf,png}")
    figure_c2_bridge_miss_bytes()
    print("wrote figure_c2_r2_bridge01_miss_bytes.{pdf,png}")
    figure_c3_r2_11_replay_effect()
    print("wrote figure_c3_r2_11_replay_effect.{pdf,png}")
    figure_c4_r2_12_throughput_by_budget()
    print("wrote figure_c4_r2_12_throughput_by_budget.{pdf,png}")
    figure_c5_r2_12_delta_by_budget_ci()
    print("wrote figure_c5_r2_12_delta_by_budget_ci.{pdf,png}")
    figure_c6_evidence_chain()
    print("wrote figure_c6_evidence_chain.{pdf,png}")


if __name__ == "__main__":
    main()
