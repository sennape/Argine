# Research 2 Final Closure

**Status: RESEARCH 2 CLOSED.**

## Milestone statuses

| Milestone | Status |
|---|---|
| II-A (R2-01..R2-04) | CLOSED / PASS / PAPER-FROZEN |
| II-B (R2-05..R2-08) | CLOSED / PASS / PAPER-FROZEN |
| R2-09A/B | CLOSED |
| R2-10 | CLOSED / Category B supported by held-out EVAL |
| R2-BRIDGE-01 | CLOSED / BRIDGE_POSITIVE |
| R2-11 | CLOSED / R2_11_PASS (72/72 cells) |
| R2-12 | CLOSED / **R2_12_FAIL** (primary 4 GiB classification) |

No downstream negative runtime result retroactively alters any earlier evidence classification. The historical statement "R2-11 NOT PROPOSED under the original frozen R2-10 decision rules" remains true and unchanged.

## Central scientific conclusion

Predictive expert information is measurably useful for forecasting routing and can improve a replay-level retention metric, but the tested reactive predictive-retention realization does not convert that information into improved end-to-end decode throughput on the evaluated runtime configuration.

## Supporting documents

- Milestone table, claim ledger, reviewer audit, literature positioning: `audits/RESEARCH-2-FINAL-CLOSURE-REVIEW/`
- R2-12 closure: `experiments/R2-12-runtime-validation/R2_12_FINAL_CLOSURE.json`
- Paper II provenance + freeze: `paper/research2/PAPER_II_PROVENANCE.{json,md}`, `paper/research2/PAPER_II_FREEZE.json`
- Manuscript: `paper/research2/manuscript.pdf`

## Unresolved question inherited by future Research 3 (not started here)

How can useful predictive expert information be converted into end-to-end runtime value when reactive predictive retention alone fails to improve throughput? Possible directions (cost-aware retention, proactive expert movement, prefetch, compute/I/O overlap, scheduling, adaptive allocation) are mentioned cautiously as future-work possibilities only — none is preregistered or implemented by this closure.
