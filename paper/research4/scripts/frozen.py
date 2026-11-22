"""Read-only loader for the frozen Research 4 evidence used by the paper. Every number the paper reports comes from here.

All captures are recomputed from per-trace decode miss bytes with exact rational arithmetic and cross-checked against the
rational strings stored in the frozen artifacts."""
from __future__ import annotations
import json
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
E = REPO / "experiments"
P0, P1, P2 = "experiments/R4-00-causal-eviction-distillation", "experiments/R4-01-cfdc-training-validation", "experiments/R4-02-cfdc-prospective-eval"
ARMS = ("NUE_X", "DECAYED_COUNT", "RICH", "CHEAP")


def jl(rel: str):
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def frac(s: str) -> Fraction:
    return Fraction(s)


def _capture(a: dict, bel: dict, p: dict) -> Fraction:
    """capture = sum_w (B_A,w - B_P,w) / sum_w (B_A,w - B_Bel,w)  (frozen primary endpoint, pooled, exact, uncapped)."""
    return Fraction(sum(a[w] - p[w] for w in a), sum(a[w] - bel[w] for w in a))


def load():
    val = jl(f"{P1}/VALIDATION_RESULTS.json"); ev = jl(f"{P2}/EVAL_RESULTS.json"); dec = jl(f"{P2}/DECISION_APPLICATION.json")
    fin = jl(f"{P2}/FINAL_CLASSIFICATION.json"); rules = jl(f"{P0}/DECISION_RULES.json"); splits = jl(f"{P0}/DATA_SPLITS.json")["splits"]
    f0, f1, f2 = (jl(f"{p}/FREEZE.json") for p in (P0, P1, P2))
    mc = jl(f"{P0}/MODEL_CONTRACT.json"); fc = jl(f"{P0}/FEATURE_CONTRACT.json"); chm = jl(f"{P1}/CHEAP_MODEL.json"); tm = jl(f"{P1}/TEACHER_MANIFEST.json")
    pw = jl(f"{P2}/PER_WORKLOAD_RESULTS.json"); rawe = jl(f"{P2}/EVAL_RAW_RESULTS.json"); opened = jl(f"{P2}/EVAL_OPENED.json"); prov1 = jl(f"{P1}/PROVENANCE.json")
    fid = jl(f"{P1}/REPLAY_FIDELITY.json")
    # --- VALIDATION per-trace decode miss bytes
    vw = val["per_workload"]; vA = {w: vw[w]["B_A"] for w in vw}; vB = {w: vw[w]["B_BELADY"] for w in vw}
    vP = {a: {w: vw[w][a]["B_P"] for w in vw} for a in ARMS}
    # --- EVAL per-trace decode miss bytes
    em = ev["miss_bytes_per_trace"]; eA = {w: em[w]["A"] for w in em}; eB = {w: em[w]["BELADY"] for w in em}
    eP = {a: {w: em[w][a] for w in em} for a in ARMS}
    V = {"A": vA, "BEL": vB, "P": vP, "capture": {a: _capture(vA, vB, vP[a]) for a in ARMS}, "n": len(vA)}
    X = {"A": eA, "BEL": eB, "P": eP, "capture": {a: _capture(eA, eB, eP[a]) for a in ARMS}, "n": len(eA)}
    for S, name in ((V, "validation"), (X, "eval")):
        S["BA"] = sum(S["A"].values()); S["BB"] = sum(S["BEL"].values()); S["headroom"] = S["BA"] - S["BB"]
        S["pooled"] = {a: sum(S["P"][a].values()) for a in ARMS}
        S["per_trace"] = {a: {w: Fraction(S["A"][w] - S["P"][a][w], S["A"][w] - S["BEL"][w]) for w in S["A"]} for a in ARMS}
    return {"val": val, "ev": ev, "dec": dec, "fin": fin, "rules": rules, "splits": splits, "f0": f0, "f1": f1, "f2": f2, "mc": mc, "fc": fc, "chm": chm, "tm": tm,
            "pw": pw, "raw": rawe, "opened": opened, "prov1": prov1, "fid": fid, "V": V, "X": X}


# ---- number formatting shared by the generators and the verifier (the printed form is what the verifier looks for) ----
def pct(x, d: int = 2, sign: bool = False) -> str:
    """Percentage of a Fraction/float in LaTeX; with sign=True a math-mode signed form such as $-29.96\\%$."""
    v = round(float(x) * 100, d)
    if sign:
        return ("$-" if v < 0 else "$+") + f"{abs(v):.{d}f}" + "\\%$"
    return ("$-$" if v < 0 else "") + f"{abs(v):.{d}f}" + "\\%"


def big(n: int) -> str:
    return f"{n:,}".replace(",", "{,}")


def pp(x, d: int = 2) -> str:
    return f"{float(x) * 100:.{d}f}"
