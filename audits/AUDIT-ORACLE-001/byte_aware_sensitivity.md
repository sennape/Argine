# Byte-Aware Sensitivity Check (AUDIT-ORACLE-001)

## Scope

Offline, deterministic, falsificatory check. No model execution, no network, no trace mutation. Canonical trace: R2-01 `run-00-4G` (`e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1`), decode phase, semantics v2, budget 4 GiB (4294967296), same cache/object/next-use model as R2-02. Canonical O_B (`Oracle-Global-Belady`, `BeladyGlobal`): **14,754 misses, 42,296,352,768 miss bytes** (from frozen R2-02 `policy_results.json`).

Script: `byte_aware_sensitivity_check.py` (imports `argine.research.trace/oracle/replay`; deterministic).

## Comparators (clairvoyant heuristics)

1. **belady-size-tiebreak**: furthest next use; ties broken by evicting larger object (frees bytes). Uses full future next-use distances.
2. **belady-byte-density**: evict max(`next_use/size`) (keeps nearer next-use per byte); INF treated as most evictable; tie by larger size. Heuristic, not claimed optimal.

## Results

| Comparator | Misses | Miss bytes | Evictions | Peak resident bytes | Δ miss bytes vs O_B | Beats O_B miss bytes |
|---|---:|---:|---:|---:|---:|---|
| Canonical O_B (R2-02) | 14,754 | 42,296,352,768 | 23,065 | 4,294,950,912 | 0 | — |
| belady-size-tiebreak | 14,754 | 42,296,352,768 | 22,977 | 4,294,950,912 | 0 | No |
| belady-byte-density | 20,966 | 57,265,680,384 | 29,566 | 4,294,950,912 | +14,969,327,616 | No |

Machine-readable: `byte_aware_sensitivity.json`.

## Classification

**NO_COUNTEREXAMPLE_FOUND**

## Interpretation (falsificatory only)

No tested byte-aware clairvoyant heuristic beat O_B on canonical miss bytes. This does **not** establish that `BeladyGlobal` is byte-optimal, near-optimal, or within any epsilon of the true offline byte optimum `O*`. Only a proven optimal algorithm, a certified bound, or an exact optimum would establish optimality/bound quality. Conversely, if a future comparator beats O_B, that would be an empirical counterexample (`BELADY_BEATEN`) and would confirm O_B is not byte-optimal for the tested objective/trace.
