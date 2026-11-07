"""R2-04 sections 7/8: the core and relative decomposition of the total
observed upper-bound gap into a STRUCTURAL SHARDING GAP and a POLICY-
INFORMATION HEADROOM, for every R2-03 budget.

Reuses `r2_02.comparison.oracle_gap()` verbatim (never a new, competing
gap formula) for all three pairwise comparisons -- it already computes
exactly the absolute/relative miss-count and miss-byte reduction formulas
section 8 specifies, with the correct per-pair denominator (the first
argument's own row), and the `oracle_dominates` flag this module's own
outcome rules reuse for FAIL detection. The TERMINOLOGY CONTRACT (section
4) is layered on top here, purely as presentation/interpretation strings:
no new numeric semantics are invented.

Section 8 explicitly forbids forcing the structural and information
relative fractions to sum arithmetically (their denominators differ), so
this module never adds the two relative fractions together. The absolute
byte/count differences DO sum exactly by construction (STRUCTURAL +
INFORMATION == TOTAL, since both are pairwise differences of the same
three numbers), and that identity is asserted here as a self-check.
"""

from __future__ import annotations

from typing import Any, Dict

from ..r2_02 import comparison as r2_02_comparison
from ..r2_02 import policy_set

LABEL_SHARDED = policy_set.LABEL_RUNTIME_SHARDED_LRU
LABEL_GLOBAL_LRU = policy_set.LABEL_GLOBAL_LRU
LABEL_ORACLE = policy_set.ORACLE_LABEL

KEY_TOTAL = "total_observed_upper_bound_gap"
KEY_STRUCTURAL = "structural_sharding_gap"
KEY_INFORMATION = "policy_information_headroom"


class DecompositionError(ValueError):
    """Base class for decomposition problems."""


class MissingPolicyError(DecompositionError):
    """A budget's rows do not contain all three required policies.
    Distinct from ImpossibleValuesError: a missing policy at one budget
    (e.g. a replay error already recorded upstream) is handled by the
    caller's own outcome classification, not necessarily an immediate
    FAIL by itself."""


class ImpossibleValuesError(DecompositionError):
    """The absolute-difference identity fails: internally impossible
    comparison values. This is unconditionally an R2-04 FAIL condition
    (section 18) and must never be silently skipped."""


def decompose_budget(rows: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Section 7/8 decomposition for ONE R2-03 budget's three rows
    (Runtime-Sharded-LRU, Global-LRU, Oracle-Global-Belady)."""
    for label in (LABEL_SHARDED, LABEL_GLOBAL_LRU, LABEL_ORACLE):
        if label not in rows:
            raise MissingPolicyError(f"missing required policy row {label!r} for "
                                     f"decomposition")

    sharded_row = rows[LABEL_SHARDED]
    global_lru_row = rows[LABEL_GLOBAL_LRU]
    oracle_row = rows[LABEL_ORACLE]

    structural = r2_02_comparison.oracle_gap(sharded_row, global_lru_row)
    information = r2_02_comparison.oracle_gap(global_lru_row, oracle_row)
    total = r2_02_comparison.oracle_gap(sharded_row, oracle_row)

    # Self-check (section 18 FAIL condition: "mathematically impossible
    # comparison values"): absolute differences must sum exactly, since
    # STRUCTURAL = Sharded - GlobalLRU, INFORMATION = GlobalLRU - Oracle,
    # TOTAL = Sharded - Oracle are pairwise differences of the same three
    # numbers (this holds for both miss-count and miss-byte views,
    # independent of sign -- the structural component MAY be negative,
    # e.g. observed at 1.5/2 GiB where sharding itself reduces misses).
    if structural["miss_reduction"] + information["miss_reduction"] != total["miss_reduction"]:
        raise ImpossibleValuesError(
            f"miss-count decomposition does not sum exactly: structural="
            f"{structural['miss_reduction']} + information="
            f"{information['miss_reduction']} != total={total['miss_reduction']}")
    if (structural["miss_byte_reduction"] + information["miss_byte_reduction"]
           != total["miss_byte_reduction"]):
        raise ImpossibleValuesError(
            f"miss-byte decomposition does not sum exactly: structural="
            f"{structural['miss_byte_reduction']} + information="
            f"{information['miss_byte_reduction']} != total="
            f"{total['miss_byte_reduction']}")

    return {
        KEY_STRUCTURAL: {
            **structural,
            "interpretation": (
                "Runtime-Sharded-LRU vs Global-LRU: both causal; approximates the "
                "runtime's sharded-retention-structure effect under the existing "
                "offline model. LIMITATION (preserved from R2-02): the offline "
                "ShardedLRU's shard-to-key mapping is statistical, not the runtime's "
                "exact address-derived shard mapping. This gap may be negative "
                "(sharding itself reducing misses relative to Global-LRU) -- that is "
                "a valid observed result, not an error."),
        },
        KEY_INFORMATION: {
            **information,
            "interpretation": (
                "Global-LRU vs Oracle-Global-Belady: both global cache scope -- the "
                "scientifically clean primary upper bound for future causal "
                "prediction/retention work. This is THEORETICAL/INFORMATIONAL "
                "HEADROOM, not demonstrated causally recoverable performance (that is "
                "II-C's job, not R2-04's)."),
        },
        KEY_TOTAL: {
            **total,
            "interpretation": (
                "Runtime-Sharded-LRU vs Oracle-Global-Belady: includes BOTH structural "
                "and information differences; must not be described purely as "
                "'prediction headroom' -- it also contains the structural sharding "
                "effect above."),
        },
    }


def decompose_all_budgets(budget_results: Dict[int, Dict[str, Dict[str, Any]]]
                          ) -> Dict[int, Dict[str, Any]]:
    """Section 7/8 decomposition for every R2-03 budget where all three
    required policies are present. Budgets missing a required policy are
    silently omitted here (not an error by itself -- the caller's outcome
    classification decides whether that is INCONCLUSIVE)."""
    out: Dict[int, Dict[str, Any]] = {}
    for budget, rows in budget_results.items():
        try:
            out[budget] = decompose_budget(rows)
        except MissingPolicyError:
            continue
        # ImpossibleValuesError is intentionally NOT caught here: it must
        # propagate to the orchestrator's outcome classification as a
        # hard FAIL (section 18), never be silently skipped.
    return out
