# R2-07 run af28e8c6dc5044a18e4a86137d59156a

**Outcome:** PASS

**Scope:** offline DESCRIPTIVE CHARACTERIZATION of layer-local expert reuse locality and working-set growth on the validated R2-01 trace -- no new replay, no model/runtime invocation, no new trace capture, no predictor, no accuracy claim, no retention-policy performance claim, no temporal-drift or cross-workload claim

**Temporal unit:** One POSITION = one (token_index, selection) entry yielded, in order, by Trace.token_selections(layer, phase) for one layer; positions are numbered 0, 1, 2, ... in that yielded order (never by raw token_index). A selection is an unordered SET of concurrently selected experts: no artificial within-token ordering is ever constructed.

## Reasons

- R2-01 PASS, R2-04/II-A PASS, R2-05 PASS, and R2-06 PASS verified; trace identity re-verified byte-identical on disk; layer-local expert identity preserved throughout; reuse accounting reconciled exactly against an independently-coded second computation (cold + reuse + duplicate-within-position == raw total occurrences); working-set windows computed deterministically with full-windows-only semantics; null-baseline invariants (token count, per-token cardinality, total occurrences, per-expert marginal counts) verified preserved; two independent syntheses over the identical trace, identical R2-05/R2-06 bridge evidence, and identical null-baseline seed/config produced an identical canonical digest; no forbidden claim (predictor accuracy, retention-policy performance, temporal drift, cross-workload generalization, R2-06-already-proved-long-range-locality) was generated.

## Headline

- **total_reuse_events**: 199784
- **total_first_observations**: 5264
- **layer_count**: 48
- **nominal_experts_per_layer**: 128
- **median_immediate_reuse_fraction**: 0.42561606101711047
- **median_reuse_within_4_fraction**: 0.6415319972958208
- **median_reuse_within_16_fraction**: 0.8602290781936344
- **median_token_reuse_gap**: 2.0
- **median_p90_token_reuse_gap**: 24.0
- **median_distinct_intervening_reuse_distance**: 8.0
- **median_working_set_size_by_window**: {8: 30.0, 32: 57.0, 128: 89.5, 256: 102.5}
- **observed_vs_null**: {'immediate_reuse_fraction': {'median_observed': 0.42561606101711047, 'median_null': 0.1872837920856933, 'ratio_observed_to_null': 2.272572849349218, 'difference_observed_minus_null': 0.23833226893141718}, 'reuse_within_4_fraction': {'median_observed': 0.6415319972958208, 'median_null': 0.4780377165808436, 'ratio_observed_to_null': 1.3420112577818486, 'difference_observed_minus_null': 0.16349428071497718}, 'reuse_within_16_fraction': {'median_observed': 0.8602290781936344, 'median_null': 0.8207671793744822, 'ratio_observed_to_null': 1.048079284614215, 'difference_observed_minus_null': 0.039461898819152186}, 'median_token_reuse_gap': {'median_observed': 2.0, 'median_null': 5.0, 'ratio_observed_to_null': 0.4, 'difference_observed_minus_null': -3.0}, 'p90_token_reuse_gap': {'median_observed': 24.0, 'median_null': 26.0, 'ratio_observed_to_null': 0.9230769230769231, 'difference_observed_minus_null': -2.0}, 'median_distinct_intervening_reuse_distance': {'median_observed': 8.0, 'median_null': 24.0, 'ratio_observed_to_null': 0.3333333333333333, 'difference_observed_minus_null': -16.0}, 'working_set_size': {8: {'median_observed': 30.0, 'median_null': 39.0, 'ratio_observed_to_null': 0.7692307692307693, 'difference_observed_minus_null': -9.0}, 32: {'median_observed': 57.0, 'median_null': 73.5, 'ratio_observed_to_null': 0.7755102040816326, 'difference_observed_minus_null': -16.5}, 128: {'median_observed': 89.5, 'median_null': 97.0, 'ratio_observed_to_null': 0.9226804123711341, 'difference_observed_minus_null': -7.5}, 256: {'median_observed': 102.5, 'median_null': 105.5, 'ratio_observed_to_null': 0.9715639810426541, 'difference_observed_minus_null': -3.0}}}
- **shortest_median_reuse_gap_layer**: 6
- **longest_median_reuse_gap_layer**: 0
- **frequency_vs_reuse_gap_spearman_correlation**: -0.6506356646901621