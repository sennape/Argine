"""R2-08 matched decode horizon (section 14).

Primary cross-workload comparisons use the same decode horizon H:
  H = min(512, minimum valid decode positions across all 8 canonical captures)
Require H >= 256 for canonical PASS. No silent truncation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...research.trace import PHASE_DECODE, PHASE_PREFILL, Step, Trace, TraceMeta

HARD_CAP = 512
MIN_H = 256


@dataclass
class CaptureHorizonInfo:
    workload_id: str
    seed: int
    trace_sha256: str
    total_positions: int
    prefill_positions: int
    decode_positions: int
    valid_decode_positions: int


def count_positions(trace: Trace) -> Tuple[int, int, int]:
    prefill = 0
    decode = 0
    for st in trace.steps:
        if st.phase == PHASE_PREFILL:
            prefill += st.n_tokens
        elif st.phase == PHASE_DECODE:
            decode += st.n_tokens
        else:
            # unknown: count by n_tokens heuristic? be conservative, do not guess phase
            pass
    return prefill + decode, prefill, decode


def decode_positions_per_layer(trace: Trace, layer: int) -> int:
    return sum(1 for _ in trace.token_selections(layer, phase=PHASE_DECODE))


def valid_decode_positions(trace: Trace) -> int:
    """Minimum decode token-selections count across all trace layers (a valid
    position requires routing evidence for that layer in decode phase)."""
    layers = trace.layers
    if not layers:
        return 0
    return min(decode_positions_per_layer(trace, layer) for layer in layers)


def build_matched_decode_trace(trace: Trace, h: int) -> Trace:
    """Trace containing the first H decode token positions in trace order.

    Keeps decode steps in order and truncates so that the cumulative decode
    token count is exactly H (assuming standard 1-token decode steps; if a step
    contains multiple tokens, it is included only if it fits entirely). The
    resulting trace preserves per-layer routes for included steps.
    """
    kept: List[Step] = []
    n = 0
    for st in trace.steps:
        if st.phase != PHASE_DECODE:
            continue
        if n + st.n_tokens > h:
            break
        kept.append(st)
        n += st.n_tokens
    if n != h:
        # If we couldn't get exact H (e.g. multi-token decode steps), keep what fit.
        # Caller validates h against valid_decode_positions.
        pass
    meta = TraceMeta(**{**trace.meta.__dict__})
    meta.split = trace.meta.split
    return Trace(meta=meta, steps=list(kept))


def compute_matched_horizon(infos: Sequence[CaptureHorizonInfo]) -> Dict[str, Any]:
    if not infos:
        return {"ok": False, "reason": "no capture horizon info", "h": None}
    min_decode = min(i.valid_decode_positions for i in infos)
    h = min(HARD_CAP, min_decode)
    ok = h >= MIN_H
    return {
        "ok": ok,
        "h": h,
        "hard_cap": HARD_CAP,
        "min_required": MIN_H,
        "minimum_valid_decode_positions_across_captures": min_decode,
        "per_capture": [
            {
                "workload_id": i.workload_id, "seed": i.seed, "trace_sha256": i.trace_sha256,
                "total_positions": i.total_positions, "prefill_positions": i.prefill_positions,
                "decode_positions": i.decode_positions,
                "valid_decode_positions": i.valid_decode_positions,
                "matched_positions_used": (h if ok else None),
                "positions_discarded_because_of_horizon_matching": (
                    max(0, i.valid_decode_positions - h) if ok else None),
            }
            for i in infos
        ],
        "reason": None if ok else f"matched decode horizon H={h} < {MIN_H}",
    }
