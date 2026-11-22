"""R3-03B pre-acquisition block-count calculation (protocol equivalence_interval.precision_gate). Stdlib only.

N_req = min N such that N >= (t_{N-1,0.975} * sigma_c / (0.5 * P))^2, with
  P       = authoritative c_predictor_token as a fraction (%) of the measured b_selected step time (R3-02-p4 ANALYSIS.json timing.a),
  sigma_c = sqrt((CV_A^2 + CV_B^2) / 2) (%), ABBA-contrast SD from the protocol-cited between-launch CVs.
Compared with the protocol block caps (8 GiB <= 20, 4 GiB <= 10). Informational power table is NOT a decision rule.
"""
import json, math, statistics as st, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments/R3-03B-microbench-evidence"))
from validate_evidence import t975, t_cdf  # noqa: E402

P_TOKEN_MS = 5.936613728923476          # authoritative host c_predictor_token (mean), run2
CAPS = {"8GiB": 20, "4GiB": 10}
CV_PROTOCOL = {"8GiB": (3.8, 1.7), "4GiB": (6.9, 9.1)}      # protocol text: R3-02-p4 between-launch CV %, (A, B)
LAUNCH_SECONDS_BASIS = None


def n_req(p_pct, cv_a, cv_b, limit=5000):
    sig = math.sqrt((cv_a ** 2 + cv_b ** 2) / 2)
    for n in range(2, limit):
        if n >= (t975(n - 1) * sig / (0.5 * p_pct)) ** 2:
            return n, sig
    return None, sig


def power_normal(n, p_pct, sig, rho):
    """approx P(95% CI for rho inside [0.5,2.0]) given true rho; known sigma_c, P_b treated as exact (optimistic)."""
    se = sig / (math.sqrt(n) * p_pct); hw = t975(n - 1) * se
    lo, hi = 0.5 + hw, 2.0 - hw
    if lo >= hi:
        return 0.0
    Phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return Phi((hi - rho) / se) - Phi((lo - rho) / se)


def main():
    a = json.load(open(ROOT / "experiments/R3-02-phase2-mechanism/ANALYSIS.json"))["timing"]
    out = {"schema": "r3-03b-block-count-addendum-v1", "protocol_rule": "equivalence_interval.precision_gate", "p_token_ms": P_TOKEN_MS, "endpoints": {}}
    for key, g in (("8GiB", "8"), ("4GiB", "4")):
        A, B = a[g]["a"], a[g]["b"]
        step_ms = st.fmean(A) * 1000
        cvs_recomputed = (100 * st.stdev(A) / st.fmean(A), 100 * st.stdev(B) / st.fmean(B))
        p_pct = P_TOKEN_MS / step_ms * 100
        n, sig = n_req(p_pct, *CV_PROTOCOL[key])
        n_alt, sig_alt = n_req(p_pct, *cvs_recomputed)
        # step-time sensitivity: new runtime executes 48 instead of 144 b_selected calls/token (8.80 us each)
        step_new = step_ms - (144 - 48) * 8.802635e-3
        n_new, _ = n_req(P_TOKEN_MS / step_new * 100, *CV_PROTOCOL[key])
        cap = CAPS[key]
        e = {"historical_b_selected_step_ms": step_ms, "cv_protocol_pct": CV_PROTOCOL[key], "cv_recomputed_pct": cvs_recomputed,
             "P_pct_of_step": p_pct, "sigma_c_pct": sig, "half_target_pct": 0.5 * p_pct, "N_req": n, "cap": cap,
             "N_req_with_recomputed_cv": n_alt, "N_req_with_step_adjusted_for_48_calls": n_new, "step_ms_adjusted": step_new,
             "exceeds_cap": n is None or n > cap}
        if n is not None and n <= cap:
            e["fixed_blocks"] = n; e["physical_launches"] = 4 * n
            e["rho_ci_halfwidth_at_N"] = t975(n - 1) * sig / (math.sqrt(n) * p_pct)
            e["approx_power_explained_at_true_rho"] = {str(r): power_normal(n, p_pct, sig, r) for r in (0.75, 1.0, 1.25, 1.5)}
            e["approx_power_explained_at_cap_blocks"] = {str(r): power_normal(cap, p_pct, sig, r) for r in (0.75, 1.0, 1.25, 1.5)}
            e["classification"] = "FIXED_N_BY_RULE"
        else:
            e["classification"] = "UNDERPOWERED_FOR_EXPLAINED_VERDICT_DO_NOT_RUN_CAPPED_EXPERIMENT"
            e["rho_ci_halfwidth_at_cap"] = t975(cap - 1) * sig / (math.sqrt(cap) * p_pct)
            e["interval_width_available"] = 1.5
            e["capped_ci_width_vs_interval_width"] = 2 * e["rho_ci_halfwidth_at_cap"]
            e["blocks_required_for_rule"] = n
        out["endpoints"][key] = e
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
