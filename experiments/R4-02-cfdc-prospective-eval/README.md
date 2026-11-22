# CFDC Prospective Evaluation and Final Classification (R4-02)

Research 4, Phase 2. The frozen EVAL corpus (the four canonical R2-09 EVAL traces) was opened once, after the opening record `EVAL_OPENED.json` was committed (commit 30e37fd, no metrics). Six frozen arms were scored once each per trace (24 units): A, BeladyGlobal, NUE_X, DECAYED_COUNT, CHEAP, RICH. The preregistered rules were applied mechanically; nothing frozen was changed.

## Result

**Final classification: `R4_CFDC_CAUSAL_STATE_INSUFFICIENT`. Research 4 CFDC is `R4_CFDC_CLOSED`; EVAL state `CLOSED`. No companion label applies.**

Pooled decode miss bytes at 1.5 GiB: A 1,226,910,965,760; BeladyGlobal 713,408,901,120; headroom 513,502,064,640.

| Arm | EVAL capture | VALIDATION capture |
|---|---|---|
| NUE_X | +9.37% | +7.42% |
| DECAYED_COUNT | +3.05% | -5.60% |
| CHEAP | -29.96% (153.8 GB more miss bytes than A) | -36.04% |
| RICH | +19.11% | +15.45% |

- Rule A fails (CHEAP VALIDATION and EVAL both below 25%).
- Rule B: required capture 25% (best comparator NUE_X at 9.37% + 3 pp is lower); CHEAP does not reach it.
- Rule C fails: CHEAP is negative on all four EVAL workloads (coding -33.1%, instruction -36.0%, reasoning -18.9%, summarization -31.6%).
- RICH gate fails: RICH is below 25% on both VALIDATION and EVAL.
- No comparator reaches 25% on both splits, so `COMPARATOR_REACHES_VIABILITY` does not apply.
- Non-gating: CHEAP beats the best comparator on 0 of 4 EVAL workloads. The 90% block-bootstrap interval for CHEAP is [-36.9%, -23.4%]; for RICH [16.4%, 21.9%].
- Not computed (report-only, non-gating): REPLICATE_CHECK seed-43 and the EVAL teacher-agreement shift diagnostic.

## Execution notes

- 24 (trace, arm) units; each completed unit was produced once and never recomputed. RICH replays ran in resumable chunks (3-4 chunks per trace); results are checkpoint-exact (tested).
- During RICH resumption two resume invocations were accidentally started concurrently. Both resumed from the same atomically-written checkpoints of a deterministic computation, so no completed result was duplicated or altered, but the per-unit chunk counts and seconds in `EVAL_RAW_RESULTS.json` may therefore include some redundant resumed work.
- No model inference was run, no runtime/C++ files were changed, and no raw trace was copied into the repository.

See `CLAIM_BOUNDARIES.md` for the bounded interpretation.
