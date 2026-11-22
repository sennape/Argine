"""Generate the paper tables (tables/*.tex), figure data (figures/figure_data.json) and figures (figures/*.pdf) from the frozen evidence.

No number is typed by hand. `render()` returns the exact text of every generated table and the figure data, so the verifier can
compare them with the committed files without writing anything; `python build_assets.py` writes tables, data and figures."""
from __future__ import annotations
import json, sys
from fractions import Fraction
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import frozen
from frozen import pct, big

OUT = HERE.parent
NAMES = {"NUE_X": "\\textsc{nue-x}", "DECAYED_COUNT": "decayed count", "RICH": "rich policy", "CHEAP": "distilled cheap policy"}
KIND = {"NUE_X": "causal, hand-built", "DECAYED_COUNT": "causal, hand-built", "RICH": "causal, learned (GBRT)", "CHEAP": "causal, learned (CART, depth 3)"}
WL = ("coding", "instruction", "reasoning", "summarization")


def _t(caption, label, cols, head, rows, size="\\small", extra="", place="t"):
    return "\n".join([f"\\begin{{table}}[{place}]", "\\centering", f"\\caption{{{caption}}}", f"\\label{{{label}}}", size, f"\\begin{{tabular}}{{{cols}}}", "\\toprule", head, "\\midrule", *rows, "\\bottomrule", "\\end{tabular}", extra, "\\end{table}"]).replace("\n\n", "\n") + "\n"


def tables(F):
    V, X = F["V"], F["X"]; out = {}
    rows = []
    for a in frozen.ARMS:
        ok = "no" if not (V["capture"][a] >= Fraction(1, 4) and X["capture"][a] >= Fraction(1, 4)) else "yes"
        rows.append(f"{NAMES[a]} & {KIND[a]} & {pct(V['capture'][a], 2, True)} & {pct(X['capture'][a], 2, True)} & {ok} \\\\")
    out["tables/table1_results.tex"] = _t(
        "Headroom capture (Eq.~\\eqref{eq:capture}) of each causal policy against the clairvoyant reference, on the validation cohort (12 traces) and on the prospective held-out evaluation (4 traces). A negative value means the policy incurs more miss bytes than the baseline. The last column states whether the policy reaches the frozen sufficiency level of $25\\%$ on both splits.",
        "tab:results", "llrrc", "Policy & Class & Validation & Held-out & $\\ge 25\\%$ on both \\\\", rows)
    rows = [f"baseline $A$ & {big(X['BA'])} & --- & --- \\\\", f"clairvoyant reference & {big(X['BB'])} & ${'-'}${big(X['headroom'])} & {pct(-Fraction(X['headroom'], X['BA']), 2, True)} \\\\"]
    for a in frozen.ARMS:
        d = X["pooled"][a] - X["BA"]
        rows.append(f"{NAMES[a]} & {big(X['pooled'][a])} & {'$+$' if d > 0 else '$-$'}{big(abs(d))} & {pct(Fraction(d, X['BA']), 2, True)} \\\\")
    out["tables/table2_missbytes.tex"] = _t(
        "Pooled decode miss bytes on the four prospective held-out traces at the 1.5~GiB budget, and the change relative to the baseline $A$ in bytes and in percent. The clairvoyant row is the reference used to define recoverable headroom; it is not a causal policy.",
        "tab:miss", "lrrr", "Policy & miss bytes & change vs.\\ $A$ (bytes) & change vs.\\ $A$ (\\%) \\\\", rows)
    rows = []
    for i, w in enumerate(X["A"]):
        wl = w.replace("-eval-v1", "")
        rows.append(f"{wl} & " + " & ".join(pct(X["per_trace"][a][w], 2, True) for a in frozen.ARMS) + " \\\\")
    out["tables/table3_eval_workloads.tex"] = _t(
        "Headroom capture on each held-out evaluation trace (one trace per workload category). The distilled cheap policy is worse than the baseline on every workload.",
        "tab:evalwl", "lrrrr", "Workload & " + " & ".join(NAMES[a] for a in frozen.ARMS) + " \\\\", rows)
    rows = []
    for w in V["A"]:
        wl = w.replace("r2-11-", "").replace("-v", " v")
        rows.append(f"{wl} & " + " & ".join(pct(V["per_trace"][a][w], 2, True) for a in frozen.ARMS) + " \\\\")
    out["tables/table4_validation_traces.tex"] = _t(
        "Headroom capture on each validation trace (three versions of each workload category). No policy reaches $25\\%$ on any individual trace; the distilled cheap policy is negative on all twelve.",
        "tab:valtr", "lrrrr", "Trace & " + " & ".join(NAMES[a] for a in frozen.ARMS) + " \\\\", rows, place="H")
    return out


