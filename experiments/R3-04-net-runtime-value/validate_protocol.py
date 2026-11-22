"""R3-04 protocol validator and reference estimators (stdlib only; no model, no timing, no network).

Single source of truth for: the three-arm schedule, order balance, the primary/secondary estimators, the frozen classification
rule, sample-size arithmetic and machine-time estimates. tools/r3_04_acquire.py imports this module; nothing here reads R3-04 data.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import statistics as st
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

# ------------------------------------------------------------------ frozen constants
RUNTIME_SHA256 = "8d2e1def2bd46f9f395a9e5662bd04cc8eafad6c77a337de2abcff1f82685ba5"   # R3-03B runtime, reused unchanged
TOKEN_DIGEST = "ac28efbe13c80f363f508a6f19d4b1d3af189eb26d6e9c81309376033075cdfa"
EVENTS = 24672                       # 514 passes x 48 layers
N_PREDICT = 512
VICTIM_CALLS_NOT_FIXED = True        # victim_calls depends on budget/policy; it is a mechanism integrity value, not a constant
ARMS = {"A": "b_selected", "S": "predicted_protect_sham", "P": "predicted_protect"}
PERMS = ["ASP", "APS", "SAP", "SPA", "PAS", "PSA"]
GIB = 1024 ** 3
BUDGET_ORDER = ["1.5", "4", "8"]
BUDGETS = {
    "1.5": {"bytes": int(1.5 * GIB), "role": "PRIMARY", "n_blocks": 18, "pair": {"decay_factor": 0.999, "aging_interval_steps": 1}},
    "4": {"bytes": 4 * GIB, "role": "CONFIRMATORY_DESCRIPTIVE", "n_blocks": 6, "pair": {"decay_factor": 0.99, "aging_interval_steps": 64}},
    "8": {"bytes": 8 * GIB, "role": "NEGATIVE_CONTROL_DESCRIPTIVE", "n_blocks": 6, "pair": {"decay_factor": 0.999, "aging_interval_steps": 16}},
}
PREDICTOR = {"predictor_decay": 0.99, "top_k": 8, "n_experts": 128}
PRIMARY_BUDGET = "1.5"
WARMUPS_PER_BUDGET = 1
COOLDOWN_WITHIN_BLOCK_S = 15
COOLDOWN_BETWEEN_BLOCKS_S = 60
DELTA_COST_REF_MS = 17.466373388671876   # R3-03B corrected sham overhead at 8 GiB (planning scale only, never an R3-04 observation)
PRECISION_FRACTION = 1.0 / 3.0           # primary precision criterion: CI half-width <= DELTA_COST_REF / 3
ALPHA = 0.05
PREFILL_PASSES = 2


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_file(p: Path) -> str:
    return sha_bytes(Path(p).read_bytes())


def sha_lf(p: Path) -> str:
    return sha_bytes(Path(p).read_bytes().replace(b"\r\n", b"\n"))


def freeze_sha(obj: Dict[str, Any]) -> str:
    o = {k: v for k, v in obj.items() if k != "freeze_sha256"}
    return sha_bytes(json.dumps(o, sort_keys=True, separators=(",", ":")).encode())


# ------------------------------------------------------------------ configurations
def config(budget: str, arm: str) -> Dict[str, Any]:
    b = BUDGETS[budget]
    params = dict(b["pair"])
    if arm in ("S", "P"):
        params.update(PREDICTOR)
    return {"config_id": f"{arm}-{budget}GiB", "arm": arm, "policy_id": ARMS[arm], "budget_label": budget, "budget_bytes": b["bytes"], "params": params}


# ------------------------------------------------------------------ schedule
def block_order(block: int) -> str:
    p = PERMS[(block - 1) % 6]
    return p + p[::-1]


def timing_schedule() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for budget in BUDGET_ORDER:
        n = BUDGETS[budget]["n_blocks"]
        for w in range(WARMUPS_PER_BUDGET):
            arm = block_order(1)[0]
            out.append({"seq": len(out) + 1, "budget": budget, "kind": "warmup", "block": 0, "position": w + 1, "arm": arm, "policy_id": ARMS[arm],
                        "config_id": f"{arm}-{budget}GiB", "cooldown_before_s": COOLDOWN_BETWEEN_BLOCKS_S})
        for b in range(1, n + 1):
            for pos, arm in enumerate(block_order(b), start=1):
                out.append({"seq": len(out) + 1, "budget": budget, "kind": "counted", "block": b, "position": pos, "arm": arm, "policy_id": ARMS[arm],
                            "config_id": f"{arm}-{budget}GiB", "cooldown_before_s": COOLDOWN_BETWEEN_BLOCKS_S if pos == 1 else COOLDOWN_WITHIN_BLOCK_S})
    return out


def mechanism_schedule() -> List[Dict[str, Any]]:
    """Dedicated trace-enabled mechanism / conformance launches (separate from timing; no timing claim)."""
    out = []
    for budget in BUDGET_ORDER:
        for arm in "ASP":
            out.append({"mseq": len(out) + 1, "budget": budget, "arm": arm, "policy_id": ARMS[arm], "config_id": f"{arm}-{budget}GiB", "policy_timing": "ON"})
        if budget == PRIMARY_BUDGET:
            out.append({"mseq": len(out) + 1, "budget": budget, "arm": "P", "policy_id": ARMS["P"], "config_id": f"P-{budget}GiB", "policy_timing": "OFF"})
    return out


def schedule_sha(s: List[Dict[str, Any]]) -> str:
    return sha_bytes(json.dumps(s, sort_keys=True, separators=(",", ":")).encode())


def balance_report(s: List[Dict[str, Any]]) -> Dict[str, Any]:
    rep: Dict[str, Any] = {}
    for budget in BUDGET_ORDER:
        c = [x for x in s if x["budget"] == budget and x["kind"] == "counted"]
        n = BUDGETS[budget]["n_blocks"]
        per_arm = {a: sum(1 for x in c if x["arm"] == a) for a in "ASP"}
        mean_pos = {a: st.fmean(x["position"] for x in c if x["arm"] == a) for a in "ASP"}
        first = {a: sum(1 for x in c if x["position"] == 1 and x["arm"] == a) for a in "ASP"}
        perm_counts = {p: sum(1 for b in range(1, n + 1) if PERMS[(b - 1) % 6] == p) for p in PERMS}
        within_block_mean_pos = all(abs(st.fmean(x["position"] for x in c if x["block"] == b and x["arm"] == a) - 3.5) < 1e-12 for b in range(1, n + 1) for a in "ASP")
        rep[budget] = {"counted": len(c), "per_arm": per_arm, "mean_position": mean_pos, "first_position_counts": first, "permutation_counts": perm_counts,
                       "every_block_has_each_arm_twice": all(sum(1 for x in c if x["block"] == b and x["arm"] == a) == 2 for b in range(1, n + 1) for a in "ASP"),
                       "within_block_mean_position_equal_3_5": within_block_mean_pos, "n_blocks_multiple_of_6": n % 6 == 0}
    return rep


# ------------------------------------------------------------------ statistics (t quantile / Fieller from the frozen R3-03B final validator, unmodified)
def _r3_03b():
    p = REPO / "experiments" / "R3-03B-final-evidence" / "validate_final.py"
    spec = importlib.util.spec_from_file_location("_r3_03b_final_validate", p)
    m = importlib.util.module_from_spec(spec)           # type: ignore[arg-type]
    spec.loader.exec_module(m)                           # type: ignore[union-attr]
    return m


_M = None


def t975(df: int) -> float:
    global _M
    _M = _M or _r3_03b()
    return _M.t975(df)


def fieller(o: List[float], p: List[float]) -> Dict[str, Any]:
    global _M
    _M = _M or _r3_03b()
    return _M.fieller(o, p)


def wall_ms_per_token(rec: Dict[str, Any]) -> float:
    return float(rec["predicted_ms"]) / float(rec["predicted_n"])


def policy_ms_per_token(rec: Dict[str, Any]) -> float:
    return float(rec["policy_timing"]["ns_total"]) / N_PREDICT / 1e6


def tps(rec: Dict[str, Any]) -> float:
    return 1000.0 / wall_ms_per_token(rec)


def block_means(recs: List[Dict[str, Any]], metric) -> Dict[int, Dict[str, float]]:
    by: Dict[int, Dict[str, List[float]]] = {}
    for r in recs:
        if r.get("kind", "counted") != "counted":
            continue                                                  # warmups never enter any estimator
        by.setdefault(r["block"], {"A": [], "S": [], "P": []})[r["arm"]].append(metric(r))
    out = {}
    for b, d in sorted(by.items()):
        if any(len(d[a]) != 2 for a in "ASP"):
            raise ValueError(f"block {b} is not a complete 2-2-2 block")
        out[b] = {a: st.fmean(d[a]) for a in "ASP"}
    return out


def contrasts(recs: List[Dict[str, Any]], metric) -> Dict[str, List[float]]:
    bm = block_means(recs, metric)
    return {"blocks": list(bm), "DELTA_COST": [v["S"] - v["A"] for v in bm.values()], "DELTA_RETENTION": [v["P"] - v["S"] for v in bm.values()],
            "DELTA_NET": [v["P"] - v["A"] for v in bm.values()]}


def t_interval(x: List[float]) -> Dict[str, float]:
    n = len(x)
    if n < 2:
        raise ValueError("need >= 2 blocks")
    m, s = st.fmean(x), st.stdev(x)
    hw = t975(n - 1) * s / math.sqrt(n)
    return {"n_blocks": n, "mean": m, "sd": s, "se": s / math.sqrt(n), "t": t975(n - 1), "half_width": hw, "lo": m - hw, "hi": m + hw}


def precision_target_ms() -> float:
    return DELTA_COST_REF_MS * PRECISION_FRACTION


def classify_primary(delta_net_wall_ms: List[float], delta_ret_adj_wall_ms: Optional[List[float]] = None, retention_improved_in_mechanism: Optional[bool] = None) -> Dict[str, Any]:
    """Frozen rule on the PRIMARY budget only. Wall ms/token: negative DELTA_NET = P faster than A."""
    ci = t_interval(delta_net_wall_ms)
    if ci["hi"] < 0:
        net = "NET_WIN"
    elif ci["lo"] > 0:
        net = "NET_LOSS"
    else:
        net = "INCONCLUSIVE"
    adequate = ci["half_width"] <= precision_target_ms()
    if net == "NET_WIN":
        claim = "Q4_NET_RUNTIME_WIN"
    elif net == "INCONCLUSIVE":
        claim = "Q3_APPROXIMATE_BREAK_EVEN_BOUNDED" if adequate else "Q5_NO_RELIABLE_CONCLUSION"
    else:
        claim = "Q1_NO_COST_RECOVERY_RESOLVED"
        if delta_ret_adj_wall_ms is not None and len(delta_ret_adj_wall_ms) >= 2 and t_interval(delta_ret_adj_wall_ms)["hi"] < 0:
            claim = "Q2_PARTIAL_COST_RECOVERY_STILL_SLOWER"
    return {"net_classification": net, "claim_class": claim, "delta_net_ci": ci, "precision_target_ms": precision_target_ms(), "precision_adequate": adequate,
            "retention_improved_in_mechanism": retention_improved_in_mechanism}


# ------------------------------------------------------------------ sample size / power
def _lgamma(x):
    return math.lgamma(x)


def power_one_sided(effect: float, sd: float, n: int) -> float:
    """P(t-based CI excludes 0 on the correct side) for a true mean effect, via exact noncentral-t integration (Simpson, deterministic)."""
    df = n - 1
    nc = effect / (sd / math.sqrt(n))
    tc = t975(df)
    lo, hi, m = 1e-9, 6.0, 4000
    h = (hi - lo) / m
    logc = (df / 2) * math.log(df) - ((df / 2 - 1) * math.log(2) + _lgamma(df / 2))

    def f(s):
        pdf = math.exp(logc + (df - 1) * math.log(s) - df * s * s / 2)
        return pdf * 0.5 * math.erfc((tc * s - nc) / math.sqrt(2))
    tot = f(lo) + f(hi) + sum((4 if i % 2 else 2) * f(lo + i * h) for i in range(1, m))
    return tot * h / 3


def smallest_n_for_halfwidth(sd: float, target: float, step: int = 6, nmax: int = 60) -> Optional[int]:
    for n in range(step, nmax + 1, step):
        if t975(n - 1) * sd / math.sqrt(n) <= target:
            return n
    return None


def launch_seconds(budget: str, arm: str, wall_ms: Dict[str, float], shutdown: str) -> float:
    """Planning model of one counted launch (seconds, no cooldown): ready 10 s + prefill + 512 decode + shutdown; 'current' applies the observed 300 s uptime floor."""
    prefill = 10.7 * wall_ms[budget] / wall_ms["8"]
    model = 10.0 + prefill + 512 * (wall_ms[budget] + (DELTA_COST_REF_MS if arm in ("S", "P") else 0.0)) / 1000.0
    if shutdown == "fixed":
        return model + 10.0
    return max(model + 10.0, 300.0)


def hours_estimate(wall_ms: Dict[str, float]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    s = timing_schedule()
    for mode in ("current", "fixed"):
        per: Dict[str, float] = {}
        for budget in BUDGET_ORDER:
            sec = 0.0
            for x in s:
                if x["budget"] == budget:
                    sec += launch_seconds(budget, x["arm"], wall_ms, mode) + x["cooldown_before_s"]
            per[budget] = sec / 3600.0
        # mechanism launches: traced decode ~5.05x slower (R3-00A: 0.83 vs 4.19 tok/s), same startup/shutdown model, 300 s floor in 'current'
        msec = 0.0
        for m in mechanism_schedule():
            model = 10.0 + 10.7 * wall_ms[m["budget"]] / wall_ms["8"] * 5.05 + 512 * (wall_ms[m["budget"]] + (DELTA_COST_REF_MS if m["arm"] in ("S", "P") else 0.0)) / 1000.0 * 5.05
            msec += (model + 10.0) if mode == "fixed" else max(model + 10.0, 300.0)
            msec += COOLDOWN_BETWEEN_BLOCKS_S
        out[mode] = {"timing_hours_per_budget": per, "timing_hours_total": sum(per.values()), "mechanism_hours": msec / 3600.0,
                     "model_bearing_hours_total": sum(per.values()) + msec / 3600.0}
    return out


def verify_freeze(path: Path) -> Dict[str, Any]:
    f = json.loads(Path(path).read_text())
    return {"freeze_sha256_matches": f.get("freeze_sha256") == freeze_sha(f)}
