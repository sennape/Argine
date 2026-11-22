# Preregistration — Research 4, Causal Future-Distance Distillation (CFDC)

State: `R4_CFDC_PHASE0_FROZEN`. EVAL: `LOCKED`. The machine-readable contract (`PROTOCOL.json`, `DATA_SPLITS.json`, `FEATURE_CONTRACT.json`, `BASELINES.json`, `MODEL_CONTRACT.json`, `DECISION_RULES.json`) is authoritative; this file summarizes it. `FREEZE.json` binds every file, the frozen code the later phases must use, and the data hashes.

## Question
How much of the clairvoyant expert-eviction optimum can be recovered by a cheap causal policy using only information available at eviction-decision time? Specifically: can a compact deployable eviction rule, distilled offline from clairvoyant next-use supervision but evaluated causally under its own cache state, recover enough of the frozen Belady miss-byte headroom to justify any later runtime implementation?

## Regime and replay
Offline, integer-exact replay of frozen Qwen3-30B-A3B-Q4_K_M routing traces at a 1.5 GiB expert budget, using the Research 3 replay loop (`r4.py:dev_replay`, R2-09A accounting), demand admission and event-atomic multi-key events. One replay per trace from a cold cache. No model is run.

## Splits
| role | traces | prior exposure |
|---|---|---|
| TRAIN | 4 DEV seed-42 traces (coding, instruction, reasoning, summarization) | R2-09A/R2-10 development; Research 3 NUE selection |
| VALIDATION | 12 R2-11 traces (4 categories × 3 versions) | b_selected and predicted_protect miss bytes; `r2-11-coding-v1` = Research 3 canonical prompt |
| EVAL | 4 R2-09 EVAL traces (one per category) | predictor recall@k only; never any eviction outcome |
| REPLICATE_CHECK | 4 DEV seed-43 traces | report-only |

**Deviation from the request.** The request named DEV seed-43 as an independent VALIDATION corpus. A Phase 0 audit of DEV data showed that under greedy decoding seed-43 routing equals seed-42 on every step except the last (coding 394/395, other workloads 523/524). The investigator chose the R2-11 corpus as VALIDATION on 2026-11-22; seed-43 is report-only.

## Decision unit, teacher and features
Each eviction needed to admit one missed expert is a decision. Candidates are residents not requested by the current event; if there are none, all residents. The teacher distance is the number of served events until the candidate's next request (∞ if none), from `argine.research.oracle.next_uses`; the target is log2(1+d), with ∞ mapped to 21. TRAIN labels come from the b_selected state trajectory (every 64th decision, all candidates). Learned policies are evaluated under their own cache state; that shift is part of the test.

The 16 integer, past-only features are defined in `FEATURE_CONTRACT.json`: recency, 0.99-decayed count, time since admission, hits since admission, reload count, previous and mean reuse interval, membership in the layer's previous event, occupancy, pending misses, recency and frequency ranks, expert bytes, layer, expert, and layer-cycle offset. No feature may be added later.

## Models (one configuration each, numpy only, deterministic)
- **CHEAP**: one CART regression tree, depth ≤ 3, ≤ 4 distinct features (top-4-by-importance refit if needed, TRAIN only), leaves of ≥ 1000 rows from ≥ 50 decisions, integer thresholds, integer leaf ranks; evict highest leaf rank, ties LRU → layer → expert.
- **RICH**: gradient-boosted regression trees, 200 rounds, learning rate 0.1, depth 6, minimum leaf 200, 255 TRAIN quantile bins, no subsampling; predictions quantized to 2^-16 before ranking. Diagnostic only; not a runtime candidate.

## Comparators (same replay)
A = b_selected (reference, frozen), BELADY (oracle denominator), NUE_X (Research 3 code, unchanged), DECAYED_COUNT (evict lowest 0.99-decayed count; ties LRU → layer → expert; no sweep).

## Primary endpoint
capture(P) = Σ_w (B_A,w − B_P,w) / Σ_w (B_A,w − B_Bel,w), decode miss bytes, pooled over the split's traces, exact rationals, uncapped. Reported with absolute reduction, per-workload capture and sign. A pooled denominator ≤ 0 is INVALID; a workload denominator ≤ 0 makes that workload UNDEFINED and fails rule C.

## Decision rules (frozen)
- **A — operational viability:** CHEAP capture ≥ 25% on VALIDATION and on EVAL (20–24.999% is a fail).
- **B — incremental value:** CHEAP EVAL capture ≥ max(25%, best of NUE_X and DECAYED_COUNT on EVAL + 3 percentage points).
- **C — workload consistency:** CHEAP capture ≥ 0 on every EVAL workload.
- **RICH gate:** RICH ≥ 25% on VALIDATION and EVAL.

| condition | class |
|---|---|
| integrity failure | `R4_CFDC_INVALID` |
| A ∧ B ∧ C | `R4_CFDC_CHEAP_VIABLE` |
| A ∧ B ∧ ¬C | `R4_CFDC_HETEROGENEOUS_NOT_VIABLE` |
| A ∧ ¬B | `R4_CFDC_CHEAP_NOT_INCREMENTAL` |
| ¬A ∧ RICH passes | `R4_CFDC_CAUSAL_INFORMATION_PRESENT_CHEAP_NOT_DEMONSTRATED` |
| ¬A ∧ RICH fails | `R4_CFDC_CAUSAL_STATE_INSUFFICIENT` |

Rule C and the RICH gate are reported in every class. `COMPARATOR_REACHES_VIABILITY` is added when NUE_X or DECAYED_COUNT reaches 25% on both VALIDATION and EVAL; it qualifies the class and never upgrades it.

## Integrity preconditions (Phase 1, before any scoring)
A reproduces its frozen miss bytes on every TRAIN and VALIDATION trace. NUE_X reproduces its frozen Research 3 DEV rows on TRAIN. BeladyGlobal reproduces the frozen Research 3 R2 value on the canonical stream. Every trace hash is checked before reading.

## Sequence and EVAL lock
Phase 1 implements the frozen code, extracts TRAIN labels, fits CHEAP and RICH on TRAIN, scores VALIDATION once, and commits a lock (script hash, model hashes, VALIDATION scores). Phase 2 opens EVAL exactly once, whatever VALIDATION showed, scores all arms in one hashed run, and classifies.

## After the result
`R4_CFDC_CHEAP_VIABLE` authorizes only a standalone CHEAP cost microbenchmark. Its later gate is benefit ≥ 10 ms/token at κ_low = 0.3792 ms/MB and benefit/cost ≥ 2.0. Any other class closes this causal-retention direction for the regime.

**No rescue:** no second feature round, new model family, threshold relaxation, new training or EVAL corpus, EVAL-driven engineering or tuning, post-hoc metric, extra candidate, or re-opening of EVAL.
