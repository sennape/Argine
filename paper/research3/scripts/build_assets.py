"""Generate the paper tables (tables/*.tex) and figures (figures/*.pdf) from the frozen evidence. No number is typed by hand."""
from __future__ import annotations
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import frozen

OUT = HERE.parent


def f(x, d=2): return f"{float(x):,.{d}f}".replace(",", "{,}")
def ci(c, d=2): return f"[{f(c['lo'], d)}, {f(c['hi'], d)}]".replace("-", "$-$")
def pct(x, d=3): return f"{100 * float(x):.{d}f}\\%"


def table_runtime(F):
    c = F["closure"]; rows = []
    for b, key, nb in (("1.5 GiB (primary)", "primary_1.5GiB", 18), ("8 GiB (control)", "control_8GiB_descriptive", 6)):
        p = c[key]; a = p["arm_stats"]
        for arm, nm in (("A", "b\\_selected"), ("S", "predicted\\_protect\\_sham"), ("P", "predicted\\_protect")):
            rows.append(f"{b if arm == 'A' else ''} & {arm} ({nm}) & {a[arm]['n_launches']} & {f(a[arm]['ms_per_token']['mean'])} & {f(a[arm]['ms_per_token']['sd'])} & {f(a[arm]['tokens_per_second']['mean'], 4)} \\\\")
        for lab, k in (("$S-A$ (cost)", "S_minus_A"), ("$P-S$ (retention)", "P_minus_S"), ("$P-A$ (net)", "P_minus_A_ms_per_token")):
            q = p[k]; rows.append(f" & {lab} & {q['n_blocks']} blocks & {f(q['mean'])} & {ci(q)} & \\\\")
        rows.append("\\midrule")
    rows = rows[:-1]
    t = ["\\begin{table}[t]", "\\centering", "\\caption{Real-runtime retention evaluation results (decode wall ms/token, 512 tokens; lower is better). Arm rows: per-launch mean, standard deviation and mean tokens/s. Contrast rows: mean over blocks of paired 2-launch arm means, two-sided 95\\% $t$ interval. The 8~GiB budget is a descriptive negative control.}",
         "\\label{tab:runtime}", "\\small", "\\begin{tabular}{llrrrr}", "\\toprule", "Budget & Arm / contrast & $n$ & ms/token & sd or 95\\% CI & tok/s \\\\", "\\midrule", *rows, "\\bottomrule", "\\end{tabular}", "\\end{table}"]
    (OUT / "tables/table2_runtime.tex").write_text("\n".join(t) + "\n", encoding="utf-8")


def table_mech(F):
    rows = []
    for r in F["rows"]:
        nm = r["name"].replace("_", "\\_")
        rows.append(f"{nm} & {float(r['misses_pt']):.3f} & {f(r['bytes_pt'] / 10**6, 3)} & {f(r['misses_avoided'], 0)} & {pct(r['cap_bytes'], 2 if r['cap_bytes'] > 0.01 else 3)} & {f(r['gross_ms'])} \\\\")
    t = ["\\begin{table}[t]", "\\centering",
         "\\caption{Offline mechanism results at 1.5~GiB on the canonical stream (512 decode tokens). Capture is the fraction of the Belady-style byte headroom, Eq.~\\eqref{eq:capture}; gross value is Eq.~\\eqref{eq:value} at $\\kappa_{\\mathrm{low}}$ with zero policy cost. The oracle row is clairvoyant and is not a causal target.}",
         "\\label{tab:mech}", "\\small", "\\begin{tabular}{lrrrrr}", "\\toprule", "Policy & misses/token & miss MB/token & misses avoided & byte capture & gross ms/token \\\\", "\\midrule", *rows, "\\bottomrule", "\\end{tabular}", "\\end{table}"]
    (OUT / "tables/table3_mechanism.tex").write_text("\n".join(t) + "\n", encoding="utf-8")


