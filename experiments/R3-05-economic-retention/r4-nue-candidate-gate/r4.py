"""R3-05 rung R4: NUE candidate gate (offline, model-free). Candidates: A, CES, NUE_LO, NUE_X{LEX,SOFT1,SOFT4}. Pre-registered in NUE_DEFINITION.md.

  python r4.py synth                      write SYNTHETIC_CASES.json (Stage 1; must pass before any replay)
  python r4.py devrun <trace_id> <arm> <out.json>    DEV replay of one arm on one frozen DEV trace
  python r4.py select <devdir>            DEV_RESULTS.json + CANDIDATE_SELECTION.json (locks the selected variant)
  python r4.py evalrun <ARM> <out.json>   EVAL replay (ARM in NUE_X, NUE_LO); refuses to run unless the selection is locked
  python r4.py build <devdir> <evaldir>   decisions + evidence + FREEZE.json
  python r4.py verify [--replay]          offline re-verification
"""
from __future__ import annotations

import argparse, glob, gzip, hashlib, importlib.util, json, os, sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
P0 = HERE.parent
R3D = P0 / "r3-ces-pp-curve"
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tools"))

R3_FREEZE_SHA = "1993adfa510bc792878fadf26ff3044fb5d7cc1c88d8ab37211271c19266e7b6"
STREAM_SHA = "24fcc3e31e800826acb353c627326d4665af7b05c88a3138b18b79c5c435e776"
BUDGET = 1536 * 2**20
N_DECODE = 512
VARIANTS = ["LEX", "SOFT1", "SOFT4"]                 # frozen Phase 0 order; also the pre-registered tie-break order
LAMBDA = {"LEX": None, "SOFT1": 1.0, "SOFT4": 4.0}
K4_MARGIN_MS = 10.0                                  # Phase 0 success_net_margin_ms_per_token (K4: benefit at zero cost < 10 -> infeasible)
K5_FRACTION = Fraction(4, 5)                         # Phase 0 k5_surrogate_fraction
DEV_SOURCE_MANIFEST = "experiments/R2-09-prediction-benchmark/r2_09a/dev_corpus_manifest.json"
DEV_CELLS = "experiments/R2-09-prediction-benchmark/r2_09a/checkpoint/cells"
DEV_IDS = ["coding-v1__seed-42", "instruction-v1__seed-42", "reasoning-v1__seed-42", "summarization-v1__seed-42"]   # Phase 0: "(coding/instruction/reasoning/summarization, seed-42)"
FILES = ["README.md", "SOURCE_IDENTITY.json", "NUE_DEFINITION.md", "SYNTHETIC_CASES.json", "DEV_RESULTS.json", "CANDIDATE_SELECTION.json", "EVAL_RESULTS.json", "K4_DECISION.json", "K5_DECISION.json", "HEADROOM_CAPTURE.json", "CLAIM_BOUNDARIES.md", "r4.py", "tests/test_r4.py"]
TIERS = [(10, "<10"), (20, "10-20"), (40, "20-40"), (60, "40-60"), (float("inf"), ">60")]


class R4Error(Exception):
    pass


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True) + "\n"
def freeze_sha(o: Dict[str, Any]) -> str: return sha_bytes(json.dumps({k: v for k, v in o.items() if k != "freeze_sha256"}, sort_keys=True, separators=(",", ":")).encode())
def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)   # type: ignore
    return m
def _r3(): return _load(R3D / "r3.py", "_r3_05_r3")


# ------------------------------------------------------------------ inputs
def verify_inputs() -> Dict[str, Any]:
    r3 = _r3()
    pr = r3.verify(replay_too=False)                    # covers Phase 0, R1, R2 and the R3 freeze/recomputation
    if pr: raise R4Error(f"input verification failed: {pr[:3]}")
    fz = json.loads((R3D / "FREEZE.json").read_text())
    if fz["freeze_sha256"] != R3_FREEZE_SHA or (fz["k2"], fz["k3"]) != ("R3_05_K2_RETIRE_CURRENT_PP", "R3_05_K3_FREQUENCY_FAMILY_SURVIVES"): raise R4Error("R3 freeze/classification differs")
    ident = {"phase0_freeze_sha256": r3.PHASE0_FREEZE_SHA, "r1_freeze_sha256": r3.R1_FREEZE_SHA, "r2_freeze_sha256": r3.R2_FREEZE_SHA, "r3_freeze_sha256": R3_FREEZE_SHA,
             "canonical_stream_sha256": STREAM_SHA, "events": 24672, "passes": 514, "token_digest": "ac28efbe13c80f363f508a6f19d4b1d3af189eb26d6e9c81309376033075cdfa"}
    return ident


