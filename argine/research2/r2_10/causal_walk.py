"""R2-10 causal event walker.

The SOLE path by which any R2-10 predictor receives trace-derived data.
Structurally enforces experiments/R2-10-working-set-predictor/causal_boundary.json:
a CausalContext at iterator position i exposes exactly stream[0:i+1] and
nothing from position i+1 onward. There is no API in this module that can
return a longer slice; this is what makes the boundary mechanically
auditable rather than merely documented.

"Same-layer stream" reuses Trace.token_selections(layer, phase) -- the same
accessor R2-05/R2-06/R2-07 already use -- and R2-06's established convention
that "consecutive" means consecutive in the iterator's own yielded order,
not consecutive raw token_index (a layer need not route every token).

Phase-1 addition (CARDINALITY-AVAILABILITY-AUDIT-001): k (the output
cardinality a predictor must produce) is resolved ONCE from trace.meta.top_k
at walk-construction time -- i.e. from trace metadata available before any
routing event is observed -- and carried on every CausalContext as `ctx.k`.
This is the only k a Phase-1 predictor may use; it is never derived from
len(target), which would read the future event being predicted. See the
audit record for why trace.meta.top_k is causally safe for every DEV cell
checked (k_{t,l} == top_k with zero exceptions across all 8 cells).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Optional, Sequence, Tuple

from ...research.trace import Trace

Entry = Tuple[int, Tuple[int, ...]]  # (token_index, expert_set)


@dataclass(frozen=True)
class CausalContext:
    """Everything a Phase-0/Phase-1 R2-10 predictor is allowed to see when
    asked to predict the target at iterator position `position` + h, for
    any h >= 1. `history` is stream[0 : position+1] inclusive of the
    current (position) observation -- never anything beyond it. `k` is the
    causally-available output cardinality (trace.meta.top_k), never derived
    from the target."""

    position: int
    token_index: int
    history: Tuple[Entry, ...]
    k: int

    @property
    def current(self) -> Entry:
        return self.history[-1]


def same_layer_stream(trace: Trace, layer: int, phase: Optional[str] = None) -> List[Entry]:
    """Materialize Trace.token_selections(layer, phase) in order.

    Pure data extraction; no causal-boundary semantics live here -- callers
    must go through iter_causal_contexts/target_at to respect the boundary.
    """
    return list(trace.token_selections(layer, phase))


def iter_causal_contexts(stream: Sequence[Entry], k: int) -> Iterator[CausalContext]:
    """Yield one CausalContext per iterator position, i = 0..len(stream)-1.

    CausalContext(i).history == tuple(stream[0:i+1]). `k` must be supplied
    by the caller from trace.meta.top_k (or an equivalent causally-available
    source), never from any entry's own expert-set length -- this function
    does not read stream[i][1]'s length to decide k, by construction.
    """
    for i in range(len(stream)):
        yield CausalContext(position=i, token_index=stream[i][0], history=tuple(stream[: i + 1]), k=k)


def target_at(stream: Sequence[Entry], position: int, horizon: int) -> Optional[Entry]:
    """Target entry at iterator position `position + horizon`, or None.

    None means the target is unavailable near the end of the trace
    (treatment of unavailable future targets, contract section 2): callers
    must skip that (position, horizon) pair when aggregating metrics, never
    fabricate a target.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    j = position + horizon
    if j >= len(stream):
        return None
    return stream[j]
