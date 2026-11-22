"""R3-05 rung R3: current-event shield control (CES, K2) + PP invocation-fraction curve (K3). Offline; no candidate (NUE_*) is evaluated.

  python r3.py run <A|CES|PP:N> <out.json>   replay the frozen canonical stream once at 1.5 GiB
  python r3.py finish <dir>                  assemble results and the K2/K3 decisions from the nine run files
  python r3.py build <dir>                   write the additive evidence files + FREEZE.json
  python r3.py verify [--replay]             offline re-verification (inputs, hashes, recomputation from integer counters; --replay re-runs CES and PP N=1)
"""
from __future__ import annotations

import argparse, hashlib, importlib.util, json, sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
P0 = HERE.parent
R1D, R2D = P0 / "r1-fidelity", P0 / "r2-belady-ceiling"
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tools"))

PHASE0_FREEZE_SHA = "415f78a850a2917a2457797106589433f944c07a863f80b14cef88fde6232ae3"
R1_FREEZE_SHA = "8d025fdfb3d2a48c54e4d905f3a0e3aad2dd70ed9fdabf602b21ec2b600c3382"
R2_FREEZE_SHA = "8b22e41505e3c14b78b6c924b703f1da4b26643123a9240f7bd9ad7d232afa44"
STREAM_SHA = "24fcc3e31e800826acb353c627326d4665af7b05c88a3138b18b79c5c435e776"
N_EVENTS, N_DECODE, FIRST_DECODE, N_LAYERS = 24672, 512, 96, 48
BUDGET = 1536 * 2**20
N_GRID = [1, 2, 4, 10, 20, 50, 100]
K2_FRACTION = Fraction(4, 5)        # frozen Phase 0 k2_ces_fraction 0.8: CES bytes avoided >= 0.8 x PP bytes avoided
K3_FRACTION = Fraction(1, 5)        # frozen Phase 0 k3_amortization_max_retained_at_f010 0.2: retained <= 0.2 at f = 0.10
K3_N = 10                           # f = 1/N = 0.10 exactly; fixed before any number was computed
EFFECT_METRIC = "decode miss bytes avoided vs A (the Phase 0 K2/K3 effect metric); misses reported alongside"
FILES = ["README.md", "SOURCE_IDENTITY.json", "CES_DEFINITION.md", "CES_RESULTS.json", "PP_CURVE.json", "K2_DECISION.json", "K3_DECISION.json", "DECISION_TABLE.json", "CLAIM_BOUNDARIES.md", "r3.py", "tests/test_r3.py"]


class R3Error(Exception):
    pass


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True) + "\n"
def freeze_sha(o: Dict[str, Any]) -> str: return sha_bytes(json.dumps({k: v for k, v in o.items() if k != "freeze_sha256"}, sort_keys=True, separators=(",", ":")).encode())
def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)   # type: ignore
    return m


# ------------------------------------------------------------------ inputs
def verify_inputs() -> Dict[str, Any]:
    r2 = _load(R2D / "r2.py", "_r3_05_r2")
    pr = r2.verify(replay_too=False)                                   # covers Phase 0, R1 and the R2 freeze/recomputation
    if pr: raise R3Error(f"input verification failed: {pr[:3]}")
    fz = json.loads((R2D / "FREEZE.json").read_text())
    if fz["freeze_sha256"] != R2_FREEZE_SHA or fz["classification"] != "R3_05_K1_HEADROOM_PASS": raise R3Error("R2 freeze/classification differs")
    k1 = json.loads((R2D / "K1_DECISION.json").read_text())
    if k1["classification"] != "R3_05_K1_HEADROOM_PASS": raise R3Error("K1 is not HEADROOM_PASS")
    return {"phase0_freeze_sha256": PHASE0_FREEZE_SHA, "r1_freeze_sha256": R1_FREEZE_SHA, "r2_freeze_sha256": R2_FREEZE_SHA, "r1_classification": "R3_05_R1_FIDELITY_PASS", "r2_classification": "R3_05_K1_HEADROOM_PASS",
            "canonical_stream_sha256": STREAM_SHA, "events": N_EVENTS, "passes": 514, "token_digest": "ac28efbe13c80f363f508a6f19d4b1d3af189eb26d6e9c81309376033075cdfa"}


