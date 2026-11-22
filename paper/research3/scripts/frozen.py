"""Read-only loader for the frozen Research 3 evidence used by the paper. Every value the paper reports comes from here."""
from __future__ import annotations
import json
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
E = REPO / "experiments"


def jl(rel: str):
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def load():
    c = jl("experiments/R3-04-closure/CLOSURE.json")
    r2 = jl("experiments/R3-05-economic-retention/r2-belady-ceiling/RESULTS.json")["budgets"]["1.5"]
    ces = jl("experiments/R3-05-economic-retention/r3-ces-pp-curve/CES_RESULTS.json")
    ev = jl("experiments/R3-05-economic-retention/r4-nue-candidate-gate/EVAL_RESULTS.json")
    hc = jl("experiments/R3-05-economic-retention/r4-nue-candidate-gate/HEADROOM_CAPTURE.json")
    k4 = jl("experiments/R3-05-economic-retention/r4-nue-candidate-gate/K4_DECISION.json")
    sel = jl("experiments/R3-05-economic-retention/r4-nue-candidate-gate/CANDIDATE_SELECTION.json")
    k2 = jl("experiments/R3-05-economic-retention/r3-ces-pp-curve/K2_DECISION.json")
    k3 = jl("experiments/R3-05-economic-retention/r3-ces-pp-curve/K3_DECISION.json")
    mb = jl("experiments/R3-03B-microbench-evidence/MICROBENCH_RESULT.json")
    b3 = jl("experiments/R3-03B-final-evidence/FINAL_RESULT.json")
    a3 = jl("experiments/R3-03A-runtime-semantic-conformance/CONFORMANCE_RESULT.json")
    r1 = jl("experiments/R3-05-economic-retention/r1-fidelity/FIDELITY_RESULTS.json")
    import importlib.util
    spec = importlib.util.spec_from_file_location("_p0", E / "R3-05-economic-retention/phase0.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)   # type: ignore
    env = m.envelope()
    A = r2["A"]["decode"]; bm = r2["mechanism"]["misses_avoided_decode"]; bb = r2["mechanism"]["miss_bytes_avoided_decode"]
    kl = env["derived"]["kappa_ms_per_MB_low"]
    def mech(name, d):
        ba = A["miss_bytes"] - d["miss_bytes"]; ma = A["misses"] - d["misses"]
        return {"name": name, "misses": d["misses"], "miss_bytes": d["miss_bytes"], "misses_pt": Fraction(d["misses"], 512), "bytes_pt": Fraction(d["miss_bytes"], 512),
                "misses_avoided": ma, "bytes_avoided": ba, "cap_bytes": Fraction(ba, bb), "cap_misses": Fraction(ma, bm), "gross_ms": float(Fraction(ba, 512)) / 1e6 * kl}
    rows = [mech("A (b_selected)", A), mech("PP (predicted_protect)", ces["PP_full"]["decode"]), mech("CES", ces["CES"]["decode"]),
            mech("NUE_LO", ev["raw"]["NUE_LO"]["decode"]), mech("NUE_X (LEX)", ev["raw"]["NUE_X"]["decode"])]
    bel = {"misses": A["misses"] - bm, "miss_bytes": A["miss_bytes"] - bb}
    rows.append(mech("Belady-style oracle", bel))
    return {"closure": c, "r2": r2, "rows": rows, "bm": bm, "bb": bb, "kappa": env["derived"]["kappa_ms_per_MB_point"], "kappa_low": kl, "env": env,
            "k2": k2, "k3": k3, "k4": k4, "sel": sel, "hc": hc, "mb": mb, "b3": b3, "a3": a3, "r1": r1}
