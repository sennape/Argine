"""Research 5 Phase 2 -- the frozen statistical analysis and classification (R5-00 ENDPOINTS.json, DECISION_RULES.json).

Pure functions over launch records (and, for G4, per-launch mechanism metrics computed from the kept traces). Nothing
here reads a file, launches anything or can be called before all counted launches exist: campaign.py calls classify()
exactly once (one-shot), or terminal() for an aborted or blocked campaign.

Frozen definitions implemented (verbatim texts are quoted in PROTOCOL_2.json):
  primary   r_b = (mean_A_b - mean_C_b) / mean_A_b over the two launches of each arm in block b, decode ms/token =
            predicted_ms / predicted_n (512); aggregate = mean of r_b over the 20 blocks at 1.5 GiB; two-sided 95%
            Student t interval, df = 19.
  G3        lower bound of that interval >= 0.02.
  G5        4 GiB: upper bound of the two-sided 95% t interval (df = 19) of the mean of -r_b <= 0.02.
  G6        at least 3 of the 4 evaluation prompts have a positive mean r_b at 1.5 GiB (point estimates).
  G4        mechanism campaign, 12 blocks, df = 11: (a) upper bound of the two-sided 95% t interval of the mean block
            difference exposed_wait(C_I) - exposed_wait(C0_I) < 0; (b) mean over the 12 C_I launches of
            hidden_deferred_bytes_fraction >= 0.25; (c) mean[exposed_wait(C0_I) - exposed_wait(C_I)] >=
            0.5 x mean[exposed_store(B) - exposed_store(C_I)] whenever the latter is positive (else (c) holds).
  G7        mechanism (C_I vs B) and both timing campaigns (C vs A): |mean(x) - mean(ref)| / mean(ref) <= 0.01 for miss
            bytes per token (end-of-run bytes_read / n_tokens) and evictions per token; budget violations 0 everywhere.
  INTEGRITY every counted launch valid (campaign.launch_problems) and, per prompt, one token digest across every arm,
            budget and campaign.
  order     DECISION_RULES classification_order, first match wins.
Traced (B, C0_I, C_I) and untraced (A, C) launches are never mixed: G3, G5, G6 use only A and C; G4 only the traced arms.
"""
from __future__ import annotations

import math
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MEI = 0.02
NO_HARM = 0.02
HIDDEN_FLOOR = 0.25
ATTRIBUTION = 0.5
PURITY = 0.01
CONSISTENCY_MIN_PROMPTS = 3
TIMING_ARMS = {"A", "C"}
MECH_ARMS = {"B", "C0_I", "C_I"}
CORRECTNESS_ARMS = {"C", "C_I", "C0_I"}
LABELS = ["R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED", "R5_NO_MEANINGFUL_OVERLAP_HEADROOM", "R5_INVALID", "R5_CONFOUNDED_NOT_PURE_SCHEDULING",
          "R5_POSITIVE_RUNTIME_IMPROVEMENT", "R5_PRIMARY_GAIN_NOT_ROBUST", "R5_RUNTIME_CHANGE_WITHOUT_MECHANISM",
          "R5_MECHANISM_PRESENT_BUT_NO_NET_RUNTIME_GAIN", "R5_NO_REALISED_OVERLAP_NO_GAIN"]


# ------------------------------------------------------------------------------------------------ exact decisions
# Gate decisions are made in exact rational arithmetic on the logged values (each float taken exactly), so that they do
# not depend on the Python build (float summation changed in 3.12) or on libm. The t critical values are fixed constants:
# df 19 is the frozen DECISION_RULES precision_plan value; df 11 is the two-sided 95% value computed by t_quantile (checked against scipy and
# t_quantile in the tests). Intervals are also reported in floating point, for reading only.
T975 = {19: Fraction(2.093024054408263), 11: Fraction(2.200985160091639)}


def F(x) -> Fraction:
    """A measured value, exactly as logged (a float is taken bit-exactly)."""
    return x if isinstance(x, Fraction) else Fraction(x)