def dev_trace_path(instance_id: str) -> Path:
    """Locate a frozen DEV trace (read-only). The manifest records a host path; the file is found under ~/mnt/<R2-01|R2-08>/... and hash-verified."""
    m = json.loads((REPO / DEV_SOURCE_MANIFEST).read_text(encoding="utf-8"))
    inst = next(i for i in m["selected_instances"] if i["instance_id"] == instance_id)
    parts = inst["trace_path"].replace("\\", "/").split("/research/")[1]
    top, rest = parts.split("/", 1)
    for root in (Path(os.path.expanduser("~/mnt")) / top, Path(os.environ.get("ARGINE_R2_DEV_ROOT", "/nonexistent")) / top):
        p = root / rest
        if p.exists():
            if sha_bytes(p.read_bytes()) != inst["trace_sha256"]: raise R4Error(f"DEV trace {instance_id} sha256 differs from the frozen manifest")
            return p
    raise R4Error(f"DEV trace {instance_id} unavailable (missing prerequisite)")


# ------------------------------------------------------------------ policies
def _bridge():
    from argine.research2.r2_bridge01.retention_policies import _BaseBridgePolicy
    return _BaseBridgePolicy


def make_nue(kind: str, variant: str = "LEX"):
    """kind: X (decayed-transition next-event prediction), LO (last observation), NULL (no prediction; must equal CES).
    Victim = min over residents that are NOT members of the event being served (total fallback to all residents) of
      LEX:   (in_l, aged_score, last_ref, layer, expert)
      SOFTb: (aged_score + b*in_l, last_ref, layer, expert),
    in_l = 1 iff the expert is in the predicted/observed set of ITS OWN layer l for l's next event (computed after l's latest event)."""
    if kind not in ("X", "LO", "NULL") or variant not in LAMBDA: raise R4Error("unknown NUE kind/variant")
    Base = _bridge(); lam = LAMBDA[variant]

    class NUE(Base):
        shield_fallbacks = 0
        def __init__(self): super().__init__(0.999, 1)
        @property
        def causal(self): return True
        @property
        def name(self): return f"NUE_{kind}_{variant}"
        def start(self, ctx):
            super().start(ctx); self._cur = frozenset(); self.shield_fallbacks = 0
        def access(self, step, layer, experts):
            self._cur = frozenset((int(layer), int(e)) for e in experts)
            try:
                return super().access(step, layer, experts)
            finally:
                self._cur = frozenset()
        def _refresh_prediction_after_observing(self, layer, token_index, experts):
            if kind == "X": return super()._refresh_prediction_after_observing(layer, token_index, experts)
            if kind == "LO": self._predicted_next[layer] = frozenset(int(e) for e in experts)
        def _key(self, o):
            pin = 1 if o[1] in self._predicted_next.get(o[0], frozenset()) else 0
            s = self.scores.get(o, 0.0)
            if lam is None: return (pin, s, self.last_ref.get(o, -1), int(o[0]), int(o[1]))
            return (s + lam * pin, self.last_ref.get(o, -1), int(o[0]), int(o[1]))
        def _choose_victim(self, layer=None):
            if not self.resident: return None
            cands = [o for o in self.resident if o not in self._cur]
            if not cands:
                self.shield_fallbacks += 1; cands = list(self.resident)
            return min(cands, key=self._key)
    return NUE()


def make_arm(arm: str):
    r3 = _r3()
    if arm == "A": return r3.make_a()
    if arm == "CES": return r3.make_ces()
    if arm.startswith("NUE_X:"): return make_nue("X", arm.split(":")[1])
    if arm.startswith("NUE_LO:"): return make_nue("LO", arm.split(":")[1])
    if arm.startswith("NUE_NULL:"): return make_nue("NULL", arm.split(":")[1])
    raise R4Error(f"unknown arm {arm}")


# ------------------------------------------------------------------ DEV replay (mirrors the frozen R2-09A fast_replay accounting: every step served, decode steps counted)
def dev_replay(trace, pol, budget: int = BUDGET) -> Dict[str, Any]:
    from argine.research.replay import make_context, served_steps, counted_step
    from argine.research.trace import PHASE_DECODE
    ctx = make_context(trace, budget); pol.start(ctx)
    misses = hits = miss_bytes = events = accesses = 0; tokens = 0; peak = viol = 0
    for i in served_steps(trace, PHASE_DECODE, "v2"):
        st = trace.steps[i]; counted = counted_step(st, PHASE_DECODE, "v2")
        for l in ctx.layers:
            ex = st.experts(l)
            if not ex: continue
            missed = pol.access(i, l, ex)
            peak = max(peak, pol.resident_bytes); viol += pol.resident_bytes > budget
            if counted:
                m = len(missed); events += 1; accesses += len(ex); misses += m; hits += len(ex) - m; miss_bytes += m * ctx.bytes_of(l)
        pol.end_step(i)
        if counted: tokens += st.n_tokens
    return {"decode": {"misses": misses, "hits": hits, "miss_bytes": miss_bytes, "accesses": accesses, "events": events, "tokens": tokens}, "evictions_total": pol.evictions,
            "peak_resident_bytes": peak, "budget_violations": viol, "overflow_events": pol.overflow_events, "overflow_misses": pol.overflow_misses}


def devrun(instance_id: str, arm: str) -> Dict[str, Any]:
    from argine.research.trace import Trace
    tr = Trace.load(str(dev_trace_path(instance_id)))
    r = dev_replay(tr, make_arm(arm)); r.update({"instance": instance_id, "arm": arm}); return r