def figure_data(F):
    V, X = F["V"], F["X"]
    d = {"threshold": float(Fraction(1, 4)), "arms": list(frozen.ARMS)}
    d["validation"] = {a: float(V["capture"][a]) for a in frozen.ARMS}; d["heldout"] = {a: float(X["capture"][a]) for a in frozen.ARMS}
    d["validation_per_trace"] = {a: {w: float(v) for w, v in V["per_trace"][a].items()} for a in frozen.ARMS}
    d["heldout_per_trace"] = {a: {w: float(v) for w, v in X["per_trace"][a].items()} for a in frozen.ARMS}
    return json.dumps(d, indent=1, sort_keys=True) + "\n"


def render(F):
    out = tables(F); out["figures/figure_data.json"] = figure_data(F); return out


def figures(F):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8.5, "font.family": "serif", "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
    d = json.loads(figure_data(F)); arms = d["arms"]; lab = {"NUE_X": "NUE-X", "DECAYED_COUNT": "decayed count", "RICH": "rich policy", "CHEAP": "distilled cheap"}
    col = {"NUE_X": "#8c8c8c", "DECAYED_COUNT": "#8c8c8c", "RICH": "#4a6fa5", "CHEAP": "#333333"}
    # Figure 2: capture on the two splits (shared axis, linear, sufficiency level visible)
    fig, axs = plt.subplots(1, 2, figsize=(6.4, 1.9), sharex=True)
    for ax, key, ttl in ((axs[0], "validation", "(a) validation (12 traces)"), (axs[1], "heldout", "(b) prospective held-out (4 traces)")):
        v = [100 * d[key][a] for a in arms]
        ax.barh(range(4), v, color=[col[a] for a in arms], height=0.62)
        ax.axvline(0, color="#555", lw=0.8); ax.axvline(100 * d["threshold"], color="#b03a2e", lw=1, ls="--")
        ax.set_yticks(range(4)); ax.set_yticklabels([lab[a] for a in arms] if ax is axs[0] else []); ax.set_ylim(3.6, -0.9)
        for i, x in enumerate(v): ax.text((x + 1.0) if x >= 0 else 1.0, i, f"{x:+.2f}%", va="center", ha="left", fontsize=7.5, zorder=5, bbox=dict(fc="white", ec="none", pad=0.6))
        ax.text(100 * d["threshold"] + 0.8, -0.62, "25% sufficiency", color="#b03a2e", fontsize=7.2, va="center")
        ax.set_xlim(-45, 45); ax.set_title(ttl, fontsize=8); ax.set_xlabel("headroom captured (%)")
    fig.tight_layout(); fig.savefig(OUT / "figures/fig2_capture.pdf"); plt.close(fig)
    # Figure 3: per-trace capture (every validation and held-out trace)
    fig, ax = plt.subplots(figsize=(6.4, 2.1))
    for i, a in enumerate(arms):
        vv = [100 * x for x in d["validation_per_trace"][a].values()]; ee = [100 * x for x in d["heldout_per_trace"][a].values()]
        ax.scatter(vv, [i - 0.12] * len(vv), s=14, color=col[a], alpha=0.75, zorder=3, label="validation trace" if i == 0 else None)
        ax.scatter(ee, [i + 0.14] * len(ee), s=26, marker="D", facecolor="white", edgecolor=col[a], lw=1.1, zorder=3, label="held-out trace" if i == 0 else None)
    ax.axvline(0, color="#555", lw=0.8); ax.axvline(100 * d["threshold"], color="#b03a2e", lw=1, ls="--")
    ax.text(100 * d["threshold"] + 0.6, -0.55, "25%", color="#b03a2e", fontsize=7.5, va="center")
    ax.set_yticks(range(4)); ax.set_yticklabels([lab[a] for a in arms]); ax.set_ylim(3.6, -0.85); ax.set_xlim(-52, 30)
    ax.set_xlabel("headroom captured on a single trace (%)"); ax.legend(loc="upper left", bbox_to_anchor=(0.0, -0.28), ncol=2, fontsize=7.2, frameon=False)
    fig.tight_layout(); fig.savefig(OUT / "figures/fig3_pertrace.pdf"); plt.close(fig)


if __name__ == "__main__":
    F = frozen.load()
    for rel, txt in render(F).items(): (OUT / rel).write_text(txt, encoding="utf-8", newline="\n")
    figures(F); print("assets written")