def TH(x: float) -> Fraction:
    """A frozen threshold: the decimal number written in DECISION_RULES (0.02 -> 1/50, not the nearest binary float)."""
    return Fraction(repr(x))


def _mv(xs: Sequence[Fraction]) -> Tuple[Fraction, Fraction, int]:
    n = len(xs)
    m = sum(xs, Fraction(0)) / n
    v = sum(((x - m) ** 2 for x in xs), Fraction(0)) / (n - 1)
    return m, v, n


def lower_ge(xs: Sequence[Fraction], thr: Fraction) -> bool:
    """mean - t*sqrt(v/n) >= thr, exactly."""
    m, v, n = _mv(xs); t = T975[n - 1]; d = m - thr
    return d >= 0 and d * d >= t * t * v / n


def upper_le(xs: Sequence[Fraction], thr: Fraction) -> bool:
    """mean + t*sqrt(v/n) <= thr, exactly."""
    m, v, n = _mv(xs); t = T975[n - 1]; d = thr - m
    return d >= 0 and d * d >= t * t * v / n


def upper_lt(xs: Sequence[Fraction], thr: Fraction) -> bool:
    """mean + t*sqrt(v/n) < thr, exactly."""
    m, v, n = _mv(xs); t = T975[n - 1]; d = thr - m
    return d > 0 and d * d > t * t * v / n