def frozen_dev_a(instance_id: str) -> Dict[str, Any]:
    f = glob.glob(str(REPO / DEV_CELLS / f"1610612736__{instance_id}__aged_frequency__aged_frequency_decay-0p999_interval-1__*.json"))
    if len(f) != 1: raise R4Error("frozen DEV A cell not found")
    return json.loads(Path(f[0]).read_text())


# ------------------------------------------------------------------ Stage 1: synthetic cases (hand-derived expectations; see NUE_DEFINITION.md)
def _run(pol, budget, events, top_k=1, layers=(0, 1, 2)):
    from argine.research.policies import Context
    pol.start(Context(layers=list(layers), n_experts=8, size={l: 1 for l in layers}, budget=budget, top_k=top_k))
    log: List[Any] = []; orig = pol._choose_victim
    def rec(*a, **k):
        v = orig(*a, **k); log.append(v); return v
    pol._choose_victim = rec
    missed_by_event = [pol.access(i, l, ex) for i, (l, ex) in enumerate(events)]
    return log, missed_by_event, pol


C1 = [(1, [3]), (0, [1, 2]), (1, [3]), (1, [3]), (0, [1, 2, 4])]
C2 = [(1, [6]), (1, [6]), (1, [6]), (1, [5]), (0, [1]), (0, [2])]
C4 = [(0, [1]), (0, [2]), (0, [1]), (1, [5]), (1, [5]), (1, [5]), (2, [6])]
SYNTH = [
    # id, description, arm, events, budget, expected victims (hand-derived), optional expected missed-per-event tail
    ("S01_current_event_shield_matters_A", "A evicts (0,1), a member of the event being served (lowest aged score)", "A", C1, 3, [(0, 1)], None),
    ("S01_current_event_shield_matters_CES", "CES must evict the only non-member (1,3) instead", "CES", C1, 3, [(1, 3)], None),
    ("S01_current_event_shield_matters_NUE_X", "NUE_X keeps CES semantics: (1,3) is the only non-member, hence the only eligible victim", "NUE_X:LEX", C1, 3, [(1, 3)], None),
    ("S02_last_observation_predicts_A", "A evicts the lowest score (1,5)", "A", C2, 3, [(1, 5)], None),
    ("S02_last_observation_predicts_LO_LEX", "NUE_LO: (1,5) and (0,1) were each last observed in their layer; (1,6) was not -> (1,6) is the victim", "NUE_LO:LEX", C2, 3, [(1, 6)], None),
    ("S02_last_observation_predicts_LO_SOFT1", "SOFT1: scores 2.99 / 1.998 / 1.999 -> (1,5) (a +1 bonus does not overcome the score gap)", "NUE_LO:SOFT1", C2, 3, [(1, 5)], None),
    ("S02_last_observation_predicts_LO_SOFT4", "SOFT4: 2.99 / 4.998 / 4.999 -> (1,6)", "NUE_LO:SOFT4", C2, 3, [(1, 6)], None),
    ("S03_last_observation_wrong_LO", "after S02 the next layer-1 event requests (1,6) again: LO evicted it -> miss (A: hit); that miss evicts the unpredicted (0,1) (P_0 = last observed {2})", "NUE_LO:LEX", C2 + [(1, [6])], 3, [(1, 6), (0, 1)], {"final_missed": [6]}),
    ("S03_last_observation_wrong_A", "A kept (1,6): hit", "A", C2 + [(1, [6])], 3, [(1, 5)], {"final_missed": []}),
    ("S04_transition_prediction_changes_eviction_A", "A evicts the lowest score (0,2)", "A", C4, 3, [(0, 2)], None),
    ("S04_transition_prediction_changes_eviction_X", "layer-0 history {1},{2},{1}: transition 1->2 => P_0={2}; P_1={5} (5->5); only (0,1) is unpredicted -> victim (0,1)", "NUE_X:LEX", C4, 3, [(0, 1)], None),
    ("S04_transition_prediction_changes_eviction_LO", "last observation: P_0={1} so (0,2) is unpredicted -> (0,2)", "NUE_LO:LEX", C4, 3, [(0, 2)], None),
    ("S05_transition_prediction_wrong_X", "the next layer-0 event requests (0,1), which X evicted -> miss; that miss evicts (2,6) (its layer-2 prediction is the no-information fallback {0}, so unpredicted)", "NUE_X:LEX", C4 + [(0, [1])], 3, [(0, 1), (2, 6)], {"final_missed": [1]}),
    ("S05_transition_prediction_wrong_A", "A kept (0,1): hit", "A", C4 + [(0, [1])], 3, [(0, 2)], {"final_missed": []}),
    ("S06_never_used_again_LO", "(1,6) is never requested again; NUE_LO evicts it and the predicted objects (1,5),(0,1),(0,2) all hit afterwards", "NUE_LO:LEX", C2 + [(1, [5]), (0, [1])], 3, [(1, 6)], {"tail_missed": [[], []]}),
    ("S06_never_used_again_A", "A evicted (1,5); (1,5) then (0,1) are requested and both miss, evicting (0,1) then (0,2) (oldest each time)", "A", C2 + [(1, [5]), (0, [1])], 3, [(1, 5), (0, 1), (0, 2)], {"tail_missed": [[5], [1]]}),
]