def load_events(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    data = (path or (R1D / "CANONICAL_STREAM.jsonl")).read_bytes()
    if sha_bytes(data) != STREAM_SHA: raise R3Error("wrong stream: sha256 differs from the frozen R1 canonical stream")
    ev = [json.loads(l) for l in data.splitlines()]
    if len(ev) != N_EVENTS: raise R3Error("event count")
    return ev


# ------------------------------------------------------------------ policies
def _ref():
    from argine.research2.r2_09a.policies import AgedFrequency, stable_obj_key   # frozen reference
    from argine.research2.r2_bridge01.retention_policies import PredictedProtectPolicy
    return AgedFrequency, stable_obj_key, PredictedProtectPolicy


def make_ces():
    AgedFrequency, stable_obj_key, _ = _ref()

    class CurrentEventShield(AgedFrequency):
        """b_selected + 'never evict a member of the event being served' (Phase 0 CES). No predictor, no history, no future knowledge."""
        shield_changed_victims = 0
        protected_fallbacks = 0

        def start(self, ctx):
            super().start(ctx); self._cur = frozenset(); self.shield_changed_victims = 0; self.protected_fallbacks = 0

        def access(self, step, layer, experts):
            self._cur = frozenset((int(layer), int(e)) for e in experts)        # exactly the members of THIS event
            try:
                return super().access(step, layer, experts)
            finally:
                self._cur = frozenset()

        def _choose_victim(self):
            if not self.resident:
                return None
            key = lambda o: (self.scores.get(o, 0.0), self.last_ref.get(o, -1), stable_obj_key(o))
            cands = [o for o in self.resident if o not in self._cur]
            if not cands:
                self.protected_fallbacks += 1; return min(self.resident, key=key)
            v = min(cands, key=key)
            if min(self.resident, key=key) != v: self.shield_changed_victims += 1
            return v

    return CurrentEventShield(0.999, 1)


def make_pp_n(n: int):
    """Frozen PredictedProtectPolicy with the prediction refreshed every n same-layer events (Phase 0 PP_CURVE). History is ALWAYS recorded;
    predict() (a pure function of the history) is called only at positions i with i % n == 0; between refreshes the last protected set is held."""
    _, _, PP = _ref()
    from argine.research2.r2_10.predictors import CausalContext

    class PPRefreshN(PP):
        refresh_count = 0

        def start(self, ctx):
            super().start(ctx); self.refresh_count = 0; self.n_refresh = n; self.refresh_log = []

        def _refresh_prediction_after_observing(self, layer, token_index, experts):
            hist = self._history.setdefault(layer, [])
            hist.append((token_index, tuple(sorted(experts))))
            i = len(hist) - 1
            if i % self.n_refresh != 0:
                return                                                       # hold the last valid protected set
            ctx = CausalContext(position=i, token_index=token_index, history=tuple(hist), k=self.ctx.top_k)
            self._predicted_next[layer] = frozenset(self._predictor_for(layer).predict(ctx, k=self.ctx.top_k))
            self.refresh_count += 1

    return PPRefreshN(0.999, 1)


def make_a():
    import r2_12_real_trace_conformance as rtc
    return rtc._build_policy("b_selected", {"decay_factor": 0.999, "aging_interval_steps": 1, "predictor_decay": 0, "top_k": 0, "n_experts": 0})


def make(arm: str):
    if arm == "A": return make_a()
    if arm == "CES": return make_ces()
    if arm.startswith("PP:"): return make_pp_n(int(arm[3:]))
    raise R3Error(f"unknown arm {arm}")


# ------------------------------------------------------------------ replay (integer accounting; same definitions as R1/R2 plus protected counters)
def replay(events: Sequence[Dict[str, Any]], pol, budget: int = BUDGET, top_k: int = 8, n_experts: int = 128) -> Dict[str, Any]:
    from argine.research.policies import Context
    sizes = {e["layer"]: e["bytes"] for e in events}
    pol.start(Context(layers=sorted(sizes), n_experts=n_experts, size=sizes, budget=budget, top_k=top_k))
    z = lambda: {"misses": 0, "hits": 0, "miss_bytes": 0, "evictions": 0}
    allp, dec = z(), z(); prot_ev = prot_en = shield_ev = shield_en = 0; peak = viol = 0
    is_pp = hasattr(pol, "_predicted_next"); is_ces = hasattr(pol, "shield_changed_victims")
    for e in events:
        decode = e["i"] >= FIRST_DECODE
        if decode:
            if is_pp:
                p = pol._predicted_next.get(e["layer"], frozenset())
                if p: prot_ev += 1; prot_en += len(p)
            if is_ces:
                u = len(set(e["req"])); shield_ev += 1; shield_en += u
        ev0 = pol.evictions
        missed = pol.access(e["i"], e["layer"], e["req"])
        u = len(set(e["req"])); m = len(missed); evs = pol.evictions - ev0
        peak = max(peak, pol.resident_bytes); viol += pol.resident_bytes > budget
        for d, on in ((allp, True), (dec, decode)):
            if on: d["misses"] += m; d["hits"] += u - m; d["miss_bytes"] += m * e["bytes"]; d["evictions"] += evs
    out = {"all_pass": allp, "decode": dec, "resident_peak_bytes": peak, "budget_bytes": budget, "budget_violations": viol, "overflow_events": pol.overflow_events, "overflow_misses": pol.overflow_misses,
           "events": len(events), "decode_protected_events": prot_ev, "decode_protected_entries": prot_en}
    if is_ces:
        out.update({"decode_shield_events": shield_ev, "decode_shield_entries": shield_en, "shield_changed_victims_all_pass": pol.shield_changed_victims, "shield_total_fallbacks": pol.protected_fallbacks})
    if hasattr(pol, "refresh_count"):
        out["predictor_refresh_count"] = pol.refresh_count; out["refresh_n"] = pol.n_refresh
    return out


def refresh_count_expected(n: int, n_layers: int = N_LAYERS, passes: int = 514) -> int:
    return n_layers * ((passes - 1) // n + 1)          # positions 0, n, 2n, ... <= passes-1 per layer


# ------------------------------------------------------------------ decisions (exact fractions)
def k2_decide(ces_bytes_avoided: int, pp_bytes_avoided: int) -> Dict[str, Any]:
    if pp_bytes_avoided <= 0: return {"classification": "R3_05_K2_NOT_DEFINED_PP_BENEFIT_NONPOSITIVE", "ratio": None}
    ratio = Fraction(ces_bytes_avoided, pp_bytes_avoided)
    return {"classification": "R3_05_K2_RETIRE_CURRENT_PP" if ratio >= K2_FRACTION else "R3_05_K2_PP_HAS_ADDITIONAL_EFFECT", "ratio": float(ratio), "ratio_exact": f"{ces_bytes_avoided}/{pp_bytes_avoided}"}


def k3_decide(retained_bytes_avoided_n10: int, pp_bytes_avoided_n1: int) -> Dict[str, Any]:
    if pp_bytes_avoided_n1 <= 0: return {"classification": "R3_05_K3_NOT_DEFINED_PP_BENEFIT_NONPOSITIVE", "retained": None}
    r = Fraction(retained_bytes_avoided_n10, pp_bytes_avoided_n1)
    return {"classification": "R3_05_K3_FREQUENCY_FAMILY_DROP" if r <= K3_FRACTION else "R3_05_K3_FREQUENCY_FAMILY_SURVIVES", "retained": float(r), "retained_exact": f"{retained_bytes_avoided_n10}/{pp_bytes_avoided_n1}"}


def benefit(a: Dict[str, Any], x: Dict[str, Any]) -> Dict[str, int]:
    return {"misses_avoided": a["decode"]["misses"] - x["decode"]["misses"], "miss_bytes_avoided": a["decode"]["miss_bytes"] - x["decode"]["miss_bytes"]}


def per_token(num: int) -> float: return float(Fraction(num, N_DECODE))


def assemble(d: Path) -> Dict[str, Any]:
    runs = {"A": json.loads((d / "A.json").read_text()), "CES": json.loads((d / "CES.json").read_text())}
    for n in N_GRID: runs[f"PP:{n}"] = json.loads((d / f"PP_{n}.json").read_text())
    r2 = json.loads((R2D / "RESULTS.json").read_text())["budgets"]["1.5"]; bel = r2["mechanism"]
    A = runs["A"]
    if A["decode"] != r2["A"]["decode"]: raise R3Error("A replay differs from the frozen R2 A replay")
    r1pp = json.loads((R1D / "FIDELITY_RESULTS.json").read_text())["replay"]["PP"]["replay"]
    if any(runs["PP:1"]["decode"][k] != r1pp["decode"][k] for k in ("misses", "hits", "miss_bytes", "evictions")) or (runs["PP:1"]["decode_protected_events"], runs["PP:1"]["decode_protected_entries"]) != (r1pp["decode"]["protected_events"], r1pp["decode"]["protected_entries"]):
        raise R3Error("PP N=1 does not reproduce the frozen R1 PP replay")
    bm, bb = bel["misses_avoided_decode"], bel["miss_bytes_avoided_decode"]
    p0 = _load(P0 / "phase0.py", "_r3_05_p0"); env = p0.envelope(); kappa_low = env["derived"]["kappa_ms_per_MB_low"]; c0 = env["inputs"]["C0_wall_cost_ms"]; ceiling = env["inputs"]["B_ceiling_ms_frozen_R3_04"]
    table = {}
    ppb = benefit(A, runs["PP:1"])
    def row(arm, x):
        b = benefit(A, x); ret = {"misses": float(Fraction(b["misses_avoided"], ppb["misses_avoided"])), "miss_bytes": float(Fraction(b["miss_bytes_avoided"], ppb["miss_bytes_avoided"]))}
        return {"misses_per_token": per_token(x["decode"]["misses"]), "miss_bytes_per_token": per_token(x["decode"]["miss_bytes"]), "decode_misses": x["decode"]["misses"], "decode_miss_bytes": x["decode"]["miss_bytes"],
                "misses_avoided_vs_A": b["misses_avoided"], "miss_bytes_avoided_vs_A": b["miss_bytes_avoided"], "benefit_retained_vs_full_PP": ret,
                "retained_exact": {"misses": f"{b['misses_avoided']}/{ppb['misses_avoided']}", "miss_bytes": f"{b['miss_bytes_avoided']}/{ppb['miss_bytes_avoided']}"},
                "fraction_of_belady_headroom": {"misses": float(Fraction(b["misses_avoided"], bm)), "miss_bytes": float(Fraction(b["miss_bytes_avoided"], bb))},
                "decode_evictions": x["decode"]["evictions"], "decode_hits": x["decode"]["hits"], "budget_violations": x["budget_violations"], "overflow_events": x["overflow_events"],
                "decode_protected_events": x["decode_protected_events"], "decode_protected_entries": x["decode_protected_entries"], "resident_peak_bytes": x["resident_peak_bytes"]}
    table["A"] = row("A", A); table["A"]["benefit_retained_vs_full_PP"] = {"misses": 0.0, "miss_bytes": 0.0}
    table["CES"] = row("CES", runs["CES"])
    curve = {}
    for n in N_GRID:
        x = runs[f"PP:{n}"]; r = row(f"PP:{n}", x); cnt = x["predictor_refresh_count"]
        r.update({"N": n, "invocation_fraction": float(Fraction(cnt, N_EVENTS)), "invocation_fraction_exact": f"{cnt}/{N_EVENTS}", "predictor_refresh_count": cnt, "expected_refresh_count": refresh_count_expected(n),
                  "PROPORTIONAL_COST_MODEL_ONLY": {"label": "PROPORTIONAL_COST_MODEL_ONLY (not measured)", "estimated_cost_ms_per_token": float(Fraction(cnt, N_EVENTS)) * c0, "benefit_ms_at_kappa_low": r["miss_bytes_avoided_vs_A"] / N_DECODE / 1e6 * kappa_low,
                                                  "net_ms_best_case": r["miss_bytes_avoided_vs_A"] / N_DECODE / 1e6 * kappa_low - float(Fraction(cnt, N_EVENTS)) * c0}})
        curve[str(n)] = r; table[f"PP N={n}"] = r
    k2 = k2_decide(table["CES"]["miss_bytes_avoided_vs_A"], table["PP N=1"]["miss_bytes_avoided_vs_A"])
    k3 = k3_decide(table[f"PP N={K3_N}"]["miss_bytes_avoided_vs_A"], table["PP N=1"]["miss_bytes_avoided_vs_A"])
    k2["misses_ratio_exact"] = f"{table['CES']['misses_avoided_vs_A']}/{table['PP N=1']['misses_avoided_vs_A']}"; k2["misses_ratio"] = float(Fraction(table["CES"]["misses_avoided_vs_A"], table["PP N=1"]["misses_avoided_vs_A"]))
    k3["retained_misses_exact"] = table[f"PP N={K3_N}"]["retained_exact"]["misses"]
    # incremental: PP beyond CES (same effect metric)
    beyond = {"pp_minus_ces_decode_misses_avoided": table["PP N=1"]["misses_avoided_vs_A"] - table["CES"]["misses_avoided_vs_A"], "pp_minus_ces_decode_miss_bytes_avoided": table["PP N=1"]["miss_bytes_avoided_vs_A"] - table["CES"]["miss_bytes_avoided_vs_A"]}
    return {"runs": runs, "table": table, "curve": curve, "k2": k2, "k3": k3, "beyond_ces": beyond, "belady": {"misses_headroom_decode": bm, "miss_bytes_headroom_decode": bb, "misses_headroom_per_token": float(Fraction(bm, N_DECODE)), "conservative_value_ms": 92.21555273959194},
            "economic_context": {"kappa_low_ms_per_MB": kappa_low, "current_predictor_wall_cost_ms": c0, "frozen_current_mechanism_benefit_ceiling_ms": ceiling}}


def build(d: Path) -> str:
    inp = verify_inputs(); f = assemble(d)
    ces = {"artifact": "R3-05-R3-CES-RESULTS", "canonical_stream_sha256": STREAM_SHA, "budget": "1.5 GiB", "A": f["runs"]["A"], "CES": f["runs"]["CES"], "PP_full": f["runs"]["PP:1"], "table_rows": {k: f["table"][k] for k in ("A", "CES", "PP N=1")}, "pp_beyond_ces": f["beyond_ces"], "effect_metric": EFFECT_METRIC}
    curve = {"artifact": "R3-05-R3-PP-CURVE", "canonical_stream_sha256": STREAM_SHA, "budget": "1.5 GiB", "refresh_definition": "refresh when (same-layer event position i) % N == 0; history always recorded; predict() is a pure function of history; protected set held between refreshes (Phase 0 PP_CURVE)",
             "N_grid": N_GRID, "curve": f["curve"], "runs": {k: v for k, v in f["runs"].items() if k.startswith("PP:")}, "economic_context": f["economic_context"], "effect_metric": EFFECT_METRIC}
    k2 = {"artifact": "R3-05-R3-K2-DECISION", "rule": "K2 (Phase 0): retire current PP if CES decode miss bytes avoided >= 0.8 x PP decode miss bytes avoided (exact fractions)", "threshold": 0.8, "effect_metric": EFFECT_METRIC, **f["k2"], "inputs": inp,
          "interpretation": "does not imply anything about NUE_X"}
    k3 = {"artifact": "R3-05-R3-K3-DECISION", "rule": f"K3 (Phase 0): drop the frequency-reduction family if the PP benefit retained at f = 0.10 (N = {K3_N}) is <= 0.2 (exact fractions)", "threshold": 0.2, "N": K3_N, "effect_metric": EFFECT_METRIC, **f["k3"], "inputs": inp,
          "interpretation": "SURVIVES would only mean amortization is still worth considering; it is not a success"}
    dt = {"artifact": "R3-05-R3-DECISION-TABLE", "canonical_stream_sha256": STREAM_SHA, "belady_context": f["belady"], "rows": f["table"], "pp_beyond_ces": f["beyond_ces"]}
    for n, t in (("CES_RESULTS.json", ces), ("PP_CURVE.json", curve), ("K2_DECISION.json", k2), ("K3_DECISION.json", k3), ("DECISION_TABLE.json", dt), ("SOURCE_IDENTITY.json", {"artifact": "R3-05-R3-SOURCE-IDENTITY", **inp})):
        (HERE / n).write_text(dumps(t), encoding="utf-8")
    fz = {"artifact": "R3-05-R3-FREEZE", "k2": k2["classification"], "k3": k3["classification"], "files_sha256": {n: sha_bytes((HERE / n).read_bytes()) for n in FILES}, **inp,
          "authorizes": "Nothing model-bearing. The next gate (NUE_X / NUE_LO DEV selection and EVAL replay) needs its own prompt."}
    fz["freeze_sha256"] = freeze_sha(fz); (HERE / "FREEZE.json").write_text(dumps(fz), encoding="utf-8"); return fz["freeze_sha256"]


def verify(replay_too: bool = False) -> List[str]:
    p: List[str] = []
    try:
        verify_inputs()
    except R3Error as e:
        return [str(e)]
    fz = json.loads((HERE / "FREEZE.json").read_text())
    if fz["freeze_sha256"] != freeze_sha(fz): p.append("freeze sha mismatch")
    for n, h in fz["files_sha256"].items():
        if sha_bytes((HERE / n).read_bytes()) != h: p.append(f"{n} differs from freeze")
    if any(s in "".join((HERE / n).read_text(encoding="utf-8") for n in FILES[:9]) for s in ("alpel", "C:\\\\")): p.append("host path leaked")
    ces = json.loads((HERE / "CES_RESULTS.json").read_text()); curve = json.loads((HERE / "PP_CURVE.json").read_text()); k2 = json.loads((HERE / "K2_DECISION.json").read_text()); k3 = json.loads((HERE / "K3_DECISION.json").read_text())
    dt = json.loads((HERE / "DECISION_TABLE.json").read_text())["rows"]
    A = ces["A"]["decode"]
    def eff(x): return A["miss_bytes"] - x["decode"]["miss_bytes"]
    kk2 = k2_decide(eff(ces["CES"]), eff(curve["runs"]["PP:1"]))
    if any(k2[k] != kk2[k] for k in ("classification", "ratio_exact")): p.append("K2 does not recompute")
    kk3 = k3_decide(eff(curve["runs"][f"PP:{K3_N}"]), eff(curve["runs"]["PP:1"]))
    if any(k3[k] != kk3[k] for k in ("classification", "retained_exact")): p.append("K3 does not recompute")
    for n in N_GRID:
        x = curve["runs"][f"PP:{n}"]
        if x["predictor_refresh_count"] != refresh_count_expected(n): p.append(f"refresh count N={n}")
        if x["budget_violations"] != 0 or x["decode"]["miss_bytes"] != dt[f"PP N={n}"]["decode_miss_bytes"]: p.append(f"curve row N={n}")
    r1pp = json.loads((R1D / "FIDELITY_RESULTS.json").read_text())["replay"]["PP"]["replay"]["decode"]
    if any(curve["runs"]["PP:1"]["decode"][k] != r1pp[k] for k in ("misses", "hits", "miss_bytes", "evictions")): p.append("PP N=1 differs from frozen R1 PP")
    if replay_too:
        ev = load_events()
        for arm, rec in (("CES", ces["CES"]), ("PP:1", curve["runs"]["PP:1"])):
            if replay(ev, make(arm)) != rec: p.append(f"re-run of {arm} differs from the recorded result")
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("args", nargs="*"); ap.add_argument("--replay", action="store_true"); a = ap.parse_args()
    if a.cmd == "run":
        arm, out = a.args[0], Path(a.args[1]); verify_inputs(); r = replay(load_events(), make(arm)); out.write_text(dumps(r)); print(arm, r["decode"]["misses"], r["decode"]["miss_bytes"], r["budget_violations"], r.get("predictor_refresh_count"))
    elif a.cmd == "build":
        print("freeze", build(Path(a.args[0])))
    elif a.cmd == "verify":
        pr = verify(a.replay); print("VERIFY", "OK" if not pr else pr); sys.exit(1 if pr else 0)
