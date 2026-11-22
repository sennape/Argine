# R2-09B Benchmark and Evaluation Contract Preregistration (v1.1 hardened)

**Contract:** `r2-09b-contract-v1.1` / schema 1
**Status:** `FROZEN_WITH_RUNTIME_MATERIALITY_GATE`
**R2-09A readiness:** `R2_09A_READY` (DEV-only)
**v1.1 digest:** see `contract.json` (`contract_digest`)
**v1.0 digest (preserved):** `2f4c0859c6ce3f589142cd8b1c6cb3c3a47946f1b222a5a2e875495cccaa6f5d`
**Upstream paper baseline:** `4025a2ecdd30cb5d2604fdfdf5cb4966f594a11d` (II-A+II-B semantically closed)
**Audit:** `AUDIT-ORACLE-001` `3789a8511e9623f953dda5a610c9d7897e4ae63b`, CASE C, resolved by paper amendment

## 1. Purpose
R2-09B prevents Research II-C from winning by construction. v1.1 resolves preregistration ambiguities before R2-09A while preserving v1.0 provenance and unchanged scientific constraints (CASE C, EXP-025/threshold gates, stochastic unresolved, primary budgets, EVAL results locked, DEV-only selection, LFU auxiliary not rerun, II-A/II-B evidence/paper unchanged).

## 2. Existing DEV evidence (bound)
Canonical R2-01 coding trace DEV SHA `e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1`; R2-08 DEV characterization captures. R2-03 DEV sweep `d957077073374f1e9fd1e101a002946f` contains Global-LRU, LFU (`LFU_SWEEP_CANONICAL_AUXILIARY`), Runtime-Sharded-LRU, `O_B` at 1.5/2/4/8/16 GiB. LFU vs Global-LRU miss bytes on that DEV trace: +4.368686/7.664017/9.760895/11.979284/0.0%. Not rerun; not generalized.

## 3. DEV/EVAL firewall
DEV allowed for inspection/development/baseline comparison/hyperparameter selection/predictor selection on DEV (no fabricated numeric engineering threshold). EVAL results locked; no EVAL performance result may tune parameters, revise primary metric/threshold/falsification, or add/remove candidates while reporting held-out EVAL. Post-EVAL failure + new method => new generation; old EVAL becomes DEV/observed for that generation; new held-out EVAL required for fresh confirmatory claims.

## 4. Held-out EVAL generalization scope and design (acquisition contract only)
Audit found no existing untouched Research2 EVAL trace/capture artifacts at freeze time. No capture executed.
- **Option A retained:** 4 new held-out workload specifications = **1 held-out workload instance per nominal category** (coding, reasoning, summarization, instruction), 1 deterministic capture each = 4 primary EVAL cells.
- **Confirmatory scope:** limited to these four frozen held-out instances/specs. A single instance does not establish generalization across an entire workload family/category. **Family/category-level generalization is unmeasured.**
- New held-out specs in `research2/workloads/r2_09/` (`r2-09-eval-v1`, split `eval`), hashed; exact DEV prompts not reused.

## 5. EVAL spec visibility and result lock
- **EVAL results:** `EVAL_LOCKED` (no inspection/opening until `B_selected` and selected R2-10 predictor/config frozen and opening recorded).
- **EVAL spec visibility:** `EVAL_PROMPTS_VISIBLE_BUT_RESULTS_HELD_OUT`. Exact prompt/spec files are tracked for immutable provenance/SHA binding; repository access means this is **not fully blinded**. The contract **prohibits prompt-specific feature/model engineering tailored to exact EVAL prompt/task content** and prohibits all EVAL-result-driven selection/tuning. Any prompt-content-driven overfitting risk must be reported as a benchmark-integrity limitation.
- States: `EVAL_LOCKED` (results), acquisition planned metadata-only, captured/sealed, `EVAL_OPENED` (irreversible per generation).

## 6. Primary budgets
Primary: **1.5 GiB** (largest tested absolute Global-LRU↔`O_B` gap), **4 GiB** (anchor), **8 GiB** (largest tested relative gap). **16 GiB** excluded as primary pressured budget (effective residency). **2 GiB** optional sensitivity only.