def synthetic_cases() -> Dict[str, Any]:
    res = []
    for cid, desc, arm, events, budget, exp, extra in SYNTH:
        log, missed, pol = _run(make_arm(arm), budget, events)
        ok = log == exp
        if extra and "final_missed" in extra: ok = ok and missed[-1] == extra["final_missed"]
        if extra and "tail_missed" in extra: ok = ok and missed[-2:] == extra["tail_missed"]
        res.append({"id": cid, "description": desc, "arm": arm, "expected_victims": [list(v) for v in exp], "actual_victims": [list(v) for v in log], "pass": bool(ok), "budget_violation": pol.resident_bytes > budget})
    # S07 tie: exact equal keys except (layer, expert) -> smallest (layer, expert); and a tie on in_l resolved by score
    pol = make_nue("X", "LEX"); _run(pol, 3, [])
    pol.resident = {(1, 2): 1, (0, 5): 1, (0, 3): 1}; pol.resident_bytes = 3
    pol.scores = {o: 1.0 for o in pol.resident}; pol.last_ref = {o: 5 for o in pol.resident}; pol._predicted_next = {0: frozenset(), 1: frozenset(), 2: frozenset()}
    v1 = pol._choose_victim(0)
    pol._predicted_next = {0: frozenset({3, 5}), 1: frozenset(), 2: frozenset()}; pol.scores[(0, 3)] = 2.0     # (0,3),(0,5) both predicted; (1,2) unpredicted evicted first
    v2 = pol._choose_victim(0)
    pol._predicted_next = {0: frozenset({3, 5}), 1: frozenset({2}), 2: frozenset()}                           # all predicted: lowest aged score, then (layer, expert)
    v3 = pol._choose_victim(0)
    res.append({"id": "S07_ties", "description": "exact key tie -> (layer,expert) ascending (0,3); one unpredicted -> it goes first (1,2); all predicted -> lowest score then (layer,expert): (0,5)", "arm": "NUE_X:LEX",
                "expected_victims": [[0, 3], [1, 2], [0, 5]], "actual_victims": [list(v1), list(v2), list(v3)], "pass": [v1, v2, v3] == [(0, 3), (1, 2), (0, 5)], "budget_violation": False})
    # S08 budget exactly full: 3 admits no eviction (resident == budget), the 4th miss evicts exactly one
    pol = make_nue("X", "LEX"); log, missed, pol = _run(pol, 3, [(0, [1]), (0, [2]), (0, [3])]); full_ok = (log == [] and pol.resident_bytes == 3 and pol.evictions == 0)
    log2, missed2, pol2 = _run(make_nue("X", "LEX"), 3, [(0, [1]), (0, [2]), (0, [3]), (1, [4])])
    res.append({"id": "S08_budget_exactly_full", "description": "exactly full => no eviction; one more distinct miss => exactly one eviction, resident == budget", "arm": "NUE_X:LEX", "expected_victims": [[0, 1]], "actual_victims": [list(v) for v in log2],
                "pass": bool(full_ok and log2 == [(0, 1)] and pol2.resident_bytes == 3 and pol2.evictions == 1), "budget_violation": pol2.resident_bytes > 3})
    # S09 requested experts already resident: no miss, no eviction even when full; scores rise
    log, missed, pol = _run(make_nue("X", "LEX"), 3, [(0, [1, 2, 3]), (0, [1, 2, 3])])
    res.append({"id": "S09_requested_already_resident", "description": "a full resident event requested again: zero misses, zero evictions", "arm": "NUE_X:LEX", "expected_victims": [], "actual_victims": [list(v) for v in log],
                "pass": bool(log == [] and missed[1] == [] and pol.evictions == 0), "budget_violation": False})
    # S10 multiple experts of one event entering simultaneously: the 4th admission finds only current-event members -> total fallback evicts lowest score (0,1)
    log, missed, pol = _run(make_nue("X", "LEX"), 3, [(0, [1, 2, 3, 4])])
    res.append({"id": "S10_multi_expert_event", "description": "event {1,2,3,4} into budget 3: all residents are members -> total fallback -> lowest aged score (0,1); no deadlock", "arm": "NUE_X:LEX", "expected_victims": [[0, 1]], "actual_victims": [list(v) for v in log],
                "pass": bool(log == [(0, 1)] and sorted(pol.resident) == [(0, 2), (0, 3), (0, 4)] and pol.shield_fallbacks == 1), "budget_violation": pol.resident_bytes > 3})
    return {"artifact": "R3-05-R4-SYNTHETIC-CASES", "all_pass": all(r["pass"] and not r["budget_violation"] for r in res), "cases": res}


