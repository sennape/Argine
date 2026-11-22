# R3-04 — Net runtime value of predicted retention (protocol + tooling preparation)

Status: **R3_04_PROTOCOL_FROZEN**. Nothing was acquired; no model-bearing command was run; the acquisition is **not** open.

Question: does the retention benefit of the REAL `predicted_protect` policy recover its own bookkeeping cost and beat `b_selected` in corrected-runtime decode wall ms/token?
Design: three arms A/S/P, 6-launch palindromic blocks (order-balanced over the 6 permutations), 1 warmup per budget, primary 1.5 GiB (N = 18 blocks = 108 launches), 4 GiB confirmatory and 8 GiB negative control (N = 6 each, descriptive), 180 counted timing launches + 3 warmups, plus a separate 10-launch trace-enabled mechanism/conformance phase. Reused runtime `8d2e1def...`.

Files: PROTOCOL.json, ESTIMANDS.md, BUDGET_SELECTION.json, SAMPLE_SIZE.json, SCHEDULE.json, INSTRUMENTATION_PLAN.md, SHUTDOWN_PLAN.md, CLAIM_BOUNDARIES.md, FREEZE.json, validate_protocol.py (estimators, schedule, power), build_artifacts.py (regenerates the JSON from frozen parents), tests/.
Gates still ahead (each needs explicit user authorisation): (1) model-free shutdown probe; (2) build nothing (runtime reused) -> mechanism phase; (3) opening freeze of the timing campaign; (4) timing acquisition.
