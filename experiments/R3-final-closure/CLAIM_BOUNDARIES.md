# Research 3 claim boundaries

## Strongest defensible claim
Predictive structure in MoE expert routing does not necessarily translate into economically useful retention control. In the tested regime, correcting semantic policy-event placement, isolating predictor cost, and replacing the original predicted_protect mechanism with a causal next-use predictive eviction policy still failed a prospectively defined viability threshold before candidate computation cost was charged.

## Tested regime
Qwen3-30B-A3B Q4_K_M on the tested Windows host and runtime build; the R3-04 workload stream (512 decode tokens, seed 42); 1.5 GiB primary expert budget (8 GiB control is descriptive; 4 GiB not estimable for the economic conversion); byte-to-time conversion kappa_low (a halved two-budget association, not a measured causal rate).

## Research 3 DOES show, within that regime
1. Expert usage contains predictive structure (Research 2, still valid).
2. Prediction quality alone does not imply runtime value.
3. Semantic placement of policy updates matters (R3-03A: 144 vs 48 policy calls per decode token).
4. Predictor/bookkeeping cost can dominate the retention value recovered (R3-03B, R3-04).
5. The original predicted_protect mechanism captures almost none of the available oracle headroom (0.230%), less than a zero-cost current-event shield (0.559%).
6. A causal next-use predictive eviction rule substantially improves mechanism value relative to current PP (NUE_X 8.79%).
7. Even that improved predictive family captures less than 9% of the Belady byte headroom.
8. Its conservative gross value (8.11 ms/token) fails the prospectively frozen 10 ms/token threshold before computation cost is charged.

## Research 3 does NOT show (explicitly disclaimed)
- expert usage is unpredictable
- Research 2 was wrong
- all retention policies are impossible
- Belady headroom is causally reachable
- all memory-management approaches fail
- all predictive caching fails universally
- other models or hardware would behave identically

## Belady interpretation
The oracle is about 40.7% below the baseline in misses. That oracle uses future information. Research 3 did not establish how much of the gap is causally recoverable; that question is out of scope for Research 3. The 40.7% figure must not be read as attainable.

## Universal claims
None allowed. All results are bounded to the tested regime above.
