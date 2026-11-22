# NUE definitions (Phase 0, pre-registered; implemented before any EVAL replay)
Common: b_selected (decay 0.999, aging interval 1) + current-event shield (CES): residents that are members of the event being served are never victims (total fallback if no other candidate).
in_l(e) = 1 iff expert e is in layer l's predicted (NUE_X: decayed_transition, decay 0.99, top_k 8) or last-observed (NUE_LO) set for l's NEXT event, computed after l's most recent event. Each resident is ranked by ITS OWN layer's prediction, in every eviction (not only during that layer's own event, unlike the retired PP).
- NUE_X_LEX:   victim = min (in_l, aged_score, last_ref, layer, expert)
- NUE_X_SOFT1/SOFT4: victim = min (aged_score + lambda*in_l, last_ref, layer, expert), lambda in {1,4}
- NUE_LO: the selected variant's key, in_l from the layer's last observed event.
No-information predictor fallback (frozen predictor returns expert 0) is part of the frozen semantics.
## Pre-registered choices
- DEV = frozen R2-09A DEV seed-42 traces (coding, instruction, reasoning, summarization) at 1.5 GiB, read-only, sha256-verified. EVAL = canonical R3-04 decode stream. No other split.
- Selection: max total decode miss bytes avoided vs A over the 4 DEV traces; tie-break = Phase 0 variant order LEX < SOFT1 < SOFT4. Locked in CANDIDATE_SELECTION.json before NUE_LO DEV and before any EVAL replay.
- K4: gross value = kappa_low (0.3792 ms/MB) x EVAL bytes avoided vs A per token; < 10 ms/token (exactly 10 passes) => infeasible. Pass also needs positive DEV bytes avoided.
- K5: LO bytes avoided >= 0.8 x X bytes avoided, at zero cost (net = benefit).
- Mechanism labels: R3_05_NUE_MECHANISM_PASS if any of NUE_LO/NUE_X passes K4, else R3_05_PREDICTIVE_RETENTION_KILLED.