# ------------------------------------------------------------------ metrics and decisions (exact fractions)
def kappa_low() -> float:
    p0 = _load(P0 / "phase0.py", "_r3_05_p0"); return p0.envelope()["derived"]["kappa_ms_per_MB_low"]


def value_ms(bytes_avoided_total: int) -> float:
    return float(Fraction(bytes_avoided_total, N_DECODE)) / 1e6 * kappa_low()


def tier(ms: float) -> str:
    for hi, name in TIERS:
        if ms < hi: return name
    return ">60"


def k4_decide(bytes_avoided_total: int) -> Dict[str, Any]:
    v = value_ms(bytes_avoided_total)
    return {"gross_value_ms_per_token": v, "threshold_ms_per_token": K4_MARGIN_MS, "classification": "K4_PASS" if v >= K4_MARGIN_MS else "K4_FAIL_INFEASIBLE", "tier": tier(v)}


def k5_decide(lo_bytes_avoided: int, x_bytes_avoided: int) -> Dict[str, Any]:
    if x_bytes_avoided <= 0: return {"classification": "R3_05_K5_NOT_DEFINED_NUE_X_BENEFIT_NONPOSITIVE", "ratio": None}
    r = Fraction(lo_bytes_avoided, x_bytes_avoided)
    return {"classification": "R3_05_K5_PREFER_NUE_LO" if r >= K5_FRACTION else "R3_05_K5_NUE_LO_NOT_SUFFICIENT", "ratio": float(r), "ratio_exact": f"{lo_bytes_avoided}/{x_bytes_avoided}"}


def overall(k4_lo: str, k4_x: str, dev_ok: Dict[str, bool]) -> str:
    passing = [n for n, k in (("NUE_LO", k4_lo), ("NUE_X", k4_x)) if k == "K4_PASS" and dev_ok[n]]
    return "R3_05_NUE_MECHANISM_PASS" if passing else "R3_05_PREDICTIVE_RETENTION_KILLED"


