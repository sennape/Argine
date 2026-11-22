# R3-05 R1 claim boundaries

1. R1 establishes only that the frozen R3-03A **request stream** (pass, layer, bytes_of_layer, requested_experts per semantic event) can be extracted deterministically and replayed by the frozen Python reference so that the Gate 2 1.5 GiB decode and all-pass counters of A (b_selected) and PP (predicted_protect) are reproduced **integer-exactly**.
2. The stream comes from the R3-03A runtime build; the Gate 2 counters come from the R3-04 mechanism runs (a different, timing-instrumented build). Exact agreement of every counter is the evidence that the request stream is policy- and build-independent for this workload. It is not an independent re-proof of the runtime.
3. Scope: one model, one prompt (r2-11-coding-v1, seed 42, greedy, 512 decode tokens), one host. The stream is routing-determined; it is not claimed to generalise to other prompts.
4. Equality across traces covers the semantic request stream only. Residency, scores, hit/miss/evict decisions and protected sets differ by policy and are excluded by definition.
5. R1 evaluates no candidate, measures no clairvoyant headroom, and makes no wall-time or throughput statement. Phase 0 and R3-04 remain unmodified.
6. Absolute host paths are not committed; sources are bound by size and SHA-256 to the frozen R3-03A evidence.
