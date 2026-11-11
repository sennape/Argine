# R2-10 Phase 1 -- Predictor Implementation & Validation (implementation note, not a contract file)

This file is NOT part of the Phase-0 frozen contract digest (it is not one
of `contract.py`'s `COMPONENT_FILES`) and must stay that way. It records,
without modifying any frozen Phase-0 file, which predictor families are
now implemented.

- `argine/research2/r2_10/baselines.py`: `global_frequency`, `last_observation`
  (Phase 0), plus `shuffled_null_descriptive_recall` (ANALYTICAL_REFERENCE_ONLY,
  never a runtime candidate, never in `selection_rule.json`'s pool).
- `argine/research2/r2_10/predictors.py` (Phase 1, new): `recency_window`
  (grid: window_W in {4,16,64,256}), `markov_transition` (fixed, no
  hyperparameters), `decayed_transition` (grid: decay_factor in
  {0.9,0.99,0.999}), `recency_frequency_transition_blend` (grid: 6 weight
  triples). Grids are copied verbatim from `predictor_space.json`; see
  `tests/test_r2_10_predictors.py::test_grids_match_frozen_predictor_space_json`
  for the executable check that they have not drifted.

All six `causal_runtime_candidate` families now exist and pass the same
output-contract and leakage tests (`tests/test_r2_10_predictors.py`).

See `CARDINALITY-AVAILABILITY-AUDIT-001` (`CARDINALITY-AVAILABILITY-AUDIT.json`,
also not a contract component file) for the resolved cardinality-leakage
question: all Phase-1 predictors source `k` from `ctx.k`
(`trace.meta.top_k`, resolved before any routing event is observed), never
from the target.

The R2-10 DEV tournament runner architecture (Step 5) is in
`argine/research2/r2_10/runner.py`. No full DEV tournament has been
executed; only a smoke test against synthetic/isolated data has been run
(see the Phase-1 freeze record / commit message for results).
