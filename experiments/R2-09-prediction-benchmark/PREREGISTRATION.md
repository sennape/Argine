# R2-09B Benchmark and Evaluation Contract Preregistration

**Contract:** `r2-09b-contract-v1` / schema 1  
**Status:** `FROZEN_WITH_RUNTIME_MATERIALITY_GATE`  
**Digest:** see `contract.json` (`contract_digest`)  
**Upstream paper baseline:** `4025a2ecdd30cb5d2604fdfdf5cb4966f594a11d` (II-A+II-B semantically closed)  
**Audit:** `AUDIT-ORACLE-001` `3789a8511e9623f953dda5a610c9d7897e4ae63b`, CASE C, resolved by paper amendment

## 1. Purpose
R2-09B prevents Research II-C from winning by construction. All selection-sensitive benchmark choices are frozen before R2-09A baseline selection, R2-10 predictor selection, EVAL opening/inspection, and R2-11 retention claims.

## 2. Existing DEV evidence (bound)
Existing Research2 characterization is DEV evidence. Canonical R2-01 coding trace is DEV (SHA `e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1`); inspected R2-08 workload captures are DEV characterization captures. Canonical R2-03 DEV sweep (`d957077073374f1e9fd1e101a002946f`) contains Global-LRU, LFU, Runtime-Sharded-LRU, `O_B` at 1.5/2/4/8/16 GiB. LFU is `LFU_SWEEP_CANONICAL_AUXILIARY`: LFU loses to Global-LRU by +4.368686% (1.5), +7.664017% (2), +9.760895% (4), +11.979284% (8) miss bytes and ties at 16 GiB on that DEV trace. **Do not rerun to rediscover.** R2-09A begins from this evidence. This does not establish EVAL outcomes, general workload/model/capacity ordering, or that aged/decayed frequency loses.

## 3. DEV/EVAL firewall
**DEV** may be inspected and used for algorithm development, baseline-family comparison, hyperparameter selection, predictor architecture/model selection, and threshold-methodology derivation only where explicitly permitted; numeric engineering threshold is not fabricated. **EVAL** workload specifications are frozen before EVAL results are inspected. No EVAL performance result may tune baseline/predictor parameters, revise primary metric, revise materiality threshold, revise success/falsification criteria, or add/remove candidate algorithms while still reporting held-out EVAL. If EVAL reveals failure and a new method is developed, that is a new experimental generation/version: old EVAL becomes DEV/observed for that generation and a new held-out EVAL is required for fresh confirmatory claims.

## 4. Held-out EVAL design (acquisition contract only; not executed)
Audit found no existing untouched Research2 EVAL trace/capture artifacts in tracked repo evidence/data paths at freeze time. R2-09B freezes **acquisition/design only**; no EVAL traces captured.

**Design decision (not statistically powered):** smallest balanced design supporting per-workload-family reporting = 4 new held-out workload specifications (1 per family: coding, reasoning, summarization, instruction), 1 deterministic capture per spec = 4 primary EVAL capture cells under frozen deterministic condition. Replicate/robustness extensions require explicit contract amendment before EVAL opening if scientifically justified.

Families reuse II-B categories but **new held-out specs** (`research2/workloads/r2_09/*`, split `eval`, version `r2-09-eval-v1`); exact DEV prompts are not reused. Specs are hashed/versioned in `eval_manifest.json`. Deterministic capture follows II-B protocol (temp 0.0, top_k 1; record actual seed/runtime/context/capture config). Trace schema/validation/rejection-retry rules are frozen in `eval_manifest.json`. Model/runtime identities must be recorded at acquisition; no acquisition occurs now.

## 5. EVAL immutability / visibility
EVAL specs are versioned and SHA-bound. Silent edits after DEV results from R2-09A/R2-10 are forbidden without creating a new benchmark version. States: `EVAL_LOCKED` (current), acquisition planned (metadata only), captured/sealed (not inspected for method selection), `EVAL_OPENED` (irreversible for this generation). EVAL results may not be inspected until `B_selected` and selected R2-10 predictor/config are frozen and opening is recorded.

