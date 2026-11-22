# Claim boundaries — R3-05 rung R4
- Offline, model-free replay. No model, no llama-server, no runtime change, no microbenchmark; policy cost is assumed zero.
- Values use kappa_low (a two-budget 1.5<->8 GiB association, halved) and are replay-to-wall conversions, not measured runtime gains.
- DEV variants were indistinguishable (tie), so the selection rests on the pre-registered tie-break; DEV evidence therefore says nothing about LEX vs SOFT.
- A K4 fail kills the NUE family as specified here; it does not prove that no other retention mechanism could capture Belady headroom.
- Single EVAL stream, one budget, deterministic replay: no confidence intervals.
