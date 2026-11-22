# R3-02 Phase 0 — Calibration and Mechanism-Attribution Protocol

Status: **PASS_R3_02_PROTOCOL_FROZEN** — Phase 0 only. No model was loaded; no acquisition has occurred.

R3-02 asks two prerequisite questions before any cost-aware retention policy is designed:

- **Q1** — what runtime cost does predictive bookkeeping add when retention behaviour is held
  equivalent to `B_selected`?
- **Q2** — how much expert-memory traffic occurs at 1.5 / 4 / 8 GiB under the real runtime?
- **Q3** — at which budgets is there enough measured traffic and runtime signal to justify a later
  cost-aware hypothesis?
- **Q4** — can real TRACE_COUNT + TIMING CELL records be generated under the frozen R3-00A/R3-01
  contracts?
- **Q5** — does the sham remain behaviourally equivalent to `B_selected` through the full real
  model/store path?

R3-02 is mechanism attribution. It is **not** a policy evaluation and must not be reported as a
cost-aware policy success or failure.

## Runtime identity

All 52 planned launches use the new MSVC R3 runtime:

`3f094f5b5f4963f488b63722f365068f09e2aa3b12e2096969da99f471bbd644`

The frozen MinGW R2 runtime (`f4d93061…`) is immutable and is **never** pooled, paired or compared
in absolute timing with the R3 runtime. R2 timing results may be cited only as historical motivation.

## Controls

| id | policy | role |
|---|---|---|
| **A** | `b_selected` | baseline |
| **B** | `predicted_protect_sham` | sham predictive control: full decayed_transition bookkeeping, `B_selected` victim semantics |
| **C** | `predicted_protect` | predictive-policy mechanism reference, **8 GiB only**, not an overhead control |

C is included at 8 GiB only because that is the regime where the frozen R2 replay objective
predicted a tie while runtime behaviour differed. R3-02 is not a re-run of R2.

## Frozen identities

| object | value |
|---|---|
| workload | `r2-11-coding-v1`, `research2/workloads/r2_11/r2-11-coding-v1.json`, sha256 `c02f9657d12d874cc43523e2b609bb604a68a6a8b267b2ffac6d3420c509b730` (DEV only) |
| model | `C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf`, sha256 `a015794b…`, 18,632,184,480 bytes |
| store | `C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs`, sha256 `fef943ae…`, 17,553,387,520 bytes |
| contract | R3-00A digest `99a68c73c5a3caba4fd53937f27907375734f3866c84e18d4ecb944013efa073` |

## Frozen policy parameters

From frozen R2 evidence (`B_SELECTED_PARAMS_BY_BUDGET`), not guessed:

| budget | `decay_factor` | `aging_interval_steps` |
|---|---|---|
| 1.5 GiB (1610612736 B) | 0.999 | 1 |
| 4 GiB (4294967296 B) | 0.99 | 64 |
| 8 GiB (8589934592 B) | 0.999 | 16 |

Predictor (B and C): `predictor_decay = 0.99`, `top_k = 8`, `n_experts = 128`. The sham uses the
same predictive parameters as `predicted_protect` and the `B_selected` victim rule.

## Decoding (frozen)

`seed = 42`, `temperature = 0`, `top_k = 1`, `context = 4096`;
`n_predict = 512` for every scientific TRACE_COUNT and TIMING launch, `n_predict = 32` for
preflights. Chosen before any data exist; never adjusted post hoc.

## Trace reuse (adopted)

One admissible TRACE_COUNT record per configuration backs its **five** counted TIMING repetitions,
only if each paired TIMING run independently matches workload id/hash, seed, budget, policy, full
policy parameters, runtime/model/store identities, schema version, contract digest and
`output_token_digest`. Reuse is never allowed across policies, budgets, workloads, seeds or
identities. Any TIMING run whose digest differs is `INADMISSIBLE`, with no fallback trace.

## Matrix and launch counts

| item | count |
|---|---|
| scientific configurations | 7 (A×3 budgets, B×3 budgets, C at 8 GiB) |
| TRACE_COUNT launches | 7 |
| TIMING warmups | 7 |
| counted TIMING launches | 35 |
| scientific CELL records | 35 |
| scientific matrix physical launches | **49** |
| non-scientific integration preflights | 3 (A, B, C at 8 GiB, `n_predict = 32`) |
| total host process launches | **52** |