## 6. Primary budgets
Primary II-C budgets: **1.5 GiB** (largest tested absolute Global-LRU↔`O_B` comparator gap; high pressure), **4 GiB** (canonical anchor/capture budget/R2-02 reproduction), **8 GiB** (largest tested relative comparator gap). **16 GiB** excluded as primary pressured budget (effective residency/policy indifference). **2 GiB** optional sensitivity only.

## 7. Causal baseline families (R2-09A)
Frozen families: Global-LRU (existing DEV evidence), LFU (existing canonical auxiliary DEV evidence), 2Q, LRU-K, aged/decayed frequency; optional simple recency-frequency hybrid slot disabled unless exact semantics frozen before R2-09A. All strictly causal, byte-capacity-aware, deterministic tie-breaking/cold-start/admission/eviction semantics defined in `baseline_space.json`. No predictor-driven policies in R2-09A.

## 8. Hyperparameter search spaces
Every tunable baseline has a finite frozen DEV search grid in `baseline_space.json` (2Q A1in fraction/A1out history/main policy; LRU-K K/correlated reference period; aged frequency decay form/factor/aging interval). Spaces motivated by II-B evidence but not selected post-hoc; no large overfitting search.

## 9. `B_selected` rule
Primary deployable comparator `B_selected(M)` is selected on **DEV only**: lowest aggregate DEV miss bytes at budget M across preregistered DEV workload families (deterministic), tie-break by lower aggregate misses then stable lexical family/config id; frozen before EVAL inspection. `B_selected(M,W_class)` may be reported as secondary diagnostic only if workload family is observable before retention decisions and rule preregistered. `B_oracle_family_on_eval` is hindsight diagnostic only, **never** deployable selected baseline and **never** primary predictor comparison.

## 10. LFU existing-evidence handling
LFU is not rerun for discovery. Canonical R2-03 DEV shows static LFU does not outperform Global-LRU on that DEV trace/grid at capacity-constrained points; this does not generalize. Aged/decayed frequency remains a valid R2-09A candidate.

## 11. Prediction target / horizons
Primary: **next-step same-layer routing prediction** (per-layer, per decode step `t`, target expert set `E_{t,l}`; causal input only). Secondary diagnostic: short-horizon future working-set union for `h ∈ {1,4,16}` (grounded in R2-07 immediate/≤4/≤16 locality; frozen, not changed after EVAL). See `prediction_contract.json`.

## 12. Causal features
Allowed (strictly causal): recency, cumulative frequency, decayed/aged frequency, recent reuse-distance history, same-layer transition history, cross-layer routing history (candidate, not established superior), observable workload-family context only if preregistered/frozen before DEV tuning, simple causal combinations. Forbidden: future expert IDs/routing/cache events, target/future leakage, EVAL labels, post-hoc unavailable labels.

## 13. Predictor family boundary
R2-10 may compare simple causal predictors (recency, frequency, decayed frequency, transition-based, cross-layer, small combined). No neural/transformer required. Complexity is not success.

## 14. Prediction metrics
Primary predictive metric: **recall of future requested experts** (higher better), chosen because retention misses occur when future-requested experts are absent; avoids misleading scalar accuracy on sparse top-k sets. Secondary: precision, F1, Jaccard, ranking metric if scores output, optional byte-weighted recall. Report per workload family / relevant cells, not only global.

## 15. Retention metrics
Primary physical replay quantity: **miss bytes** (lower better). Also report misses, miss rate, evictions, peak resident bytes, budget violations. `DeltaMissBytes_vs_B_selected = MissBytes(P)-MissBytes(B_selected)` (negative better). Continuity: `Recovery_vs_LRU` where useful. Comparator: `RecoveryOfBeladyGap=(B-P)/(B-O_B)` with CASE C semantics.

