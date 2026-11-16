# Research 2 Final Claim Ledger (Human-Readable)

Machine-readable form: `CLAIM_LEDGER.json` (same directory). Statuses: `SUPPORTED`, `SUPPORTED_WITH_SCOPE`, `DESCRIPTIVE_ONLY`, `HYPOTHESIS_ONLY`, `NOT_SUPPORTED`, `HISTORICAL_ONLY`.

## SUPPORTED / SUPPORTED_WITH_SCOPE

| ID | Claim | Status |
|---|---|---|
| C-SUP-1 | `decayed_transition` improves held-out prediction quality over trivial baselines (EVAL h=1: +12.04% vs `last_observation`, +28.71% vs `global_frequency`). | SUPPORTED_WITH_SCOPE |
| C-SUP-2 | Predictive routing information contains exploitable structure beyond trivial baselines. | SUPPORTED_WITH_SCOPE |
| C-SUP-3 | Replay-level retention (`miss_bytes_per_decode_step`) improves under `PredictedProtect` at the primary 4 GiB budget (R2-11: `R2_11_PASS`, CI entirely below zero). | SUPPORTED_WITH_SCOPE |
| C-SUP-4 | The tested real-runtime realization does NOT improve decode throughput at the primary 4 GiB budget (delta -0.171721 TPS, CI entirely ≤0). | SUPPORTED |
| C-SUP-5 | R2-12's primary result is negative (`R2_12_FAIL`). | SUPPORTED |

## NOT_SUPPORTED / HYPOTHESIS_ONLY (explicitly prohibited claims)

| ID | Disclaimed statement | Status |
|---|---|---|
| C-NOT-1 | Predictive retention universally fails. | NOT_SUPPORTED |
| C-NOT-2 | The R2-10 predictor is useless. | NOT_SUPPORTED |
| C-NOT-3 | The R2-11 replay benefit was invalid. | NOT_SUPPORTED |
| C-NOT-4 | Policy overhead is proven to cause the R2-12 runtime loss. | HYPOTHESIS_ONLY |
| C-NOT-5 | The 8 GiB loss proves a monotonic memory-budget effect. | NOT_SUPPORTED |
| C-NOT-6 | Increasing memory necessarily increases predictive-policy overhead. | NOT_SUPPORTED |
| C-NOT-7 | R2-12 proves predictive prefetch would fail. | NOT_SUPPORTED |
| C-NOT-8 | R2-12 proves no predictive runtime optimization can succeed. | NOT_SUPPORTED |

## HISTORICAL_ONLY

| ID | Statement | Note |
|---|---|---|
| C-HIST-1 | "R2-11 NOT PROPOSED under the original frozen R2-10 decision rules." | Remains true and unchanged; R2-BRIDGE-01 is prospective evidence under a separate, later contract that justified *proposing* R2-11 afresh — it does not rewrite this historical statement. |

Full evidence citations (artifact paths, commits, statistical support) are in `CLAIM_LEDGER.json`.
