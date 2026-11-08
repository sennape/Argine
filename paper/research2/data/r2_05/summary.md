# R2-05 run da82e15b61c94019840477642618565a

**Outcome:** PASS

**Scope:** offline DESCRIPTIVE CHARACTERIZATION of layer-local expert routing frequency on the validated R2-01 trace -- no new replay, no model/runtime invocation, no new trace capture, no causal or predictability claim

**Request unit:** One counted expert request = one occurrence of one expert id within one token's selected-expert tuple (Trace.token_selections(layer, phase)), for one (layer_id, expert_id) pair. This is the token-expert ASSIGNMENT unit -- the router's raw per-token top-k selection -- not the cache-access/residency-event unit R2-01..R2-04 use (the per-step-per-layer union across tokens). Duplicate expert ids within one token's tuple, if any, are each counted separately: never deduplicated.

## Reasons

- R2-01 PASS and R2-04 PASS/II-A-closed verified; trace identity re-verified byte-identical on disk; expert identity treated layer-locally throughout; frequency counts reconciled exactly against an independently-coded second computation; probability mass per layer sums to 1.0 within tolerance; rank and tie-breaking deterministic; two independent syntheses over the identical trace produced an identical canonical digest; no forbidden claim (temporal stability, Zipfian/power-law without evidence, frequency-explains-Oracle-gap) was generated.

## Headline

- **total_routing_requests**: 205048
- **layer_count**: 48
- **nominal_experts_per_layer**: 128
- **top_k_recorded_in_trace_meta**: 8
- **median_normalized_entropy**: 0.8521530182050354
- **median_effective_expert_fraction**: 0.513554224151178
- **median_top_8_share**: 0.3000919373106534
- **min_top_8_share**: 0.15945065176908751
- **max_top_8_share**: 0.4073556797020484
- **most_concentrated_layer**: 31
- **least_concentrated_layer**: 0
- **experts_for_80pct_median**: 43.0
- **experts_for_80pct_min**: 31
- **experts_for_80pct_max**: 68