## 16. CASE C comparator semantics
`O_B` is implemented clairvoyant Belady-style furthest-next-use comparator, **not** proven byte-optimal. `O* <= O_B`, `O* <= C*`, no guaranteed ordering between `O_B` and `C*`. `RecoveryOfBeladyGap` is signed, may be negative or >1; >1 means P beats `O_B` on miss bytes, **not** `O*`; not a fraction of maximum causal opportunity. Forbidden: Oracle headroom, upper bound on causal recovery, maximum recoverable opportunity.

## 17. Reporting granularity
Primary: per `(M, W_class/workload instance, decoding condition)`. Required per cell: budget, workload, decoding condition, `B_LRU`, LFU where relevant, `B_selected` family/config, `P`, `O_B`, miss bytes, misses, `Recovery_vs_LRU` where applicable, `RecoveryOfBeladyGap`, primary predictive metric, engineering relevance metric only if scientifically valid. Aggregates secondary; positive aggregate must not hide systematic family losses.

## 18. Deterministic / stochastic conditions
Deterministic (separately reported, never pooled): temperature 0.0, top_k 1. Stochastic: **UNRESOLVED** (`STOCHASTIC_PROFILE_UNRESOLVED`) — no versioned canonical stochastic profile found; no numeric profile invented. Blocks stochastic robustness claims until explicit justified profile + seed IDs frozen before EVAL opening by contract amendment. Deterministic evaluation remains defined.

## 19. EXP-025 / practical-effect threshold
EXP-025 is `P2_FULL_STREAM_DISTRIBUTION_MODEL`; transferability to arbitrary policy-induced miss streams **UNSUPPORTED**. No direct miss-byte/constant-bandwidth→seconds conversion. Future modeled form: `DeltaT_model = PerformanceModel(MissStream_B_selected)-PerformanceModel(MissStream_P)` only after validation. `THRESHOLD_STATUS=PENDING_POLICY_STREAM_CALIBRATION`; numeric engineering threshold not fabricated. Blocks R2-11 engineering-materiality claims, not DEV-only R2-09A.

## 20. Scientific vs engineering improvement
**Scientific improvement:** `P` has fewer held-out miss bytes than `B_selected` under identical semantics. **Engineering-material improvement:** exceeds a preregistered physically justified threshold (currently not evaluable pending calibration). Report separately.

## 21. Falsification / hypotheses
Statuses: prediction benchmark / retention improvement = SUPPORTED/FALSIFIED/INCONCLUSIVE; engineering materiality = SUPPORTED/FALSIFIED/NOT_EVALUABLE_PENDING_CALIBRATION. No vague “significant improvement”. Primary hypotheses are stable per-cell IDs in `decision_rules.json` (3 budgets × 4 families × deterministic = 12 confirmatory cells), all `EVAL_LOCKED`, no results. Primary confirmatory vs secondary vs exploratory boundaries frozen.

## 22. R2-09A/10/11/12 output contracts
**R2-09A:** all preregistered baseline families on DEV, selected/frozen hyperparameters, `B_selected` rule, full non-winning results, no EVAL-driven selection, deterministic replay verification, budget semantics, evidence digest, tournament freeze artifact. **R2-10:** all preregistered predictor candidates, DEV performance, selected predictor/config frozen before EVAL, full losing results, no retention claim by default. **R2-11:** frozen predictor vs `B_selected`/Global-LRU/LFU where relevant/`O_B` comparator only; prediction accuracy ≠ retention benefit. **R2-12:** only stage for measured real-runtime latency/throughput/tokens/sec/TTFA/actual I/O.

## 23. Reproducibility
Machine-readable components are versioned in this directory. `contract.json` records `contract_digest` (deterministic over components and frozen EVAL spec files, excluding self digest field). Any change to EVAL specs/budgets/baseline families/hyperparam spaces/prediction horizons/primary metrics/stochastic profile/decision rules/threshold status changes digest.
