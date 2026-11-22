# Research 3 — final scientific closure

**Status:** CLOSED. **Classification:** `R3_CLOSED_NEGATIVE_PREDICTIVE_RETENTION_NOT_VIABLE_IN_TESTED_REGIME`.

**Scope (unchanged):** whether the predictive expert information established in Research 2 can be converted into useful real-runtime retention value. Research 3 is not, and is not retrospectively redefined as, a general search over memory-management policies.

## Evidence arc
| Stage | Result | What it established |
|---|---|---|
| R3-03A | PASS | The old runtime issued 144 semantic policy updates per decode token where the frozen research semantics require 48 (3x). Corrected to one update per (pass, layer); physical expert-store ensures stay separate. Corrected runtime conformed. |
| R3-03B | R3_03B_EXPLAINED | Direct policy bookkeeping accounts for the corrected sham-over-baseline overhead (rho 0.9024, 95% Fieller CI [0.876, 0.929] inside the frozen [0.5, 2.0]). Scope: this runtime, host and configuration. |
| R3-04 | R3_04_CLOSED_NET_LOSS | 1.5 GiB, 18 paired blocks: A 526.97, S 541.56, P 542.80 ms/token; P-A +15.827 ms/token (95% CI [10.46, 21.19]); throughput -2.93%. |
| R3-05 R1 | R3_05_R1_FIDELITY_PASS | The offline canonical stream reproduces the real Gate 2 counters integer-exactly. |
| R3-05 R2 | R3_05_K1_HEADROOM_PASS | Belady-style oracle: 207.990 -> 123.395 misses/token (-40.673% misses, -40.653% bytes); conservative ceiling 92.216 ms/token. |
| R3-05 R3 | R3_05_K2_RETIRE_CURRENT_PP | A zero-cost current-event shield (CES) captures 0.559% of the oracle byte headroom, current PP 0.230%: PP's effect was not predictive. K3 formally SURVIVES but cannot rescue PP. |
| R3-05 R4 | R3_05_PREDICTIVE_RETENTION_KILLED | NUE_LO 4.78% / 4.41 ms/token, NUE_X 8.79% / 8.11 ms/token, both below the prospectively frozen K4 floor of 10 ms/token at zero policy cost. |

## Central answer
Within the tested regime, no: corrected predictive retention lost throughput at runtime, and the best causal predictive retention family failed the prospectively frozen viability threshold even before its computation cost was charged.

## Files
`FINAL_RESULT.json` (classification, claim, statuses), `EVIDENCE_CHAIN.json` (every bound freeze, commit and key number), `RESEARCH_3_CLOSURE.json` (closure record, untested directions, roadmap, R4 handoff), `CLAIM_BOUNDARIES.md`, `UNTESTED_DIRECTIONS.md`, `ROADMAP_IMPACT.md`, `FREEZE.json`.

Verify: `python closure.py verify` (offline; re-checks each freeze against its commit, recomputes the key numbers from the frozen artifacts, and runs the R3-05 validator chain).
