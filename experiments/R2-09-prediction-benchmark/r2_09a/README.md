# R2-09A — Causal Baseline Tournament (DEV-only)

- Outcome: **R2_09A_PASS**
- R2-09B contract: v1.1 `b6274b354e39854effcca1510f0f817a85a162ab80cb6d29a31041a9978a7b28`
- DEV corpus digest: `f758b77492eebc38bc81ba22d6b06ab8f19138d1099b635821369a4d022d9398` (8 instances)
- Tournament digest: `5b600d1451983dabafdfbd6ea6bfdc4c4c3bdcf9327c9ec4d57c25025759f847`
- Cells: 624/624
- Deterministic: True, budget_violations: 0

## B_selected(M) (DEV-only, mechanical)
- 1.5 GiB: `aged_frequency` / `aged_frequency/decay=0.999,interval=1` | Score=599967231.392514 | tie_break=none | Δvs GLRU=-39209363.196942925 | Δvs LFU=-32016200.050171852
- 4 GiB: `aged_frequency` / `aged_frequency/decay=0.99,interval=64` | Score=238400316.389602 | tie_break=none | Δvs GLRU=-20967144.08320877 | Δvs LFU=-33138866.443009704
- 8 GiB: `aged_frequency` / `aged_frequency/decay=0.999,interval=16` | Score=43221096.976686 | tie_break=none | Δvs GLRU=-4061973.7629782557 | Δvs LFU=-11446777.64901305

## Execution hardening
- Checkpoint/resume hardened after incomplete in-memory runs (PID 8532, PID 11208 terminated incomplete; no durable checkpoints in old runner). Frozen scientific semantics/corpus/params/metrics/selection unchanged.

## Gates
- EVAL_LOCKED, specs visible but not used for engineering. No R2-10/11/12. PENDING_POLICY_STREAM_CALIBRATION. STOCHASTIC_PROFILE_UNRESOLVED. CASE C binding.

## Provenance correction
- PHASE_0_PROVENANCE_CORRECTION (NO_SCIENTIFIC_CHANGE_REPORTING_ERROR): earlier reported digest 18158e... superseded by authoritative 758b77...; coding-v1/seed-43 decode_steps corrected in reporting 380→381 (verified 388-7-0=381); historical manifest bytes unrecoverable; no persisted evidence used denominator 380; scientific corpus/semantics unchanged; 240/240 checkpoint cells valid on rescan.

## Closure-test completion correction
- Initial closure attempt: 	ests/test_r2_09a_baseline_semantics.py and 	ests/test_r2_09a_tournament.py timed out in the execution environment (gate not fully satisfied at that point).
- Subsequent explicit rerun: baseline_semantics **PASS** (5 passed, 0 failed, 0 skipped, no timeout, no heavy canonical replay recompute); tournament **PASS** (5 passed, 0 failed, 0 skipped, no timeout, heavy canonical replay recomputed/verified, ~1230s).
- No scientific result, tournament cell, B_selected value, corpus identity, or policy configuration changed.