def figures(F):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8.5, "font.family": "serif", "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
    # Figure 2: headroom capture (log scale)
    sel = [r for r in F["rows"] if not r["name"].startswith("A ")]
    lab = ["PP", "CES", "NUE_LO", "NUE_X", "Belady (oracle)"]
    val = [100 * float(r["cap_bytes"]) for r in sel]
    fig, ax = plt.subplots(figsize=(4.6, 1.9))
    col = ["#8c8c8c", "#8c8c8c", "#4a6fa5", "#4a6fa5", "#c9c9c9"]
    ax.barh(range(len(val)), val, color=col, height=0.6)
    ax.set_xscale("log"); ax.set_xlim(0.1, 300); ax.set_ylim(4.5, -1.2); ax.set_yticks(range(len(val))); ax.set_yticklabels(lab)
    k4 = 100 * 10 / F["kappa_low"] * 1e6 * 512 / F["bb"]
    ax.axvline(k4, color="#b03a2e", lw=1, ls="--"); ax.text(k4 * 1.08, -0.75, f"K4 floor ({k4:.2f}%)", color="#b03a2e", fontsize=7.5, va="center")
    for i, v in enumerate(val): ax.text(v * 1.1, i, f"{v:.3f}%" if v < 1 else (f"{v:.2f}%" if v < 100 else "100%"), va="center", fontsize=7.5)
    ax.set_xlabel("share of Belady-style miss-byte headroom (log scale)")
    fig.tight_layout(); fig.savefig(OUT / "figures/fig2_headroom_capture.pdf"); plt.close(fig)
    # Figure 3: runtime arms and paired contrasts
    c = F["closure"]; fig, axs = plt.subplots(1, 2, figsize=(6.4, 2.2), gridspec_kw={"width_ratios": [1, 1.25]})
    for j, (key, nm) in enumerate((("primary_1.5GiB", "1.5 GiB"), ("control_8GiB_descriptive", "8 GiB"))):
        a = c[key]["arm_stats"]
        axs[0].errorbar([j - 0.2, j, j + 0.2], [a[k]["ms_per_token"]["mean"] for k in "ASP"], yerr=[a[k]["ms_per_token"]["sd"] for k in "ASP"], fmt="none", ecolor="#555", capsize=2, lw=0.8)
        axs[0].scatter([j - 0.2, j, j + 0.2], [a[k]["ms_per_token"]["mean"] for k in "ASP"], c=["#4a6fa5", "#8c8c8c", "#b03a2e"], s=14, zorder=3)
    axs[0].set_xticks([0, 1]); axs[0].set_xticklabels(["1.5 GiB", "8 GiB"]); axs[0].set_ylabel("decode ms/token (mean $\\pm$ sd)"); axs[0].set_title("arms A, S, P (left to right)", fontsize=8)
    labs, y = [], 0
    for key, nm in (("primary_1.5GiB", "1.5 GiB"), ("control_8GiB_descriptive", "8 GiB")):
        for k, l in (("S_minus_A", "S$-$A"), ("P_minus_S", "P$-$S"), ("P_minus_A_ms_per_token", "P$-$A")):
            q = c[key][k]; axs[1].errorbar(q["mean"], y, xerr=[[q["mean"] - q["lo"]], [q["hi"] - q["mean"]]], fmt="o", ms=3.5, color="#333", capsize=2, lw=0.9)
            labs.append(f"{l}  {nm}"); y += 1
    axs[1].axvline(0, color="#999", lw=0.8); axs[1].set_yticks(range(len(labs))); axs[1].set_yticklabels(labs); axs[1].invert_yaxis()
    axs[1].set_xlabel("paired contrast, ms/token (95% CI)"); axs[1].set_title("block-level contrasts", fontsize=8)
    fig.tight_layout(); fig.savefig(OUT / "figures/fig3_runtime.pdf"); plt.close(fig)


if __name__ == "__main__":
    F = frozen.load(); table_runtime(F); table_mech(F); figures(F); print("assets written")
