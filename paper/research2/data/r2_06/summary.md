# R2-06 run 2e075161f02443ff822279add6938ca4

**Outcome:** PASS

**Scope:** offline DESCRIPTIVE CHARACTERIZATION of SAME-LAYER token-to-token expert-routing transition structure on the validated R2-01 trace -- no new replay, no model/runtime invocation, no new trace capture, no predictor, no accuracy claim, no reuse-distance or temporal-drift measurement, no cross-layer primary claim

**Transition unit:** One counted transition observation = one (source_expert_id, target_expert_id) pair contributed by one pair of CONSECUTIVE entries yielded by Trace.token_selections(layer, phase) for one layer (token-to-token, SAME layer; 'consecutive' = consecutive in the iterator's own yielded order, not consecutive token_index). Multi-expert selections are expanded via pairwise set-to-set counting: every expert i in the source selection paired with every expert j in the target selection, each pair counted once with weight 1 (never deduplicated). Cross-layer transitions are excluded from this unit entirely; layer visitation order is architectural, not a routing-structure transition.

## Reasons

- R2-01 PASS and R2-05 PASS verified; trace identity re-verified byte-identical on disk; expert identity treated layer-locally throughout; transition counts reconciled exactly against an independently-coded second computation; conditional probability mass per source expert sums to 1.0 within tolerance; the minimum transition support threshold was predeclared before this run and applied only to headline/strong-transition summaries, never to raw per-cell evidence; two independent syntheses over the identical trace, identical R2-05 bridge evidence, and identical null-baseline seed/config produced an identical canonical digest; no forbidden claim (predictor accuracy, reuse distance, temporal drift, cross-layer primary evidence, assumed self-transition dominance) was generated.

## Headline

- **total_transition_observations**: 1637312
- **layer_count**: 48
- **nominal_experts_per_layer**: 128
- **median_nmi**: 0.0989325510804687
- **min_nmi**: 0.08257545058595259
- **max_nmi**: 0.13678327969122506
- **fraction_layers_with_nmi_positive**: 1.0
- **median_conditional_entropy**: 3.735329608094245
- **median_self_transition_lift**: 4.18503995784333
- **median_top_1_outgoing_probability**: 0.0903966894977169
- **median_outgoing_effective_target_fraction**: 0.7169950308335794
- **max_supported_transition_lift**: 31.90476190476191
- **max_supported_transition_lift_layer**: 37
- **strongest_layer_by_nmi**: 37
- **weakest_layer_by_nmi**: 45
- **bridge_all_layers_compatible**: True