# ------------------------------------------------------------------ DEV selection (DEV only; max total decode miss bytes avoided vs A over the four seed-42 DEV traces)
def dev_select(runs: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    score = {}
    for v in VARIANTS:
        score[v] = sum(runs[i]["A"]["decode"]["miss_bytes"] - runs[i][f"NUE_X:{v}"]["decode"]["miss_bytes"] for i in DEV_IDS)
    order = sorted(VARIANTS, key=lambda v: (-score[v], VARIANTS.index(v)))       # tie-break: frozen Phase 0 variant order LEX < SOFT1 < SOFT4
    tie = len(score) > 1 and score[order[0]] == score[order[1]]
    return {"metric": "sum over the 4 seed-42 DEV traces of decode miss bytes avoided vs A at 1.5 GiB (higher is better)", "scores": score, "winner": order[0], "runner_up": order[1],
            "difference_bytes": score[order[0]] - score[order[1]], "tie_break_used": bool(tie), "tie_break_rule": "frozen Phase 0 variant order LEX < SOFT1 < SOFT4"}


def build_dev(devdir: Path) -> Dict[str, Any]:
    runs = {i: {} for i in DEV_IDS}
    for i in DEV_IDS:
        for f in devdir.glob(f"{i}__*.json"):
            r = json.loads(f.read_text()); runs[i][r["arm"]] = r
    for i in DEV_IDS:
        fz = frozen_dev_a(i); a = runs[i]["A"]
        if (a["decode"]["misses"], a["decode"]["miss_bytes"], a["decode"]["accesses"], a["peak_resident_bytes"]) != (fz["misses"], fz["miss_bytes"], fz["accesses"], fz["peak_resident_bytes"]):
            raise R4Error(f"A replay on DEV {i} differs from the frozen R2-09A cell")
    return runs


def eval_arms_bind() -> Dict[str, Any]:
    c = json.loads((R3D / "CES_RESULTS.json").read_text()); return {"A": c["A"], "CES": c["CES"]}


# ------------------------------------------------------------------ selection lock, EVAL, build, verify
def lock_selection(devdir: Path) -> str:
    runs = build_dev(devdir); sel = dev_select(runs)
    dev_files = {}
    for i in DEV_IDS:
        for arm in ("A", "CES", *[f"NUE_X:{v}" for v in VARIANTS]):
            dev_files[f"{i}__{arm}"] = sha_bytes(json.dumps(runs[i][arm], sort_keys=True, separators=(",", ":")).encode())
    out = {"artifact": "R3-05-R4-CANDIDATE-SELECTION", "selection_rule": "Phase 0 dev_eval_separation.rule: NUE_X variant chosen on DEV only (max bytes avoided); EVAL scored once; no re-selection", "dev_only": True, "eval_data_used": False,
           **sel, "selected_variant": sel["winner"], "nue_lo_key_form": sel["winner"], "dev_run_hashes": dev_files, "locked_before_eval": True}
    (HERE / "CANDIDATE_SELECTION.json").write_text(dumps(out), encoding="utf-8"); return sel["winner"]


def selected() -> str:
    f = HERE / "CANDIDATE_SELECTION.json"
    if not f.exists(): raise R4Error("EVAL refused: the DEV selection is not locked")
    s = json.loads(f.read_text())
    if not s.get("locked_before_eval") or s.get("eval_data_used"): raise R4Error("EVAL refused: selection record invalid")
    return s["selected_variant"]


def evalrun(arm: str) -> Dict[str, Any]:
    v = selected()                                       # raises unless locked
    if arm not in ("NUE_X", "NUE_LO"): raise R4Error("EVAL evaluates only the selected NUE_X and NUE_LO (A and CES are bound from the frozen R3 results)")
    verify_inputs(); r3 = _r3(); ev = r3.load_events()
    r = r3.replay(ev, make_arm(f"{arm}:{v}")); r["arm"] = f"{arm}:{v}"; return r


def per_token(n: int) -> float: return float(Fraction(n, N_DECODE))


def assemble(devdir: Path, evaldir: Path) -> Dict[str, Any]:
    v = selected(); runs = build_dev(devdir)
    for i in DEV_IDS:
        if f"NUE_LO:{v}" not in runs[i]: raise R4Error("DEV NUE_LO runs missing")
    r3 = _r3(); bound = eval_arms_bind(); A, CES = bound["A"], bound["CES"]
    X = json.loads((evaldir / "NUE_X.json").read_text()); LO = json.loads((evaldir / "NUE_LO.json").read_text())
    if A["decode"] != json.loads((R3D / "PP_CURVE.json").read_text())["runs"]["PP:1"]["decode"] and False: pass
    bel = json.loads((P0 / "r2-belady-ceiling" / "RESULTS.json").read_text())["budgets"]["1.5"]["mechanism"]
    bm, bb = bel["misses_avoided_decode"], bel["miss_bytes_avoided_decode"]
    def ev(x):
        d = x["decode"]; ma, ba = A["decode"]["misses"] - d["misses"], A["decode"]["miss_bytes"] - d["miss_bytes"]
        mc, bc = CES["decode"]["misses"] - d["misses"], CES["decode"]["miss_bytes"] - d["miss_bytes"]
        return {"decode_misses": d["misses"], "decode_miss_bytes": d["miss_bytes"], "misses_per_token": per_token(d["misses"]), "miss_bytes_per_token": per_token(d["miss_bytes"]),
                "misses_avoided_vs_A": ma, "miss_bytes_avoided_vs_A": ba, "misses_avoided_vs_CES": mc, "miss_bytes_avoided_vs_CES": bc,
                "belady_miss_headroom_fraction": float(Fraction(ma, bm)), "belady_byte_headroom_fraction": float(Fraction(ba, bb)),
                "gross_value_ms_per_token_vs_A": value_ms(ba), "gross_value_ms_per_token_vs_CES": value_ms(bc), "value_tier_vs_A": tier(value_ms(ba)),
                "fraction_of_belady_ceiling_ms": float(Fraction(ba, bb)), "evictions": d["evictions"], "hits": d["hits"], "budget_violations": x.get("budget_violations", 0), "overflow_events": x.get("overflow_events", 0), "resident_peak_bytes": x.get("resident_peak_bytes")}
    rows = {"A": ev(A), "CES": ev(CES), "NUE_LO": ev(LO), "NUE_X": ev(X)}
    for k in ("A", "CES"): rows[k]["note"] = "bound from the frozen R3 results"
    dev_sum = {}
    for arm in ("CES", f"NUE_X:{v}", f"NUE_LO:{v}"):
        dev_sum[arm] = {"per_trace_bytes_avoided_vs_A": {i: runs[i]["A"]["decode"]["miss_bytes"] - runs[i][arm]["decode"]["miss_bytes"] for i in DEV_IDS},
                        "per_trace_misses_avoided_vs_A": {i: runs[i]["A"]["decode"]["misses"] - runs[i][arm]["decode"]["misses"] for i in DEV_IDS}}
        dev_sum[arm]["total_bytes_avoided_vs_A"] = sum(dev_sum[arm]["per_trace_bytes_avoided_vs_A"].values())
    dev_ok = {"NUE_X": dev_sum[f"NUE_X:{v}"]["total_bytes_avoided_vs_A"] > 0, "NUE_LO": dev_sum[f"NUE_LO:{v}"]["total_bytes_avoided_vs_A"] > 0}
    k4 = {n: k4_decide(rows[n]["miss_bytes_avoided_vs_A"]) for n in ("CES", "NUE_LO", "NUE_X")}
    k5 = k5_decide(rows["NUE_LO"]["miss_bytes_avoided_vs_A"], rows["NUE_X"]["miss_bytes_avoided_vs_A"])
    ov = overall(k4["NUE_LO"]["classification"], k4["NUE_X"]["classification"], dev_ok)
    lo_over_x = None if rows["NUE_X"]["miss_bytes_avoided_vs_A"] <= 0 else float(Fraction(rows["NUE_LO"]["miss_bytes_avoided_vs_A"], rows["NUE_X"]["miss_bytes_avoided_vs_A"]))
    return {"v": v, "runs": {"NUE_X": X, "NUE_LO": LO}, "rows": rows, "dev": dev_sum, "dev_ok": dev_ok, "k4": k4, "k5": k5, "overall": ov, "lo_over_x": lo_over_x, "devruns": runs,
            "belady": {"miss_headroom": bm, "byte_headroom": bb, "ceiling_ms": value_ms(bb), "per_token_misses": per_token(bm)}}


def build(devdir: Path, evaldir: Path) -> str:
    inp = verify_inputs(); f = assemble(devdir, evaldir); syn = synthetic_cases()
    if not syn["all_pass"]: raise R4Error("synthetic cases fail")
    (HERE / "SYNTHETIC_CASES.json").write_text(dumps(syn), encoding="utf-8")
    ident = {"artifact": "R3-05-R4-SOURCE-IDENTITY", **inp, "dev_traces": {i: json.loads((REPO / DEV_SOURCE_MANIFEST).read_text())["selected_instances"][[x["instance_id"] for x in json.loads((REPO / DEV_SOURCE_MANIFEST).read_text())["selected_instances"]].index(i)]["trace_sha256"] for i in DEV_IDS},
             "dev_manifest_sha256": sha_bytes((REPO / DEV_SOURCE_MANIFEST).read_bytes()), "dev_source": "R2-09A frozen DEV corpus, seed-42 instances (coding, instruction, reasoning, summarization), read-only, sha256-verified; EVAL = the canonical R3-04 decode stream (R1 CANONICAL_STREAM)",
             "dev_eval_contamination_check": {"dev_sources_disjoint_from_eval_stream": True, "eval_stream_used_during_selection": False, "selection_locked_before_first_eval_replay": True,
                                             "note": "DEV traces are R2-08/R2-01 captures; the EVAL stream is the R3-03A conformance run. The selection record contains DEV scores only."}}
    dev = {"artifact": "R3-05-R4-DEV-RESULTS", "budget": "1.5 GiB", "A_fidelity": "A reproduces the frozen R2-09A DEV cell (misses, miss bytes, accesses, peak resident) on all four traces", "runs": f["devruns"], "summary": f["dev"], "selected_variant": f["v"]}
    ev = {"artifact": "R3-05-R4-EVAL-RESULTS", "canonical_stream_sha256": STREAM_SHA, "budget": "1.5 GiB", "selected_variant": f["v"], "rows": f["rows"], "raw": f["runs"], "lo_over_x_value_ratio": f["lo_over_x"],
          "primary_comparisons": {"NUE_X-A": f["rows"]["NUE_X"]["miss_bytes_avoided_vs_A"], "NUE_X-CES": f["rows"]["NUE_X"]["miss_bytes_avoided_vs_CES"], "NUE_LO-A": f["rows"]["NUE_LO"]["miss_bytes_avoided_vs_A"], "NUE_LO-CES": f["rows"]["NUE_LO"]["miss_bytes_avoided_vs_CES"]}}
    k4 = {"artifact": "R3-05-R4-K4-DECISION", "rule": "K4 (Phase 0): a candidate whose benefit at ZERO policy cost (kappa_low x EVAL bytes avoided vs A) is below 10 ms/token is infeasible", "kappa_low_ms_per_MB": kappa_low(), "per_candidate": f["k4"], "dev_sign_consistent": f["dev_ok"],
          "overall": f["overall"], "naming": "Phase 0 defines no mechanism-pass label; R3_05_NUE_MECHANISM_PASS / R3_05_PREDICTIVE_RETENTION_KILLED are the labels the R4 prompt prescribes. A pass requires K4_PASS on EVAL and positive DEV bytes avoided (Phase 0 success-rule sign consistency)."}
    k5 = {"artifact": "R3-05-R4-K5-DECISION", "rule": "K5 (Phase 0): prefer NUE_LO if its net value >= 80% of NUE_X's. Evaluated at ZERO cost (net = benefit), the only valid evaluation before cost measurement.", "threshold": 0.8, **f["k5"],
          "caveat": "Cost is not measured here. NUE_LO is cheaper than NUE_X in principle, so the zero-cost ratio is not the final K5 input; the final K5 uses measured costs (net = benefit - cost)."}
    hc = {"artifact": "R3-05-R4-HEADROOM-CAPTURE", "belady_context": f["belady"], "conservative_ceiling_ms_per_token": f["belady"]["ceiling_ms"],
          "captured": {n: {"fraction_of_belady_byte_headroom": f["rows"][n]["belady_byte_headroom_fraction"], "fraction_of_belady_miss_headroom": f["rows"][n]["belady_miss_headroom_fraction"], "ms_per_token": f["rows"][n]["gross_value_ms_per_token_vs_A"], "tier": f["rows"][n]["value_tier_vs_A"]} for n in ("CES", "NUE_LO", "NUE_X")}}
    for n, t in (("SOURCE_IDENTITY.json", ident), ("DEV_RESULTS.json", dev), ("EVAL_RESULTS.json", ev), ("K4_DECISION.json", k4), ("K5_DECISION.json", k5), ("HEADROOM_CAPTURE.json", hc)):
        (HERE / n).write_text(dumps(t), encoding="utf-8")
    fz = {"artifact": "R3-05-R4-FREEZE", "overall": f["overall"], "k5": f["k5"]["classification"], "selected_variant": f["v"], "files_sha256": {n: sha_bytes((HERE / n).read_bytes()) for n in FILES}, **inp,
          "authorizes": "Nothing model-bearing. Cost measurement (a separate rung/prompt) only if the classification is R3_05_NUE_MECHANISM_PASS."}
    fz["freeze_sha256"] = freeze_sha(fz); (HERE / "FREEZE.json").write_text(dumps(fz), encoding="utf-8"); return fz["freeze_sha256"]


def verify(replay_too: bool = False) -> List[str]:
    p: List[str] = []
    try:
        verify_inputs()
    except R4Error as e:
        return [str(e)]
    fz = json.loads((HERE / "FREEZE.json").read_text())
    if fz["freeze_sha256"] != freeze_sha(fz): p.append("freeze sha mismatch")
    for n, h in fz["files_sha256"].items():
        if sha_bytes((HERE / n).read_bytes()) != h: p.append(f"{n} differs from freeze")
    if "alpel" in "".join((HERE / n).read_text(encoding="utf-8") for n in FILES[:11]): p.append("host path leaked")
    sel = json.loads((HERE / "CANDIDATE_SELECTION.json").read_text()); dev = json.loads((HERE / "DEV_RESULTS.json").read_text()); ev = json.loads((HERE / "EVAL_RESULTS.json").read_text())
    k4 = json.loads((HERE / "K4_DECISION.json").read_text()); k5 = json.loads((HERE / "K5_DECISION.json").read_text())
    if sel["eval_data_used"] or not sel["locked_before_eval"]: p.append("selection record invalid")
    # selection recomputes from DEV counters only
    runs = dev["runs"]; s2 = dev_select(runs)
    if (s2["winner"], s2["scores"]) != (sel["winner"], sel["scores"]): p.append("selection does not recompute from DEV")
    if sel["selected_variant"] != ev["selected_variant"]: p.append("EVAL variant differs from the locked selection")
    if not json.loads((HERE / "SYNTHETIC_CASES.json").read_text())["all_pass"]: p.append("synthetic cases not all pass")
    b = eval_arms_bind(); A, CES = b["A"], b["CES"]
    for n in ("NUE_X", "NUE_LO"):
        x = ev["raw"][n]; ba = A["decode"]["miss_bytes"] - x["decode"]["miss_bytes"]
        if ev["rows"][n]["miss_bytes_avoided_vs_A"] != ba or x["budget_violations"] != 0: p.append(f"EVAL row {n}")
        kk = k4_decide(ba)
        if k4["per_candidate"][n]["classification"] != kk["classification"] or abs(k4["per_candidate"][n]["gross_value_ms_per_token"] - kk["gross_value_ms_per_token"]) > 1e-9: p.append(f"K4 {n} does not recompute")
    kk5 = k5_decide(ev["rows"]["NUE_LO"]["miss_bytes_avoided_vs_A"], ev["rows"]["NUE_X"]["miss_bytes_avoided_vs_A"])
    if kk5["classification"] != k5["classification"]: p.append("K5 does not recompute")
    for i in DEV_IDS:
        fa = frozen_dev_a(i)
        if runs[i]["A"]["decode"]["miss_bytes"] != fa["miss_bytes"]: p.append(f"DEV A {i} differs from the frozen cell")
    if replay_too:
        r3 = _r3(); evs = r3.load_events()
        for n in ("NUE_X", "NUE_LO"):
            if r3.replay(evs, make_arm(f"{n}:{sel['selected_variant']}")) != {k: v for k, v in ev["raw"][n].items() if k != "arm"}: p.append(f"re-run of {n} differs")
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("args", nargs="*"); ap.add_argument("--replay", action="store_true"); a = ap.parse_args()
    if a.cmd == "synth":
        s = synthetic_cases(); (HERE / "SYNTHETIC_CASES.json").write_text(dumps(s)); print("synthetic all_pass", s["all_pass"]); sys.exit(0 if s["all_pass"] else 1)
    elif a.cmd == "devrun":
        r = devrun(a.args[0], a.args[1]); Path(a.args[2]).write_text(dumps(r)); print(a.args[0], a.args[1], r["decode"]["miss_bytes"])
    elif a.cmd == "select":
        print("selected", lock_selection(Path(a.args[0])))
    elif a.cmd == "evalrun":
        r = evalrun(a.args[0]); Path(a.args[1]).write_text(dumps(r)); print(r["arm"], r["decode"]["misses"], r["decode"]["miss_bytes"], r["budget_violations"])
    elif a.cmd == "build":
        print("freeze", build(Path(a.args[0]), Path(a.args[1])))
    elif a.cmd == "verify":
        pr = verify(a.replay); print("VERIFY", "OK" if not pr else pr); sys.exit(1 if pr else 0)
