# Claim impact of the campaign-efficiency amendment (PRIMARY_PLUS_8G)

Unchanged: the primary question, endpoint (decode wall ms/token), arms, 1.5 GiB primary with N = 18 blocks (108 counted launches), the frozen estimator, alpha, NET_WIN / NET_LOSS / INCONCLUSIVE definitions, runtime identity, token digest.

Narrowed: R3-04 now has **two** timing budgets (1.5 primary, 8 descriptive negative control). The earlier boundary "the three tested budgets" becomes "the two tested budgets". Nothing can be said from R3-04 about **timing at 4 GiB** with the corrected runtime; R2-12's old-runtime 4 GiB result remains what it was (valid, old runtime, not reinterpreted). Gate 2 supplies mechanism counters (not timing) at 4 GiB.

Still never claimable: generalisation beyond this model/workload/machine/runtime/threads/context and the two tested budgets; any speedup from retention (better retention is not better throughput); anything about HoPi or original Argine.

Recoverable: a 4 GiB timing budget (1 warmup + 6 blocks, 37 launches, about 1.8 h) can be run later as its own additive, prospectively frozen protocol if a later milestone needs it.
