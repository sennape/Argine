"""Lightweight deterministic replay for R2-09A tournament (no per-step arrays/timelines/cost)."""
from __future__ import annotations
import hashlib, json
from argine.research.policies import Context
from argine.research.replay import InvariantViolation, make_context, served_steps, counted_step
from argine.research.trace import PHASE_DECODE, Trace

EXCLUDED_SEGMENTS = ("warmup", "flush-sentinel")

def _canon_json(obj): return json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False)
def _digest(obj): return hashlib.sha256(_canon_json(obj).encode("utf-8")).hexdigest()

def run_fast(trace: Trace, policy, budget: int, *, phase=PHASE_DECODE, trace_digest=""):
    from argine.research2.r2_01.policy_interface import assert_causal
    assert_causal(policy)
    ctx = make_context(trace, budget)
    policy.start(ctx)
    if policy.resident_bytes > budget:
        raise InvariantViolation(f"{policy.name}: start resident {policy.resident_bytes}>{budget}")
    layers = ctx.layers
    per_layer = {l: {"accesses": 0, "misses": 0} for l in layers}
    hits=misses=acc=events=bmiss=0
    peak=policy.resident_bytes
    tokens=0
    n_counted=0
    for i in served_steps(trace, phase, "v2"):
        st = trace.steps[i]
        counted = counted_step(st, phase, "v2")
        if counted:
            n_counted += 1
        for l in layers:
            ex = st.experts(l)
            if not ex: continue
            missed = policy.access(i, l, ex)
            if policy.resident_bytes > budget:
                raise InvariantViolation(f"{policy.name}: resident {policy.resident_bytes}>{budget} step {i} layer {l}")
            peak = max(peak, policy.resident_bytes)
            if counted:
                b = ctx.bytes_of(l); m = len(missed)
                events += 1; acc += len(ex); misses += m; hits += len(ex)-m; bmiss += m*b
                per_layer[l]["accesses"] += len(ex); per_layer[l]["misses"] += m
        policy.end_step(i)
        if counted:
            tokens += st.n_tokens
    det = {
        "policy": policy.name,
        "budget": int(budget),
        "phase": phase or "all",
        "steps": n_counted,
        "tokens": tokens,
        "events": events,
        "accesses": acc,
        "hits": hits,
        "misses": misses,
        "bytes_missed": bmiss,
        "evictions": policy.evictions,
        "overflow_events": policy.overflow_events,
        "overflow_misses": policy.overflow_misses,
        "peak_resident": peak,
        "per_layer": per_layer,
        # exclude per_step arrays for speed but keep deterministic fields used by R2-02/R2-03 digest?
        # R2-01/R2-02 _deterministic_subset includes per_step_misses/bytes/accesses; omitting would change digest and break reuse match.
        "per_step_misses": None, "per_step_bytes": None, "per_step_accesses": None,
    }
    # compute canonical digest matching R2-01/R2-02 pattern
    det_for_digest = {k: det[k] for k in ("policy","budget","phase","steps","tokens","events","accesses","hits","misses","bytes_missed","evictions","overflow_events","overflow_misses","peak_resident","per_layer","per_step_misses","per_step_bytes","per_step_accesses")}
    # But we must match canonical R2-03 digests for reuse verification -> need full per_step arrays.
    # So fast path cannot be used when digest must match R2-03. We'll compute per-step in a compact way.
    return det, det_for_digest