Preflights verify only: model loads, store loads, policy id recognized, clean termination, expected
telemetry/provenance lines, deterministic output identity available, no budget-invariant violation.
They never enter endpoint computation, and their performance never alters the protocol.

## Run order (frozen)

Preflights: A, B, C at 8 GiB (60 s apart).

Then per budget block:

- 1.5 GiB and 4 GiB — one TRACE_COUNT per configuration, one warmup per configuration, then five
  counted repetitions alternating `AB, BA, AB, BA, AB`.
- 8 GiB — TRACE_COUNT for A, B, C; warmups A, B, C; then five counted repetitions of the balanced
  Latin-square sequence `ABC, BCA, CAB, ABC, BCA`.

Cooldowns from the frozen R2-12 semantics: **15 s** between adjacent controls within a repetition,
**60 s** between repetitions, blocks, TRACE_COUNT/warmup transitions and preflights. No outcome
dependent reordering.

## Environment (frozen)

Required: browsers fully closed (Chrome and all others), no streaming video, no heavy background
work, operator-attested clean environment, fixed non-Power-Saver power mode, free-RAM feasibility
gate, frozen runtime/model/store/workload SHA and policy parameters.

Explicitly **not** gates: AC connection, battery percentage, TZS0 threshold.

## Endpoints (definitions only; no values exist yet)

- `step_time = decode_duration_seconds / tokens_generated` (TIMING), seconds/token.
- `delta_overhead_time(budget) = median over paired repetitions of step_time(B) − step_time(A)`.
- `delta_overhead_relative = (step_time(B) − step_time(A)) / step_time(A)`.
- CPU analogue with `process_cpu_seconds / tokens_generated` (never a substitute for wall clock).
- Traffic per configuration from TRACE_COUNT: miss bytes/token, bytes read/token, reads/token,
  evictions/decision step, peak resident bytes.
- Precision rule **OVERHEAD_BOUNDED**: the 95 % CI half-width of `delta_overhead_time` at the 4 GiB
  budget must be ≤ 1 % of the median `B_selected` step time at that budget.

TRACE_COUNT wall time is never a performance measurement.

## Statistics (frozen)

Paired by repetition within budget; 5 counted repetitions; per-repetition paired differences;
median of differences; 10,000 paired bootstrap resamples with fixed seed 20261116; 95 % percentile
CI. No p-value, no outlier removal, no per-token pooling; all five individual values reported.

## Sham real-runtime equivalence (frozen)

Per budget, A vs B TRACE_COUNT must be identical in: requested expert sequence where semantically
required, victim sequence, evicted expert sequence, resident-set trajectory, resident bytes after
every event, eviction total, final resident set and `output_token_digest`, with
`predictor_decisions > 0` for B. Divergence ⇒ `MECHANISM_INSUFFICIENT`, and A-vs-B timing is not
interpreted as isolated predictive overhead.

## Mechanism classification (frozen)

`MECHANISM_OBSERVABLE` only if all seven hold: 7/7 TRACE_COUNT admissible; 35/35 CELLs admissible;
sham/B retention trajectories identical at all three budgets; sham predictive activity > 0; traffic
measured and > 0 at ≥ 1 budget; precision criterion met at 4 GiB; all identities and acquisition
invariants hold. Otherwise `MECHANISM_INSUFFICIENT`, naming the failing conditions.

## Next-phase gate

Cost-model protocol design may be authorised only if `MECHANISM_OBSERVABLE` **and** the measured
observables support at least one prospective runtime-cost quantity that is available before a
retention decision (or derivable from history), not holdout-derived, not based on TRACE_COUNT
timing, not an unidentified causal claim, and measurable/calibratable on the real runtime.

## Claim boundaries

Allowed: measurable/bounded bookkeeping overhead; traffic present at budget X; A/B retention
trajectories equivalent; traffic and timing vary across budgets. Not allowed: I/O caused the timing
difference; policy overhead caused the R2 result; cost-aware retention will improve throughput;
R3 achieved runtime speedup.

## Failure and rerun rule

A failed/inadmissible launch may be rerun **once**, preserving configuration, repetition id,
decoding parameters, ordering slot and identities. A second failure marks the cell `INADMISSIBLE`:
no imputation, no replacement repetitions, no reordering.

## Not frozen here

Cost-aware policy, cost-model coefficients, primary R3 scientific endpoint, DEV selection rule,
EVAL rules, runtime benchmark endpoint.
