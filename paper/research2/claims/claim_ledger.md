# Research II-A Paper Claim Ledger

Status: **FROZEN** at the II-A paper tranche freeze. This ledger is the
authoritative record of every numerical or scientific claim made in
`paper/research2/manuscript.tex`. Every entry below carries the source
experiment, the canonical run ID, the source artifact, and the metric key
(or an explicit reason a claim is NOT YET SUPPORTED). Do not allow later
prose edits to the manuscript to add, strengthen, or silently drop a claim
without a corresponding update to this ledger.

Two states are used:
- **SUPPORTED** — directly read from a canonical, PASS-classified run's
  evidence artifact, as copied into `paper/research2/data/`.
- **NOT YET SUPPORTED** — explicitly disclaimed in the manuscript; listed
  here so a reviewer can confirm no later edit smuggled in a number for it.

## SUPPORTED claims

| # | Claim (as stated in the manuscript) | Experiment | Run ID | Artifact | Metric key |
|---|---|---|---|---|---|
| 1 | Runtime telemetry and StoreReplay agree exactly on this trace: event disagreement rate = 0.0, miss/eviction/miss-byte counts identical, 0 unexplained tail disagreements, EXP-021 closure = PASS. | R2-01 | `bb6d8ca34dbc4537a43d54be30c9f3e1` | `validation.json` | `event_disagreement_rate`, `wl_rp_misses`/`wl_rt_misses`, `wl_rp_evictions`/`wl_rt_evictions`, `wl_rp_miss_bytes`/`wl_rt_miss_bytes`, `tail_disagreements_unexplained`, `exp021_closure` |
| 2 | At the 4 GiB anchor budget, Oracle-Global-Belady achieves a relative miss-byte reduction of 0.5859 (58.59%) over Global-LRU. | R2-02 | `238c8ee7f0914df7b28942ec99627c24` | `comparison.json` | `oracle_gaps_by_baseline.Global-LRU.relative_miss_byte_reduction` |
| 3 | The structural sharding gap between Runtime-Sharded-LRU and Global-LRU at 4 GiB is small: 1,200,218,112 bytes, relative 0.0116 (1.16%). | R2-02 | `238c8ee7f0914df7b28942ec99627c24` | `comparison.json` | `sharding_decomposition.structural_sharding_gap.{miss_byte_reduction,relative}` |
| 4 | Full budget sweep (Global-LRU vs. Oracle-Global-Belady), relative miss-byte reduction: 1.5 GiB = 44.11%, 2 GiB = 47.55%, 4 GiB = 58.59%, 8 GiB = 70.66%, 16 GiB = 0.00%. | R2-03 | `d957077073374f1e9fd1e101a002946f` | `comparison.json` / `budget_results.csv` | `policy_information_headroom[*].relative_miss_byte_reduction` by `budget_bytes` |
| 5 | The 4 GiB sweep point exactly reproduces the R2-02 standalone result (58.59%), confirming cross-experiment consistency. | R2-02 + R2-03 | `238c8ee7f0914df7b28942ec99627c24`, `d957077073374f1e9fd1e101a002946f` | both `comparison.json` | cross-check of `relative_miss_byte_reduction` at `budget_bytes=4294967296` |
| 6 | At 16 GiB, Global-LRU and Oracle-Global-Belady produce identical miss counts, identical miss bytes, and zero evictions each (the first tested effective-residency point). | R2-03 | `d957077073374f1e9fd1e101a002946f` | `budget_results.json` | per-policy `misses`, `miss_bytes`, `evictions` at `budget_bytes=17179869184` |
| 7 | The relative information-headroom curve across the tested sweep is classified as collapse-at-effective-residency (non-monotonic rise then collapse), not monotonic. | R2-04 | `cd2b58b019314fafb90304234e6d1907` | `claims.json` | `supported_now[].basis = "curve_shape classification (section 12)"` |
| 8 | Maximum *relative* information headroom occurs at the 8 GiB tested point (70.66%). | R2-04 | `cd2b58b019314fafb90304234e6d1907` | `recoverable_margin.json` / `margin_by_budget.csv` | `policy_information_headroom` at `budget_bytes=8589934592` |
| 9 | Maximum *absolute* miss-byte headroom occurs at the 1.5 GiB tested point (103,574,900,736 bytes ≈ 103.57 GB), a different budget than the relative maximum. | R2-04 | `cd2b58b019314fafb90304234e6d1907` | `recoverable_margin.json` / `margin_by_budget.csv` | `policy_information_headroom` at `budget_bytes=1610612736`, absolute component |
| 10 | Per-budget headline pairs (relative %, absolute bytes): 1.5 GiB → 44.11%, 103,574,900,736 B; 2 GiB → 47.55%, 93,423,513,600 B; 4 GiB → 58.59%, 59,855,155,200 B; 8 GiB → 70.66%, 16,179,056,640 B; 16 GiB → 0.00%, 0 B. | R2-04 | `cd2b58b019314fafb90304234e6d1907` | `recoverable_margin.json` / `margin_by_budget.csv` | `policy_information_headroom[*]` by `budget_bytes` |
| 11 | 16 GiB is the first *tested* budget exhibiting policy convergence; this is an empirical observation over the tested grid, not a proven mathematical minimal-residency threshold. | R2-04 | `cd2b58b019314fafb90304234e6d1907` | `claims.json` | `supported_now[].basis = "effective_residency_budget (section 9)"` |
| 12 | The trace used across R2-01 through R2-04 is the single validated source trace, SHA-256 `e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1`. | R2-01..R2-04 | all four canonical runs | each `provenance.json` / `manifest.json` | trace hash field |

## NOT YET SUPPORTED claims (explicitly disclaimed in the manuscript)

| # | Disclaimed statement | Reason | Assigned to |
|---|---|---|---|
| 1 | "A causal predictor will recover X% of the Oracle headroom." | No causal predictor exists yet. | II-C (R2-09..R2-12) |
| 2 | "The Oracle gap itself is recoverable by a practical predictor." | The Oracle comparison establishes an informational upper bound only; whether it is causally exploitable is unanswered. | II-B (structure) / II-C (recovery) |
| 3 | Any "X% lower latency / X% faster inference / X% higher tokens/s / X% lower SSD traffic / X% real runtime improvement" claim. | R2-01..R2-04 are offline miss-cost analyses over replayed traces; miss bytes are a proxy for data that would need servicing, never a measured production performance number. | N/A — out of scope for this programme stage |
| 4 | Any specific numeric percentage for "causally recovered margin." | Causal recovery is NOT YET MEASURED. | II-C |
| 5 | `fraction_of_oracle_headroom_recovered = (B - P) / (B - O)` evaluated to any numeric value. | The metric is defined now (Eq. in Section 6) for future use; no causal predictor exists yet to evaluate `P`. | II-C |

## Governance

- Source of truth for this ledger: `paper/research2/data/r2_04/claims.json` (verbatim canonical claim classification from the R2-04 run), cross-checked against `data/r2_01/`, `data/r2_02/`, `data/r2_03/` artifacts for claims 1-6, 12 above which R2-04's own ledger does not cover.
- This ledger is part of the II-A paper freeze. Any future edit to the manuscript's numerical or scientific claims must first update this file in the same commit.
