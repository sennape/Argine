"""R2-06 section 14: the predeclared minimum transition support threshold.

Predeclared BEFORE any real execution of R2-06, based on sample-count
conventions already used elsewhere in this project, NOT on observed
results (a results-dependent threshold would let the analysis tune its
own conclusions). No real R2-06 run has occurred at implementation time;
these constants are fixed now and must not be revisited after seeing
real data without a documented, separately-reviewed change.

Basis for the chosen values: R2-05's own concentration metrics (section
8 of that experiment) treat a layer's statistics as well-formed once it
has collected enough requests that per-expert sampling noise is small
relative to the signal being measured; R2-05 itself did not need to fix
a numeric floor because it reports a FULL distribution over all observed
experts rather than screening individual cells for inclusion in a
headline statistic. R2-06 DOES need a floor, because its headline/
strong-transition summaries deliberately exclude rare, low-confidence
(i, j) cells (section 14) while still preserving them in the raw
transition_metrics.csv. In the absence of an existing project-specific
statistical convention for this exact kind of per-cell count threshold,
R2-06 adopts a conservative, standard-statistics-style fixed rule:

  - MIN_SOURCE_SUPPORT = 30: a source expert's outgoing transition
    distribution (its row of the matrix) is included in any
    "headline"/"strong-transition" cross-layer summary only if that
    source's total outgoing transition count (row_sum) is >= 30 -- the
    conventional minimum-sample-size rule of thumb for a proportion
    estimate to be considered reasonably stable (not derived from, or
    tuned to, any R2-06 result; chosen for being a widely-documented,
    unremarkable default rather than an invented number).
  - MIN_PAIR_SUPPORT = 10: an individual (source, target) transition
    cell is included in any "strong transition" / headline lift
    reporting only if its own raw observation count is >= 10 -- a
    stricter, cell-level floor (an individual cell is a much noisier
    estimate than an entire row), again a conservative round-number
    convention and not tuned to any observed R2-06 result.

Both thresholds apply ONLY to what is surfaced in headline/strong-
transition summaries (summary.py, figures.py, claims.py) and the
CLI headline output. The raw per-cell data in transition_metrics.csv and
layer_transition_summary.csv is NEVER filtered, thresholded, or dropped
by this module or any other -- every observed transition, however rare,
remains visible in the full evidence output.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

MIN_SOURCE_SUPPORT = 30
MIN_PAIR_SUPPORT = 10

SUPPORT_THRESHOLD_RATIONALE = (
    f"Predeclared before any real R2-06 execution, based on sample-count, not "
    f"observed-result, considerations: MIN_SOURCE_SUPPORT={MIN_SOURCE_SUPPORT} "
    f"(a source expert's outgoing row must have at least this many total "
    f"transition observations to be included in headline/strong-transition "
    f"summaries), MIN_PAIR_SUPPORT={MIN_PAIR_SUPPORT} (an individual (source, "
    f"target) cell must have at least this many observations to be reported as "
    f"a 'strong transition'). Both are conservative, widely-documented "
    f"minimum-sample-size conventions, not tuned to any R2-06 result -- no real "
    f"R2-06 canonical run has occurred at the time these constants were fixed. "
    f"Raw per-cell data is never filtered by this rule; only headline/strong-"
    f"transition SUMMARIES are."
)


def supported_sources(table: Dict[str, Any]) -> List[int]:
    """Source experts whose row meets MIN_SOURCE_SUPPORT, deterministically
    ordered ascending by expert id."""
    return sorted(i for i in table["sources"] if table["row_sums"].get(i, 0) >= MIN_SOURCE_SUPPORT)


def supported_pairs(table: Dict[str, Any]) -> List[Tuple[int, int]]:
    """(source, target) cells meeting MIN_PAIR_SUPPORT, deterministically
    ordered: source ascending, then target ascending."""
    out = []
    for i in table["sources"]:
        for j in sorted(table["conditional"].get(i, {})):
            if table["counts"].get((i, j), 0) >= MIN_PAIR_SUPPORT:
                out.append((i, j))
    return out


def strongest_supported_lift(table: Dict[str, Any]) -> "Tuple[int, int, float] | None":
    """The (source, target, lift) triple with the largest lift among
    cells meeting MIN_PAIR_SUPPORT, deterministic tie-break by (source
    asc, target asc). None if no cell meets the threshold or no lift is
    defined for any such cell."""
    candidates = []
    for (i, j) in supported_pairs(table):
        lift_value = table["lift"].get(i, {}).get(j)
        if lift_value is not None:
            candidates.append((i, j, lift_value))
    if not candidates:
        return None
    best = max(candidates, key=lambda t: (t[2], -t[0], -t[1]))
    return best
