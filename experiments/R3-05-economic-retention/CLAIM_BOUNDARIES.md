# R3-05 Phase 0 claim boundaries

1. Phase 0 makes **no performance claim**. It is a design decision plus a pre-registered protocol. No candidate has been evaluated.
2. The economic envelope is **derived** from frozen R3-04, Gate 2, R3-03B and R2 artifacts (hashed in FREEZE.json). `kappa` (ms of wall time per MB of decode miss bytes) is a two-budget association for b_selected, not a causal per-miss cost. Phase 1 halves it before any decision.
3. The clairvoyant (Belady) ceiling is a **prior** from the R2 coding-v1 trace. It is non-causal and not achievable by any causal policy. Phase 1 re-measures it on the R3-04 stream.
4. The diagnosis that predicted_protect's value is mostly intra-event self-eviction avoidance is a **hypothesis** (H1) from code reading. It is tested by the CES baseline, not asserted.
5. Prediction quality from R2 is not questioned or re-measured. "Prediction failed" is **not** a conclusion of R3-04 or R3-05 Phase 0.
6. Scope stays one model, one runtime build, one host, one prompt/512-token decode, the 1.5 GiB primary budget, and secondary 4/8 GiB replay only. No HoPi comparison.
7. Phase 0 authorizes **nothing model-bearing**. Runtime realization, conformance and benchmark gates each need their own approval.
8. R4 subject matter (gating, portfolios, adaptive control) is described, not evaluated.
