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
    ax.set_ylim(0, max(vals) * 1.28)
    for b, v in zip(bars, vals):
        txt = f"{v:.3f}" if v > 0 else "0.000"
        ax.annotate(txt, (b.get_x() + b.get_width() / 2, v), textcoords="offset points",
                    xytext=(0, 5), ha="center", fontsize=8.5)
    ax.annotate("deterministic decoding\n(temp=0, top_k=1)",
                xy=(1, vals[1]), xytext=(1.35, max(vals) * 0.55),
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


if __name__ == "__main__":
    main()
