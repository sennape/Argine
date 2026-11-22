# R3-03B claim boundaries

## Supported
- The R2 predictor-quality result remains valid.
- The R2 replay/bridge results remain valid.
- R2-12 measured the old realized runtime validly (old sham-minus-b_selected overhead ~57.4 ms/token, 5 launches per arm).
- The old runtime event semantics were not equivalent to R2 replay semantics.
- R3-03A corrected the semantic event stream (24,672 events/launch, frozen token digest reproduced in all 80 launches).
- R3-03B measures policy bookkeeping time directly (ARGINE_POLICY_TIMING, schema r3-03b-v1).
- Under the frozen rule the corrected overhead is quantitatively explained by measured policy timing:
  rho = mean(O)/mean(P) with a bounded 95% Fieller CI entirely inside [0.5, 2.0].

## Not supported
- Generalization beyond the tested implementation, configuration, host, model and 8 GiB setting.
- "Always 17.47 ms/token": that is one measured mean on this configuration, not a constant.
- The microbenchmark equalling the in-situ cost (in-situ P is ~3.26x the microbench figure; mechanism untested).
- predicted_protect improving throughput (no such comparison was made).
- R2-12 having been invalid.
- Causal attribution beyond the tested implementation and configuration.
- Any cause for the ~300 s server exit time (undetermined).
- The "preflight RAM-feasibility failure" statement: no artifact supports it; the artifacts show a launch-005 shutdown stall and operator interrupt.
