# R3-04 final claim boundaries

Scope of the R3-04 result (frozen with the final evidence).

1. **Prediction quality** — not tested here. R3-04 does not re-measure predictor accuracy; earlier frozen R2/R3 results stand unchanged.
2. **Retention mechanism** — at 1.5 GiB the predicted-protect policy (P) shows no resolvable wall-time benefit over the sham-protect control (S): P−S is small, its CI contains 0, and its direction is slower. Gate 2 replay bounded the possible wall benefit at ≤0.57 ms/token at 1.5 GiB and 0 at 8 GiB. Causality of any residual P−S difference is not claimed.
3. **Bookkeeping cost** — S−A is a measured wall cost of the predictor/bookkeeping path in this build, partly decomposed by policy-timing counters (R3-03B Fieller ratio). At 1.5 GiB the ratio is INCONCLUSIVE; at 8 GiB it is EXPLAINED.
4. **Net throughput** — classification is exactly the frozen rule on P−A at 1.5 GiB (mean of 18 paired block contrasts, t 95% CI). It is a statement about this runtime, not a prediction for other builds.
5. **Budget scope** — primary result is 1.5 GiB only; 8 GiB is a descriptive negative control and cannot override it. 4 GiB was dropped by the frozen efficiency amendment and is untested.
6. **Tested model/runtime scope** — one model file, one frozen llama.cpp runtime build, one prompt/512-token decode, one host. No claim about other models, quantisations, prompts, runtimes, hardware or concurrent load.
7. **Generalisation** — none beyond (6).
8. **Historical HoPi comparison** — not made. Throughput must not be compared to historical HoPi figures unless configuration identity is proven; that is a separate gate with its own frozen protocol.
9. **Clean-environment attestation** — required by the driver at launch but not persisted; it cannot be independently re-proved from the artifacts.
