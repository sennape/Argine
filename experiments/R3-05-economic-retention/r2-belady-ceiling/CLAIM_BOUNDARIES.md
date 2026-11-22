# R3-05 R2 claim boundaries

1. R2 measures a **ceiling** on one frozen stream (r2-11-coding-v1, seed 42, greedy, 512 decode tokens). It evaluates no candidate; it says nothing about whether NUE_X, CES or any causal policy captures any of it.
2. `R3_05_K1_HEADROOM_PASS` means only: enough theoretical future-retention value exists on this stream to justify testing causal candidates. It does not claim any causal policy can reach it, nor that a wall-time win is achievable.
3. The ceiling is **Belady-style** (the historical `BeladyGlobal`): not proven optimal for multi-key atomic events with slightly unequal layer sizes; non-causal; not deployable.
4. Conversion to ms/token uses the frozen Phase 0 conservative kappa (half of a two-budget **association** between b_selected wall time and decode miss bytes, 1.5 vs 8 GiB). It is not a causal per-miss cost. It is applied only at budgets where Phase 0 supports it (1.5 and 8 GiB); **4 GiB is NOT_ESTIMABLE** (no measured wall time there; any linear value is descriptive and unvalidated). Secondary budgets cannot override the primary 1.5 GiB gate.
5. The headroom is measured against b_selected's replayed misses. The A-vs-Belady gap includes a gap that any causal policy would have to close with a prediction of the future; PP's own capture of this headroom is 0.23% (101 of 43,313 misses), which is a statement about the current mechanism only.
6. Historical comparison is informational: the R2-03/R2-09A prior came from a different generation (381 decode steps); the Belady logic is the same code.
7. Scope: one model, one runtime build lineage, one host, one prompt; no HoPi comparison; no generalisation. Phase 0, R1 and all R3-04 evidence are unmodified.
