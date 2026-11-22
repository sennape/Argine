# R3-04 claim boundaries

## Status of earlier results (unchanged)
R2 predictor quality, R2-BRIDGE and R2-11 replay retention are valid. R2-12 validly measured the **old** runtime (negative throughput). The old runtime issued ~144 policy events/token versus 48 semantic events/token now, so R2-12 is **not** reinterpreted and **not** called invalid; R3-04 tests the corrected runtime prospectively. R3-03B explained the corrected S - A overhead (rho 0.9024, CI [0.876, 0.929]) at 8 GiB only.

## The five outcomes R3-04 can separate
1. P improves retention but does not recover its cost: mechanism phase shows fewer decode miss bytes for P than A/S and primary = NET_LOSS (Q1).
2. P recovers some cost but stays slower: NET_LOSS and DELTA_RETENTION_ADJ CI entirely < 0 (Q2).
3. Approximate break-even: INCONCLUSIVE with CI half-width <= 5.82 ms/token (Q3), bounded, never "equal".
4. Net win: NET_WIN (Q4).
5. No reliable conclusion at the available precision (Q5).
Better retention is never reported as better throughput; the two come from different phases and different endpoints.

## What can never be claimed from R3-04
* Generalisation beyond: this model, this single workload (r2-11-coding-v1, seed 42, 512 tokens), this machine, this runtime build, thread count and context, and the three tested budgets.
* A result at an untested budget; a causal speedup; that the in-situ cost equals the microbenchmark cost.
* A NET_LOSS at 1.5 GiB proves nothing about other budgets or other predictors/implementations; a NET_WIN would not transfer either.
* That mechanism counters measured in the 10 traced launches were observed in every timing launch: they are tied to timing launches by determinism (digest, calls, victim_calls), not measured there.
* Any statement about HoPi or original Argine (see below).

## Prior-evidence use
Replay retention deltas: budget selection and a prior upper bound only (<= ~0.57 ms/token at 1.5 GiB, <= ~0.11 at 4 GiB, 0 at 8 GiB under a generous wall-proportional-to-miss-bytes model, versus a ~17.5 ms/token cost). R2-12 SDs: conservative planning only. No historical outcome is a fresh observation.
Expectation recorded **before** acquisition (not a decision input): NET_LOSS is expected; the experiment's value is to resolve it quantitatively and to separate cost from retention benefit.

## Future HoPi gate (Part R; NOT run, NOT part of R3-04)
A matched comparison of original Argine/HoPi versus the final corrected predicted Argine needs, identical: model file and quantisation; expert store and budget (bytes); thread count; context size and batch settings; prompt/workload, seed and decoding; measurement procedure (decode wall ms/token from server timings, same warmup/cooldown/ABBA-style order, same cooldown and shutdown handling, same environment attestation); runtime build identities recorded for both; per-arm policy timing/mechanism counters where available. It would be its own frozen protocol, with the endpoint chosen before acquisition, and cannot reuse R3-04 numbers as observations.