# ------------------------------------------------------------------------------------------------ Student t
def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta function (modified Lentz)."""
    tiny, eps = 1e-300, 1e-15
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d; d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c; c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d; d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c; c = c if abs(c) > tiny else tiny
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            return h
    raise ArithmeticError("incomplete beta did not converge")


def _ibeta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lb = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lb) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lb) * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: int) -> float:
    x = df / (df + t * t)
    tail = 0.5 * _ibeta(df / 2.0, 0.5, x)
    return 1.0 - tail if t >= 0 else tail


def t_quantile(p: float, df: int) -> float:
    lo, hi = -50.0, 50.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def interval95(xs: Sequence[float]) -> Dict[str, float]:
    """Two-sided 95% Student t interval of the mean (df = n - 1), for REPORTING (decisions use the exact functions)."""
    n = len(xs)
    if n < 2:
        raise ValueError("an interval needs at least 2 blocks")
    m_, v_, _ = _mv([F(x) for x in xs])
    m = float(m_)
    sd = math.sqrt(float(v_))
    t = float(T975[n - 1]) if (n - 1) in T975 else t_quantile(0.975, n - 1)
    half = t * sd / math.sqrt(n)
    return {"n": n, "df": n - 1, "mean": m, "sd": sd, "t_975": t, "half_width": half, "lower": m - half, "upper": m + half}


# ------------------------------------------------------------------------------------------------ helpers
def ms_per_token(rec: Dict[str, Any]) -> Fraction:
    t = rec["server_timings"]
    if t.get("predicted_n") != 512 or not (t.get("predicted_ms") or 0) > 0:
        raise ValueError(f"launch {rec['seq']}: invalid server timings")
    return F(t["predicted_ms"]) / t["predicted_n"]


def _blocks(recs: Iterable[Dict[str, Any]], campaign: str) -> Dict[int, List[Dict[str, Any]]]:
    out: Dict[int, List[Dict[str, Any]]] = {}
    for r in recs:
        if r["campaign"] == campaign:
            out.setdefault(r["block"], []).append(r)
    return {b: sorted(v, key=lambda r: r["pos"]) for b, v in sorted(out.items())}


def _mean(xs: Sequence) -> Fraction:
    return sum((F(x) for x in xs), Fraction(0)) / len(xs)


def block_r(block: List[Dict[str, Any]]) -> Fraction:
    a = [ms_per_token(r) for r in block if r["arm"] == "A"]
    c = [ms_per_token(r) for r in block if r["arm"] == "C"]
    if len(a) != 2 or len(c) != 2 or any(r["traced"] for r in block):
        raise ValueError("a timing block has exactly two untraced A and two untraced C launches")
    return (_mean(a) - _mean(c)) / _mean(a)


def timing(recs: List[Dict[str, Any]], campaign: str) -> Dict[str, Any]:
    bl = _blocks(recs, campaign)
    if len(bl) != 20:
        raise ValueError(f"{campaign}: {len(bl)} blocks, expected 20")
    rb = {b: block_r(v) for b, v in bl.items()}
    per_prompt: Dict[str, List[float]] = {}
    for b, v in bl.items():
        per_prompt.setdefault(v[0]["prompt"], []).append(rb[b])
    return {"r_b": rb, "interval": interval95(list(rb.values())), "per_prompt_mean_r": {p: _mean(x) for p, x in sorted(per_prompt.items())}}


def floats(d: Dict[Any, Any]) -> Dict[Any, float]:
    return {k: float(v) for k, v in d.items()}


def purity(recs: List[Dict[str, Any]], campaign: str, arm: str, ref: str) -> Dict[str, Any]:
    def per_tok(arm_: str, key: str) -> float:
        xs = [Fraction(r["r5_counters"][key], r["n_tokens"]) for r in recs if r["campaign"] == campaign and r["arm"] == arm_]
        if not xs:
            raise ValueError(f"{campaign}: no {arm_} launch")
        return _mean(xs)
    out: Dict[str, Any] = {}
    for key, name in (("bytes_read", "miss_bytes_per_token"), ("evictions", "evictions_per_token")):
        x, y = per_tok(arm, key), per_tok(ref, key)
        within = (abs(x - y) <= TH(PURITY) * y) if y else (x == 0)
        out[name] = {arm: float(x), ref: float(y), "relative_difference": float(abs(x - y) / y) if y else (0.0 if x == 0 else math.inf),
                     "within": within}
    return out


def exact_ms(m: Dict[str, Any], what: str) -> Fraction:
    """Exact ms per decode token from the integer nanosecond sums when present (mechanism.as_dict), else the given value."""
    if f"exposed_{what}_ns" in m and m.get("decode_tokens"):
        return Fraction(m[f"exposed_{what}_ns"], m["decode_tokens"] * 1_000_000)
    return F(m[f"exposed_{what}_ms_per_token"])


def exact_hidden(m: Dict[str, Any]) -> Fraction:
    if "deferred_bytes" in m:
        return Fraction(m["hidden_deferred_bytes"], m["deferred_bytes"]) if m["deferred_bytes"] else Fraction(0)
    return F(m["hidden_deferred_bytes_fraction"])


def mechanism(recs: List[Dict[str, Any]], mech: Dict[int, Dict[str, float]]) -> Dict[str, Any]:
    """G4 from the 12 mechanism blocks; mech[seq] = per-launch metrics (mechanism.LaunchMechanism.as_dict())."""
    bl = _blocks(recs, "mechanism_1.5GiB")
    if len(bl) != 12:
        raise ValueError(f"mechanism: {len(bl)} blocks, expected 12")
    d, gain_wait, gain_store, hidden = [], [], [], []
    for b, v in bl.items():
        arm = {r["arm"]: r for r in v}
        if sorted(arm) != sorted(MECH_ARMS) or len(v) != 3 or not all(r["traced"] for r in v):
            raise ValueError(f"mechanism block {b} does not hold one traced launch of B, C0_I and C_I")
        ew = {a: exact_ms(mech[arm[a]["seq"]], "wait") for a in MECH_ARMS}
        es = {a: exact_ms(mech[arm[a]["seq"]], "store") for a in MECH_ARMS}
        d.append(ew["C_I"] - ew["C0_I"])
        gain_wait.append(ew["C0_I"] - ew["C_I"])
        gain_store.append(es["B"] - es["C_I"])
        hidden.append(exact_hidden(mech[arm["C_I"]["seq"]]))
    iv = interval95(d)
    mw, ms_ = _mean(gain_wait), _mean(gain_store)
    a_ok = upper_lt(d, Fraction(0))
    b_ok = _mean(hidden) >= TH(HIDDEN_FLOOR)
    c_ok = (mw >= TH(ATTRIBUTION) * ms_) if ms_ > 0 else True
    return {"a_wait_difference_interval": iv, "a": a_ok, "b_mean_hidden_deferred_bytes_fraction": float(_mean(hidden)), "b": b_ok,
            "c_mean_wait_reduction_ms": float(mw), "c_mean_store_reduction_ms": float(ms_), "c_applies": ms_ > 0, "c": c_ok,
            "c_status": "tested" if ms_ > 0 else "not applicable (store time not reduced): holds by the frozen text 'whenever the latter is positive'",
            "bookkeeping_effect_ms_per_token_descriptive": float(_mean([exact_ms(mech[{r["arm"]: r for r in v}["C0_I"]["seq"]], "store")
                                                                        - exact_ms(mech[{r["arm"]: r for r in v}["B"]["seq"]], "store")
                                                                        for v in bl.values()])),
            "G4": a_ok and b_ok and c_ok}


def token_digest_check(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per prompt: the reference is the digest of the A and B launches. A/B disagreement = integrity failure (campaign);
    a C, C_I or C0_I launch differing from a consistent A/B reference = correctness failure (intervention)."""
    ab_bad, c_bad = [], []
    for p in sorted({r["prompt"] for r in recs}):
        ref = {r["token_digest"] for r in recs if r["prompt"] == p and r["arm"] in ("A", "B")}
        if len(ref) != 1 or None in ref:
            ab_bad.append(p)
            continue
        c_bad += [r["seq"] for r in recs if r["prompt"] == p and r["arm"] in CORRECTNESS_ARMS and r["token_digest"] not in ref]
    return {"ab_inconsistent_prompts": ab_bad, "c_correctness_failures": sorted(c_bad)}


