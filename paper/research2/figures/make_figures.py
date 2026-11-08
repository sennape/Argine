#!/usr/bin/env python3
"""Generate the II-A paper figures from the frozen canonical data snapshot
under paper/research2/data/ (never from hand-typed datapoints).

Figure 1: Global-LRU vs Oracle-Global-Belady miss rate by expert-memory
          budget (R2-03).
Figure 2: Relative Oracle-Global-Belady miss-byte reduction over
          Global-LRU vs budget (R2-03/R2-04's primary information-
          headroom curve).

Usage: python3 make_figures.py
Outputs: figure1_miss_rate_vs_budget.{pdf,png}
         figure2_relative_headroom_vs_budget.{pdf,png}
"""

from __future__ import annotations

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


def _load_r2_03_comparison() -> dict:
    return json.loads((DATA / "r2_03" / "comparison.json").read_text(encoding="utf-8"))


def figure1(comparison: dict) -> None:
    glru = sorted(comparison["global_lru_miss_rate_vs_budget"], key=lambda p: p["budget_bytes"])
    oracle = sorted(comparison["oracle_miss_rate_vs_budget"], key=lambda p: p["budget_bytes"])
    x = [p["budget_bytes"] / GIB for p in glru]
    y_glru = [p["miss_rate"] for p in glru]
    y_oracle = [p["miss_rate"] for p in oracle]

    fig, ax = plt.subplots()
    ax.plot(x, y_glru, marker="o", linewidth=1.3, markersize=4, label="Global-LRU (causal)")
    ax.plot(x, y_oracle, marker="s", linewidth=1.3, markersize=4,
           label="Oracle-Global-Belady (non-causal)")
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
    ax.set_ylabel("Relative Oracle miss-byte reduction\n(information headroom, vs. Global-LRU)")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{v:g}" for v in x])
    ax.set_ylim(-0.05, 1.0)
    for xv, yv in zip(x, y):
        ax.annotate(f"{yv:.2%}", (xv, yv), textcoords="offset points",
                   xytext=(0, 7), ha="center", fontsize=7.5)
    fig.savefig(HERE / "figure2_relative_headroom_vs_budget.pdf")
    fig.savefig(HERE / "figure2_relative_headroom_vs_budget.png", dpi=200)
    plt.close(fig)


def main() -> None:
    comparison = _load_r2_03_comparison()
    figure1(comparison)
    figure2(comparison)
    print("wrote figure1_miss_rate_vs_budget.{pdf,png}")
    print("wrote figure2_relative_headroom_vs_budget.{pdf,png}")


if __name__ == "__main__":
    main()
