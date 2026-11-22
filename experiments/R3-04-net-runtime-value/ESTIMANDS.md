# R3-04 estimands (frozen before any R3-04 launch)

Policies: **A** = `b_selected`, **S** = `predicted_protect_sham` (full predictive bookkeeping, victims chosen exactly as A), **P** = `predicted_protect`.
Per-budget parameters are the frozen R2 pairs (1.5 GiB: decay 0.999 / interval 1; 4 GiB: 0.99 / 64; 8 GiB: 0.999 / 16); S and P add predictor_decay 0.99, top_k 8, n_experts 128.

## Primary endpoint
`decode wall ms/token` = server `timings.predicted_ms / predicted_n` (n = 512). Lower is better. It is additive in cost, which is why the three contrasts add up exactly.
Secondary: `tokens/s = 1000 / (ms/token)` (per launch, then block means; every sign is reversed), per-arm `policy ms/token = policy_timing.ns_total / 512 / 1e6`, and the exact trace-derived mechanism metrics of the mechanism phase.
Wall time and policy time are different quantities and are never mixed: policy time is a measured component *inside* the wall time.

## Block structure and contrasts
A block has 6 launches: a permutation of {A,S,P} followed by its reverse (mean position 3.5 for every arm, so a linear drift cancels exactly inside a block). Block b uses permutation `PERMS[(b-1) mod 6]`. For each block, `m_X` is the mean of the 2 launches of arm X.

| estimand | per-block value | reading (wall ms/token) |
|---|---|---|
| DELTA_COST | m_S - m_A | predictive bookkeeping cost without retention benefit |
| DELTA_RETENTION | m_P - m_S | incremental effect of actually using predicted protection (bookkeeping largely shared) |
| DELTA_NET | m_P - m_A = DELTA_COST + DELTA_RETENTION | net value of the whole policy; **< 0 means P is faster than A** |

The same three contrasts are taken on `policy ms/token` (POL_COST, POL_RET, POL_NET). `DELTA_RETENTION_ADJ = DELTA_RETENTION(wall) - POL_RET` removes the measured extra bookkeeping of P over S.
`rho_cost = mean(DELTA_COST)/mean(POL_COST)` with a 95% Fieller interval (df = N - 1, block-paired means) continues R3-03B at every budget (descriptive).

## Decision rule (primary budget 1.5 GiB, DELTA_NET only, wall ms/token)
Two-sided 95% t interval over the 18 blocks (df = 17).
* **NET_WIN**: upper bound < 0. **NET_LOSS**: lower bound > 0. **INCONCLUSIVE**: interval contains 0.
* Precision flag: adequate iff half-width <= DELTA_COST_REF/3 = 5.822 ms/token (DELTA_COST_REF = 17.466 ms/token, the R3-03B measured bookkeeping cost, used only as a planning scale).
* No percentage threshold is invented: the sign of a confidence interval classifies; the cost scale only decides whether the precision was adequate.
* Claim classes: Q4 (NET_WIN); Q3 (INCONCLUSIVE and precision adequate: bounded approximate break-even); Q5 (INCONCLUSIVE and precision inadequate); Q2 (NET_LOSS and the CI of DELTA_RETENTION_ADJ entirely below 0: some cost recovered, still slower); Q1 (NET_LOSS otherwise: retention may improve but the cost is not recovered).
* 4 GiB and 8 GiB are descriptive: no label, never able to override the primary classification. tokens/s never classifies.

## What the mechanism phase adds
Exact decode miss bytes/token, hits, evictions per arm and budget (deterministic given greedy decoding and a deterministic policy). It answers "did P improve retention?" separately from "did P pay for itself?". The two must never be conflated.