def classify_order(g: Dict[str, bool], blocked: bool, invalid: bool) -> str:
    if blocked:
        return LABELS[0]
    if invalid:
        return LABELS[2]
    if not g["G7"]:
        return LABELS[3]
    G3, G4, G5, G6 = g["G3"], g["G4"], g["G5"], g["G6"]
    if G3 and G4 and G5 and G6:
        return LABELS[4]
    if G3 and G4:
        return LABELS[5]
    if G3:
        return LABELS[6]
    if G4:
        return LABELS[7]
    return LABELS[8]


def terminal(reason: str, correctness: bool) -> Dict[str, Any]:
    """An aborted campaign or a correctness failure of C/C_I/C0_I: the label is fixed by the precedence; no endpoint is computed."""
    label = LABELS[0] if correctness else LABELS[2]
    return {"classification": label, "endpoints_computed": False, "reason": reason,
            "rule": "DECISION_RULES classification_order rows 1 and 3; blame_rules terminal: R5_INVALID is final"}


def classify(recs: List[Dict[str, Any]], mech: Dict[int, Dict[str, float]], expected_counted: int = 196) -> Dict[str, Any]:
    """The one-shot Phase 2 result. recs: the 196 valid counted launch records (one per slot)."""
    if len(recs) != expected_counted or len({r["seq"] for r in recs}) != expected_counted:
        raise ValueError(f"{len(recs)} counted launches, expected {expected_counted}: no partial analysis")
    tok = token_digest_check(recs)
    violations = [r["seq"] for r in recs if r["r5_counters"].get("violations") != 0]
    blocked = bool(tok["c_correctness_failures"]) or any(r["arm"] in CORRECTNESS_ARMS for r in recs if r["seq"] in violations)
    invalid = bool(tok["ab_inconsistent_prompts"]) or any(r["arm"] in ("A", "B") for r in recs if r["seq"] in violations)
    if blocked or invalid:
        return {**terminal("token digest or budget-violation failure in the counted launches", blocked), "token_digests": tok,
                "budget_violation_launches": violations}
    t15, t4 = timing(recs, "timing_1.5GiB"), timing(recs, "timing_4GiB")
    mechs = mechanism(recs, mech)
    pur = {"mechanism_C_I_vs_B": purity(recs, "mechanism_1.5GiB", "C_I", "B"),
           "timing_1.5GiB_C_vs_A": purity(recs, "timing_1.5GiB", "C", "A"),
           "timing_4GiB_C_vs_A": purity(recs, "timing_4GiB", "C", "A")}
    g7 = all(v["within"] for p in pur.values() for v in p.values()) and not violations
    s4 = [-x for x in t4["r_b"].values()]
    noharm = interval95(s4)
    positive = sum(1 for v in t15["per_prompt_mean_r"].values() if v > 0)
    gates = {"G3": lower_ge(list(t15["r_b"].values()), TH(MEI)), "G4": mechs["G4"], "G5": upper_le(s4, TH(NO_HARM)),
             "G6": positive >= CONSISTENCY_MIN_PROMPTS, "G7": g7}
    label = classify_order(gates, False, False)
    return {"classification": label, "endpoints_computed": True, "gates": gates,
            "primary_1.5GiB": {"aggregate_mean_r": t15["interval"]["mean"], "interval95": t15["interval"], "r_b": floats(t15["r_b"]),
                               "aggregate_mean_r_exact": str(_mean(list(t15["r_b"].values())))},
            "no_harm_4GiB": {"mean_slowdown": noharm["mean"], "interval95": noharm, "r_b": floats(t4["r_b"])},
            "consistency_1.5GiB": {"per_prompt_mean_r": floats(t15["per_prompt_mean_r"]), "positive_prompts": positive},
            "decision_arithmetic": "exact rational arithmetic on the logged values; t(0.975, 19) = 2.093024054408263 (frozen precision_plan), "
                                   "t(0.975, 11) = 2.200985160091639",
            "mechanism": mechs, "purity": pur, "budget_violation_launches": violations, "token_digests": tok,
            "improvement": label == LABELS[4],
            "anti_rescue": "only R5_POSITIVE_RUNTIME_IMPROVEMENT is an improvement; every other label is a negative or uninterpretable "
                           "outcome; no secondary endpoint, subset, prompt or budget can upgrade it",
            "descriptive": descriptive(recs, mech)}


