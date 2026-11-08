# R2-08 — Routing Drift and Workload Dependence

**Phase:** II-B
**Depends on:** R2-07 (PASS)
**Implementation:** `argine/research2/r2_08/` (registered in `argine.research2`)
**Roadmap state:** `implemented = true`; pre-real-execution scientific state is **READY FOR REAL ANALYSIS** (not PASS)

## Scientific purpose
Characterize how routing structure changes over time and across workloads on the
same canonical model/runtime protocol (Qwen3-30B-A3B-Q4_K_M), separating:
1. **Temporal drift** — within one execution of one workload.
2. **Same-workload replicate variability** — same workload/protocol, seed 42 vs 43
   (seed replicates if stochastic; otherwise same-workload reproducibility
   replicates, stated explicitly).
3. **Workload dependence** — between different workload families.

These quantities remain separate and are never collapsed into a single
“routing variability” measure.

## Core question
Does the routing structure discovered in R2-05, R2-06, and R2-07 remain
sufficiently stable to support a future causal mechanism, or is it strongly
dependent on workload and/or rapidly drifting over time? All outcomes
(stable, temporal-drift dominant, workload-dependence dominant, replicate-
variability dominant, mixed, layer-heterogeneous) are scientifically valid.
PASS is methodological, not outcome-dependent.

## Model and scope
Canonical model only: **Qwen3-30B-A3B-Q4_K_M**. No cross-model work. No
predictor, no heuristic, no prefetch, no retention-policy changes, no
Oracle-headroom recovery (II-C). No runtime performance claim. Supported claims
are limited to the tested Qwen model, tested workload families, and tested
consumer-machine protocol.

## Canonical capture protocol (reconstructed from R2-01 provenance)
Held constant across captures: model/runtime identity, quantization, trace
schema, instrumentation, expert-memory budget **4 GiB**, context **4096**,
generation threads **4**, batch threads **12**, cache/store configuration, and
generation parameters except seed/replicate. Only workload, workload prompt
digest, and seed/replicate may differ.

## Workload matrix (8 canonical cells)
| Workload | Seed 42 | Seed 43 |
|---|---:|---:|
| `coding-v1` | reuse canonical R2-01 trace (`bb6d8ca34dbc4537a43d54be30c9f3e1`, capture `286a41477ce54d5fb9ccc24f4e508f22`, SHA `e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1`) | capture |
| `reasoning-v1` | capture | capture |
| `summarization-v1` | capture | capture |
| `instruction-v1` | capture | capture |

`coding-v1` reuses `workloads/v1/coding.json` (dev). `reasoning-v1`,
`summarization-v1`, and `instruction-v1` are frozen R2-08 definitions under
`research2/workloads/r2_08/` (prompt content frozen before real capture).
`summarization-v1` uses a committed local technical source
(`research2/workloads/r2_08/sources/summarization_technical_source.txt`) with no
web/network dependency.

## Analysis design
- **Primary phase:** DECODE only (prefill excluded to avoid prompt-length confounding).
- **Matched horizon:** `H = min(512, min valid decode positions across all 8 cells)`;
  canonical PASS requires `H >= 256`. First `H` valid decode routing positions
  per layer; no silent truncation.
- **Windows:** exactly 8 contiguous chronological windows, `W=floor(H/8)`,
  remainder beyond `8*W` trimmed and recorded.
- **Primary drift metric:** Jensen–Shannon divergence, log base 2, full nominal
  128-expert support including zeros, `[0,1]`.
- **Hot-set turnover:** top-8 and top-16 Jaccard distance, deterministic
  tie-breaking by expert ID ascending.
- **Replicate variability:** seed 42 vs 43, same matched horizon `H` (frequency
  JSD, top-8/16 Jaccard, normalized entropy difference).
- **Cross-workload variability:** layer-local frequency JSD and top-8/16 Jaccard
  for every pair of different workload families; replicate identity preserved.
- **Bridges (reused definitions):** R2-05 (frequency/normalized entropy/effective
  expert fraction/top-k share), R2-06 (NMI, self-transition lift), R2-07
  (immediate reuse, reuse ≤4/≤16, median token reuse gap, W32/W128 working-set).
- **Three-scale comparison & regime:** median temporal / replicate / cross-workload
  JSD with descriptive ratios (denominators > 0 only). Predeclared regimes:
  `STABLE_ACROSS_WORKLOADS`, `TEMPORAL_DRIFT_DOMINANT`,
  `WORKLOAD_DEPENDENCE_DOMINANT`, `RUN_VARIABILITY_DOMINANT`, `MIXED`.

## Outcome rules
- **PASS (methodological):** prerequisites verified; 8 valid canonical cells;
  compatible fingerprints; decode phase available; `H >= 256`; deterministic
  windows/distributions/JSD/Jaccard; repeated offline synthesis produces
  identical canonical digest; no forbidden claims. PASS does not require
  meaningful workload dependence.
- **INCONCLUSIVE:** <8 valid cells, `H < 256`, decode phase unavailable,
  ambiguous protocol, or indefensible replicate semantics.
- **FAIL:** configuration drift outside allowed differences, wrong
  model/runtime/trace identity, malformed expert IDs/layers/distributions,
  nondeterministic digest, post-hoc workload mutation, or unsupported claims.

## Outputs (Research2 evidence root)
`summary.json`, `capture_matrix.json`, `workload_manifest.json`,
`capture_diagnostics.json`, `temporal_drift.csv/.json`,
`replicate_variability.csv/.json`, `cross_workload.csv/.json`,
`layer_summary.csv/.json`, `workload_metrics.json`, `comparison.json`,
`claims.json`, `figure_data.json`, `provenance.json`. Full traces are referenced
by path+SHA, not copied.

## Current state
**Implemented; READY FOR REAL ANALYSIS.** No canonical real-capture result exists
yet in this implementation task. The automated workflow is:
```bat
argine.bat research2 run R2-08 --model C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf
```
This command is **not executed** during implementation. After a successful
human R2-08 PASS, II-B is CLOSED/PASS and the next action is **II-B paper
freeze** (not R2-09). `paper/research2/` is not modified.
