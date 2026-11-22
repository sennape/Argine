# R4-00 — Causal Future-Distance Distillation (CFDC), Phase 0

**State:** `R4_CFDC_PHASE0_FROZEN`. **EVAL:** `LOCKED`. **Outcome metrics:** none exist.

Research 4 asks how much of the clairvoyant expert-eviction optimum a cheap causal policy can recover using only information available at the eviction decision. This directory holds the frozen Phase 0 protocol only: no replay, training or scoring has been run, and no VALIDATION or EVAL trace has been read.

| file | content |
|---|---|
| `PREREGISTRATION.md` | human-readable summary of the frozen protocol |
| `PROTOCOL.json` | question, regime, phases, prohibitions, Research 3 preservation |
| `DATA_SPLITS.json` | TRAIN / VALIDATION / EVAL / REPLICATE_CHECK trace identities and hashes, prior exposure, seed-43 independence audit |
| `FEATURE_CONTRACT.json` | 16 causal integer features, clocks, candidate set |
| `BASELINES.json` | replay semantics, reference A, Belady oracle, NUE_X and DECAYED_COUNT |
| `MODEL_CONTRACT.json` | teacher, CHEAP and RICH definitions |
| `DECISION_RULES.json` | endpoint, thresholds, classification order, integrity preconditions, EVAL audit, anti-rescue |
| `LITERATURE_BOUNDARY.md` | relationship to Bacon et al. 2026, FlashMoE, Zhang 2026 and learned-replacement work |
| `CLAIM_BOUNDARIES.md` | what Phase 0 does and does not establish |
| `FREEZE.json` | file hashes, contract digest, bound-code hashes (LF-normalized), data hashes, state |
| `phase0.py` | builds and verifies the contract (`python phase0.py verify`) |
| `tests/test_phase0.py` | contract tests |

Research 3 is closed and unchanged (`R3_CLOSED_NEGATIVE_PREDICTIVE_RETENTION_NOT_VIABLE_IN_TESTED_REGIME`); `phase0.py verify` checks that no Research 3 file differs from the source head.
