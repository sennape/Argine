"""R2-03's predeclared budget sweep (section "BUDGET SWEEP" of the task).

Predeclared BEFORE any real candidate analysis -- these values are fixed
module-level constants, never computed from or tuned to a result, and
never swept further at runtime (this module defines the one and only
sweep; nothing in orchestrator.py accepts a budget list parameter).

Chosen by inspecting, in order:

- `research2/protocol.json` / `research2/baselines.json` / workloads.json:
  no budget convention is declared there (those files are experiment-
  state-machine / identifier placeholders, not budget conventions).
- Research Part 1's own canonical budget convention, used identically and
  repeatedly across EXP-007 (router characterization), EXP-008 (working-
  set), EXP-019 (instrumentation validation) and EXP-021 (replay
  fidelity): **4 / 2 / 1.5 GiB** for the 30B model on the reference
  machine. This is reused verbatim, not reinvented -- it is the project's
  own, already-validated lower/memory-pressure budget set.
- The real R2-01-validated trace's own total expert-working-set size
  (computed once, from the trace's own metadata: 128 experts x 48 layers,
  `sum(expert_bytes[layer] * n_experts for layer in layers)` ~=
  17,553,162,240 bytes ~= 16.35 GiB for this specific capture). Part 1's
  own experiments never swept a budget above 4 GiB for this model, so no
  existing canonical higher point exists to reuse; two higher points are
  added using the SAME convention Part 1 already uses for its own budget
  labels (clean GiB values, doublings of the existing 4 GiB point): 8 GiB
  (roughly half of the real total working-set size -- still memory-
  constrained, but far less pressured than 4 GiB) and 16 GiB (at
  approximately the real total working-set size -- the regime where
  nothing should need eviction at all, and any residual Oracle/LRU gap
  should be at or near zero). This brackets "below or around the total
  expert working-set capacity" exactly, before any real analysis is run.

5 points total, strictly increasing, each an exact byte capacity (no
fractional-GiB rounding surprises)."""

from __future__ import annotations

GIB = 1 << 30

BUDGET_1_5_GIB = int(1.5 * GIB)   # 1,610,612,736  -- Part 1 canonical (EXP-007/008/019/021)
BUDGET_2_GIB = 2 * GIB             # 2,147,483,648  -- Part 1 canonical
BUDGET_4_GIB = 4 * GIB             # 4,294,967,296  -- Part 1 canonical; the R2-02 reproduction point
BUDGET_8_GIB = 8 * GIB             # 8,589,934,592  -- higher point, ~half the real total working set
BUDGET_16_GIB = 16 * GIB           # 17,179,869,184 -- higher point, ~= the real total working set

# Order matters for the published curve (low memory pressure -> high) and
# for report readability; it is NOT a hint about expected result shape
# (section "NO POST-HOC STORYTELLING" -- the actual shape is reported,
# never assumed monotonic).
FIXED_BUDGETS_BYTES = (
    BUDGET_1_5_GIB,
    BUDGET_2_GIB,
    BUDGET_4_GIB,
    BUDGET_8_GIB,
    BUDGET_16_GIB,
)

# The mandatory R2-02 reproduction point (section "R2-02 REPRODUCTION
# CHECK"): must be exactly 4 GiB and must be a member of the sweep above.
REPRODUCTION_BUDGET_BYTES = BUDGET_4_GIB

assert REPRODUCTION_BUDGET_BYTES in FIXED_BUDGETS_BYTES
assert len(FIXED_BUDGETS_BYTES) == len(set(FIXED_BUDGETS_BYTES)), "no duplicate budgets"
assert list(FIXED_BUDGETS_BYTES) == sorted(FIXED_BUDGETS_BYTES), "budgets must be strictly increasing"


def budget_label(budget_bytes: int) -> str:
    """Human-readable GiB label for a declared budget, e.g. '1.5 GiB'."""
    gib = budget_bytes / GIB
    if gib == int(gib):
        return f"{int(gib)} GiB"
    return f"{gib:g} GiB"