def descriptive(recs: List[Dict[str, Any]], mech: Dict[int, Dict[str, float]]) -> Dict[str, Any]:
    """ENDPOINTS secondary and descriptive quantities; never used to classify."""
    out: Dict[str, Any] = {}
    for c in ("timing_1.5GiB", "timing_4GiB"):
        for a in ("A", "C"):
            rs = [r for r in recs if r["campaign"] == c and r["arm"] == a]
            out[f"{c}_{a}"] = {"decode_ms_per_token_mean": float(_mean([ms_per_token(r) for r in rs])),
                               "tokens_per_second_mean": float(_mean([1000 / ms_per_token(r) for r in rs])),
                               "prefill_ms_mean": float(_mean([r["server_timings"].get("prompt_ms", 0.0) for r in rs]))}
    for a in ("B", "C0_I", "C_I"):
        rs = [r for r in recs if r["campaign"] == "mechanism_1.5GiB" and r["arm"] == a]
        out[f"mechanism_{a}"] = {k: float(_mean([mech[r["seq"]][k] for r in rs])) for k in
                                 ("exposed_wait_ms_per_token", "exposed_store_ms_per_token", "deferred_read_bytes_per_token",
                                  "hidden_deferred_bytes_fraction")}
        out[f"mechanism_{a}"]["traced_decode_ms_per_token_mean"] = float(_mean([ms_per_token(r) for r in rs]))
        out[f"mechanism_{a}"]["non_hook_ms_per_token_mean"] = float(_mean([ms_per_token(r) - F(mech[r["seq"]]["exposed_store_ms_per_token"]) for r in rs]))
    out["note"] = "descriptive only; traced (mechanism) and untraced (timing) values are never compared with each other"
    return out
