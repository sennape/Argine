# Research 5 Phase 0 — independent adversarial review

An independent review agent received the draft protocol and attempted to break it. It was asked to look for:
- outcome-dependent flexibility and hidden tuning;
- leakage, unfair baselines, weak endpoints and pairing errors;
- unmeasurable mechanism claims and prediction/scheduling ambiguity;
- oracle misuse, weak falsification and rhetorical rescue paths.

It reported 8 material findings, 7 minor ones and 4 notes. All material and minor findings were resolved before the freeze, as summarised below. The draft's `state` field already read FROZEN when it was reviewed (note 18). The freeze recorded in `FREEZE.json` was computed only after these resolutions.

| # | severity | finding | resolution in the frozen contract |
|---|---|---|---|
| 1 | material | Hook time can fall because C's later nodes touch instead of re-acquire, with no overlap at all. | Added bookkeeping control C0_I (C's code path, A's wait placement). G4 now tests exposed **wait** of C_I against C0_I and requires wait reduction to explain ≥ 50% of the drop in hook time against B. |
| 2 | material | "hidden fraction > 0" is trivially met. | Floor of 0.25 of deferred bytes; G4 unit and df (12 blocks, df = 11) stated. |
| 3 | material | Per-node times are undefined across threads. | Critical-path rule: node time = last thread out minus first thread in; wait = maximum over threads; T_tail in H uses the latest completion over threads. |
| 4 | material | Tracing is asymmetric between B and C_I. | B, C0_I and C_I write identical record types and fields; per-arm trace overhead is reported on development prompts. |
| 5 | material | The headroom gate can be recomputed until it passes. | H is computed once from 8 pre-declared B launches on the frozen binary; every H ever computed is reported. |
| 6 | material | Implementation choices act as hidden tuning parameters. | IMPLEMENTATION_FREEZE lists the wait primitive, handle-release point, barrier placement, pin-release point, touch order and trace layout. Every development timing comparison goes in a committed log. No change after the freeze. |
| 7 | material | Failure blame is decided by judgment, and INVALID is not terminal. | Mechanical blame rules with a closed list of documented infrastructure causes. C correctness failures are never rerun. R5_INVALID is final. |
| 8 | material | An early end-of-sequence would invalidate the campaign by design. | `ignore_eos = true`. |
| 9 | material | The no-harm gate is underpowered and would turn real positives into "not robust". | 4 GiB raised to 20 blocks. The margin is aligned with the MEI at 2%. A harmless C passes with probability ≈ 0.99 under the planning SD. |
| 10 | minor | The planning SD comes from the slower blocco path. | Stated as a power risk. Power at a true 3% effect is ≈ 0.54. Block counts are fixed and not data-dependent. The development A-vs-A noise estimate is informational only. |
| 11 | minor | Baseline fidelity is unchecked. | G1 requires A on the R5 build to give tokens identical to the pre-R5 runtime and to be no more than 2% slower. |
| 12 | minor | Purity is checked only in traced runs. | An end-of-run counter record is written in every launch. G7 applies to both timing campaigns. One 1% definition. |
| 13 | minor | Prompt leakage has a loophole. | No binary of any kind may run evaluation prompts before opening. Tracked references at the source HEAD are recorded (only the workload file). |
| 14 | minor | "Issue order" is ambiguous. | A's exact order is frozen in COMPARATORS.json, with a model-free sequence test required in G1. |
| 15 | minor | Host-condition controls are thin. | Per-launch host record (power, plan, CPU and NVMe temperature, Defender and Update state); scans and updates paused; AC required; no data-driven exclusion. |
| 16 | note | The MEI justification cited drift that a paired design cancels. | Rewritten: practical relevance plus instrumentation scale and expected precision. |
| 17 | note | H is loose. | Stated in COMPARATORS.json and ENDPOINTS.json; H is used only as an opening gate. |
| 18 | note | The draft state was already FROZEN while under review. | Recorded here; the freeze digest was computed after the resolutions. |
| 19 | note | "Mechanism present" labels could be spun as success. | CLAIM_BOUNDARIES forbids partial-success wording; non-hook time per token is always reported. |

The reviewer's overall verdict was that the protocol, after the material fixes, is a clean and falsifiable test. The intervention is causal rather than predictive. Its residual risk is mainly loss of power, not a false positive.
