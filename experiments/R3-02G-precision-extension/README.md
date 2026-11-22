# R3-02G — 4 GiB Precision Extension (feasibility + preregistration)

R3-02 remains permanently **MECHANISM_INSUFFICIENT**. This is a separate, additive
confirmatory extension: **R3-02G-PRECISION-EXTENSION**.

## Feasibility

Pilot 4-GiB paired differences: [0.08606, 0.02527, 0.06675, 0.05439, 0.06891] (median 0.066749,
CI half-width 0.030396, threshold 0.002665). Normal-model Monte Carlo from the pilot
(SD 0.02259, 1,000 experiments × 400 bootstrap resamples, seed 20261116):

- N=300 → P(pass) 0.158
- N=400 → 0.463
- N=500 → 0.724
- **N=600 → 0.907** (chosen)
- N=750 → 0.987

Sensitivity with SD ×1.3: P(pass) at N=600 is 0.298 — disclosed risk.

## Design

TIMING-only, extension-only (the five pilot pairs are not reused), 600 fresh pairs in
balanced ABBA blocks (A,B,B,A / B,A,A,B), two warmups, 15 s / 60 s cooldowns, the frozen
endpoint and the frozen median + 10,000-draw bootstrap percentile CI.

## Cost

1,202 model-bearing launches; ~64 h expected, ~80 h conservative;
~10 MB disk (no traces); 4-GiB configuration needs ~6.55 GiB available RAM.

## Rules

Fixed N=600; no optional stopping; a failure is final for this extension; a pass allows a
successor classification and only then the cost-model gate.