## 7. Causal baseline families and complete semantics
Families: Global-LRU, LFU (existing DEV evidence), 2Q, LRU-K, aged/decayed frequency; optional simple hybrid disabled unless amended with full semantics before R2-09A. All strictly causal, byte-capacity-aware, deterministic. **All outcome-relevant semantics frozen in v1.1** (no implementation-spec deferral): byte-capacity/object-size treatment, global causal access ordinals, admission/eviction, deterministic tie-breaking, cold start. 2Q frozen: `M_a1in=floor(M*a1in_fraction)`, `M_am=M-M_a1in`; A1in FIFO (no promotion on hit); Am LRU; A1out ghost key history with `G=max(1,floor(N_ref*a1out_slots_factor))`, `N_ref=max(1,floor(M/S_ref))`, `S_ref`=median trace expert bytes (deterministic fallbacks), ghost stores keys only; miss with ghost key -> Am; miss without -> A1in; evicted A1in key -> A1out; stable `(layer,expert_id)` ties. LFU/LRU-K/aged frequency tie-breaks and update rules fully frozen in `baseline_space.json`.

## 8. B_selected rule (exact aggregation)
Primary deployable comparator `B_selected(M)` selected **DEV only**, frozen before EVAL inspection/opening.
- `Score(B,M)=mean_{i in DEV_instances} (miss_bytes(B,M,i) / decode_steps(i))`, **lower better**.
- **Normalization:** `decode_steps(i)` = decode routing steps/events in instance `i` under frozen replay/trace semantics.
- **Weights:** equal weight `1/N` per DEV workload instance (after normalization).
- **Tie-breaking:** lower mean `misses(B,M,i)/decode_steps(i)`, then stable lexical `family_id + canonical config id`.
- **DEV corpus:** R2-09A freezes exact DEV tournament trace/instance set in its manifest before any tuning/selection (may include canonical R2-01/R2-03 coding and/or R2-08 DEV captures); not chosen by results.
- `B_selected(M,W_inst_or_category)` secondary diagnostic only if observable and preregistered. `B_oracle_family_on_eval` hindsight diagnostic only, never deployable/primary.

## 9. Prediction target, k_tl cutoff, and cross-layer causal boundary
Primary: next-step same-layer routing prediction of `E_{t,l}`. `k_tl = |E_{t,l}|` from trace metadata/event router-requested cardinality (expected **8** for canonical Qwen3-30B-A3B Research2 traces; read actual value, do not assume when trace differs). Primary metric `future_requested_expert_recall_at_k`: predictor outputs **exactly `k_tl` unique expert IDs** (or top-`k_tl` cutoff for ranked scores); deterministic tie-break score desc then stable `(layer,expert_id)` asc; if variable cardinality encountered, use actual `|E_{t,l}|`. Fixed cardinality prevents recall gaming.
- **Causal boundary (`intrastep_cross_layer_causal_context`):** may use completed prior steps `< t` and routing outcomes/causal features from layers `< l` of the **same step `t`** (already occurred). **Forbidden:** target `E_{t,l}`, any layer `> l` of same step `t`, any future step `> t`, future info/EVAL labels/unavailable labels. Prior-step-only variant only secondary if separately preregistered.

## 10. Metrics, CASE C, EXP-025, thresholds
Primary prediction metric: recall at `k_tl` (higher). Primary physical replay quantity: `miss_bytes` (lower). `DeltaMissBytes_vs_B_selected = MissBytes(P)-MissBytes(B_selected)`. `RecoveryOfBeladyGap=(B-P)/(B-O_B)` is CASE C comparator-relative only: signed, may be negative/>1; >1 means beats `O_B` not `O*`; not fraction of maximum causal opportunity. `O_B` not byte-optimal; `O*<=O_B`, `O*<=C*`, no guaranteed `O_B` vs `C*` ordering. EXP-025 P2, arbitrary policy-stream transferability unsupported; no direct bytes→seconds. `THRESHOLD_STATUS=PENDING_POLICY_STREAM_CALIBRATION` (blocks R2-11 engineering-materiality claims, not R2-09A). Deterministic: temp 0.0, top_k 1. Stochastic: `UNRESOLVED`.

## 11. Reporting and hypotheses
Primary unit: `(M, held-out workload instance, deterministic)`. 12 primary confirmatory hypotheses (3 budgets × 4 held-out instances × deterministic), all `EVAL_LOCKED`. Aggregates secondary; must not hide instance losses. Confirmatory scope limited to the 4 frozen instances; family/category-level generalization unmeasured.

## 12. R2-09A readiness
`R2_09A_READY` for DEV-only execution under frozen v1.1 contract. Gates not blocking R2-09A: engineering materiality pending calibration; stochastic profile unresolved; specs visible/not fully blinded (prompt-specific engineering prohibited).
