# R2-10 -- Causal Working-Set Predictor

**Phase:** II-C
**Depends on:** R2-09 (R2-09A closed/PASS_CONFIRMED, R2-09B frozen)

## Scientific purpose

Build a causal (no-future-information) predictor of the near-future
expert working set, benchmarked DEV-only against trivial/null baselines,
as a precursor to R2-11 (predictive retention) and R2-12 (runtime
validation).

## Current state

**R2-10 Phase 0: CONTRACT FROZEN** (`PHASE0_FROZEN`, see
`R2-10-PHASE0-FREEZE.json`). The experimental contract -- causal boundary,
target definition, output contract, metrics/aggregation, DEV/EVAL
protocol, predictor candidate grid, null/trivial baselines, selection
rule, and acceptance/interpretation categories -- is defined and tested
(`tests/test_r2_10_contract.py`, `tests/test_r2_10_leakage.py`) BEFORE any
DEV predictor tournament result exists.

**No R2-10 scientific result exists yet.** Phase 0 implements only the two
trivial/null causal baselines (`global_frequency`, `last_observation`)
needed to exercise the causal walker and output contract end-to-end on
synthetic traces; it does not run the predictor tournament against the
real DEV corpus.

## Contract components (this directory)

- `contract.json` -- top-level Phase-0 contract, digest, upstream binding
- `causal_boundary.json` -- mechanically auditable ALLOWED/FORBIDDEN feature table
- `output_contract.json` -- deterministic cardinality/tie-break/cold-start rules
- `dev_eval_protocol.json` -- binds to R2-09A's 8 frozen DEV cells; EVAL stays locked
- `predictor_space.json` -- candidate causal predictor families + finite grids; null/trivial baselines
- `selection_rule.json` -- DEV-only selection statistic, aggregation, tie-breaking
- `decision_rules.json` -- acceptance/interpretation categories A/B/C; leakage/determinism audit status

## Implementation (`argine/research2/r2_10/`)

- `contract.py` -- loads/validates/digests the frozen contract; checks upstream R2-09B/R2-09A digests have not drifted
- `causal_walk.py` -- the sole causal-boundary-enforcing path from a `Trace` to predictor-visible data
- `output_contract.py` -- `apply_output_contract` (deterministic top-k selection), `recall_at_k`
- `baselines.py` -- `GlobalFrequencyBaseline`, `LastObservationBaseline`, `shuffled_null_descriptive_recall` (analytical reference only)

## Explicit statement

No R2-10 DEV tournament, R2-11, or R2-12 work has been started. No
R2-09A/R2-09B frozen evidence was modified while producing this contract.
