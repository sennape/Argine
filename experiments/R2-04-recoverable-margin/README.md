# R2-04 -- Recoverable-Margin Analysis

**Phase:** II-A (final experiment)
**Depends on:** R2-01 (PASS), R2-02 (PASS), R2-03 (PASS)

## Scientific purpose

Quantify the upper-bound margin available to future causal retention/
prediction research, by decomposing R2-03's observed Oracle-vs-causal-
retention gap (across its fixed budget sweep) into a STRUCTURAL SHARDING
GAP and a POLICY-INFORMATION HEADROOM.

**Critical distinction (never collapsed):** the POLICY-INFORMATION
HEADROOM this experiment reports is a THEORETICAL/INFORMATIONAL upper
bound, not a DEMONSTRATED CAUSALLY RECOVERABLE performance figure. No
causal predictor exists yet (that is II-C's job -- R2-09..R2-12). The
"causally recovered margin" is explicitly reported as `NOT_YET_MEASURED`.

## Nature of this experiment

R2-04 is a pure offline SYNTHESIS over already-completed R2-01/R2-02/
R2-03 evidence. It performs **no new replay**, invokes **no model or
runtime**, and captures **no new trace** -- it reads R2-03's own
registered `budget_results.json` dynamically (never a hard-coded metric
value) and computes a deterministic decomposition/regime-classification
on top of it.

## Terminology contract

- **TOTAL OBSERVED UPPER-BOUND GAP** = Runtime-Sharded-LRU vs
  Oracle-Global-Belady (includes both structural and information
  differences).
- **STRUCTURAL SHARDING GAP** = Runtime-Sharded-LRU vs Global-LRU (both
  causal; the runtime's sharded-retention-structure effect under the
  existing offline model -- the offline ShardedLRU's shard mapping is
  statistical, not the runtime's exact address-derived one).
- **POLICY-INFORMATION HEADROOM** = Global-LRU vs Oracle-Global-Belady
  (both global cache scope; the scientifically clean primary upper bound
  for future causal prediction/retention work).
- **CAUSALLY RECOVERED MARGIN** = `NOT_YET_MEASURED -- II-C`; never a
  fabricated percentage.

## Dependencies

- R2-01 (trace/replay validation)
- R2-02 (Oracle vs retention baselines at 4 GiB)
- R2-03 (budget-sensitivity sweep; the direct upstream evidence source)

## Implementation

`argine/research2/r2_04/`: `prerequisite.py` (R2-01+R2-02+R2-03 PASS and
binding-consistency gate, reusing `r2_03.prerequisite` for the first two),
`decomposition.py` (structural/information/total gap formulas, reusing
`r2_02.comparison.oracle_gap` verbatim), `regimes.py` (effective-
residency / capacity-constrained-point / peak-opportunity / curve-shape
classification), `lfu_summary.py` (secondary LFU-vs-Global-LRU summary),
`claims.py` (SUPPORTED NOW vs NOT YET SUPPORTED claims, plus the
documented-only II-C future metric), `figures.py` (paper figure data A/B/
C), `orchestrator.py` (ties it together, writes evidence, classifies
PASS/INCONCLUSIVE/FAIL).

## Outputs

`manifest.json`, `provenance.json`, `recoverable_margin.json`,
`margin_by_budget.csv`, `claims.json`, `summary.json`, `summary.md` --
under the Research2 evidence root, plus one registry entry.

## Outcome rules (summary; see `docs/research2/METHODOLOGY.md`)

- **PASS**: upstream PASS chain + binding verified; every R2-03-declared
  budget decomposes cleanly; two independent syntheses agree on a
  canonical digest; no internally impossible decomposition values; causal
  recovery explicitly left unmeasured. A zero/near-zero headroom result is
  still a valid PASS.
- **INCONCLUSIVE**: a declared budget is missing a required policy row
  and cannot be decomposed.
- **FAIL**: an internally impossible decomposition value, a malformed
  upstream row, nondeterministic synthesis, or a generated claim that
  violates the naming safeguard (Oracle headroom presented as demonstrated
  causal recovery).

## Explicit statement

This experiment does **not** draft the II-A paper section and does
**not** start II-B. After a real PASS run, II-A becomes CLOSED/PASS
(R2-01..R2-04 all PASS); II-B remains FUTURE/READY until a separate
"paper-freeze" step completes.
