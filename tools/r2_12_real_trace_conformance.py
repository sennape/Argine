"""R2-12 -- real-runtime trace vs frozen Python reference, decision-by-decision.

Reads the rich JSONL decision trace produced by the REAL compiled runtime
(argine-store.cpp's servi_evento_con_politica, via ARGINE_RETENTION_TRACE --
see experiments/R2-12-runtime-validation/RETENTION-POLICY-TRACE-INSTRUMENTATION.md
for the schema) and replays the identical event sequence through the ACTUAL
frozen Python reference classes (argine.research2.r2_09a.policies.AgedFrequency
and argine.research2.r2_bridge01.retention_policies.PredictedProtectPolicy),
never a reimplementation of either side. Compares decision-by-decision and
fails immediately at the first mismatch, reporting its full state.

This tool does not run any real model and does not touch llama.cpp/ggml. It
only reads a trace file already produced by a real run (or, for the
synthetic tests, a hand-built one) and the already-frozen Python classes.

"protected_before" (both policies, now a required comparison field):
The real runtime's trace reports the ACTUAL protected set choose_victim()
consults for predicted_protect, via RetentionPolicy::protected_for(layer)
-- a minimal, read-only, additive accessor on the frozen module
(argine-retention-policy.h/.cpp, commit c2f9a68 base; the accessor itself
was added in a later, separately-committed, explicitly user-authorized
change -- see RETENTION-POLICY-TRACE-INSTRUMENTATION.md). It participates
in no decision: choose_victim() still reads predicted_next_ directly, as
it always did; protected_for() only exposes the same already-computed
value for the trace. This closes what was previously a known structural
visibility gap (predicted_before was always [] for predicted_protect in
any real trace, even though the real decision was correct) -- that gap no
longer exists, so this field is now compared strictly for BOTH policies:
for b_selected it is legitimately always [] on both sides; for
predicted_protect it is the real, non-empty, causally-correct protected
set after the first event on a layer. Any disagreement on this field is
now treated exactly like any other decision-field mismatch: reported at
the first occurrence, with the run stopped.

Usage:
    python3 r2_12_real_trace_conformance.py <trace.jsonl>

Exit code 0 = every event matched exactly. Exit code 1 = mismatch, malformed
trace, or inconsistent provenance (reported on stderr as JSON).
"""
import json
import sys
from pathlib import Path


def _locate_repo_root():
    root = Path(__file__).resolve().parent
    while not (root / "argine").is_dir():
        if root.parent == root:
            raise SystemExit("could not locate repo root containing argine/")
        root = root.parent
    return root


ROOT = _locate_repo_root()
sys.path.insert(0, str(ROOT))

from argine.research.policies import Context  # noqa: E402
from argine.research2.r2_09a.policies import AgedFrequency  # noqa: E402
from argine.research2.r2_bridge01.retention_policies import (  # noqa: E402
    PredictedProtectPolicy,
    FROZEN_PREDICTOR_DECAY,
)


class TraceFormatError(Exception):
    """A trace line is missing a required field or is not valid JSON."""


class ProvenanceMismatchError(Exception):
    """Two lines of the same trace disagree on policy identity or parameters."""


REQUIRED_FIELDS = (
    "step", "policy", "layer", "bytes_of_layer", "requested_experts",
    "hit_experts", "miss_experts", "protected_before", "resident_before",
    "evicted", "resident_after", "cache_resident_bytes_before",
    "cache_resident_bytes_after", "policy_resident_bytes_after",
    "budget_bytes", "policy_params",
)
REQUIRED_PARAM_FIELDS = ("decay_factor", "aging_interval_steps", "predictor_decay", "top_k", "n_experts")


def parse_trace(lines):
    """Parse and validate trace lines. Raises TraceFormatError on any
    structurally invalid or incomplete line -- never guesses a missing
    field and never silently drops a bad line."""
    events = []
    for i, raw in enumerate(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError as e:
            raise TraceFormatError(f"line {i}: not valid JSON ({e})")
        if not isinstance(ev, dict):
            raise TraceFormatError(f"line {i}: not a JSON object")
        missing = [f for f in REQUIRED_FIELDS if f not in ev]
        if missing:
            raise TraceFormatError(f"line {i}: missing required field(s) {missing}")
        pmissing = [f for f in REQUIRED_PARAM_FIELDS if f not in ev["policy_params"]]
        if pmissing:
            raise TraceFormatError(f"line {i}: policy_params missing field(s) {pmissing}")
        events.append(ev)
    if not events:
        raise TraceFormatError("trace is empty")
    return events


def check_provenance(events):
    """Every event must agree on policy identity and frozen parameters;
    a trace that silently changed policy mid-run would make the whole
    comparison meaningless. Raises ProvenanceMismatchError with the exact
    pair of disagreeing events, not a vague summary."""
    first = events[0]
    for ev in events[1:]:
        if ev["policy"] != first["policy"]:
            raise ProvenanceMismatchError(
                f"step {ev['step']}: policy={ev['policy']!r} but step {first['step']} "
                f"had policy={first['policy']!r}"
            )
        if ev["policy_params"] != first["policy_params"]:
            raise ProvenanceMismatchError(
                f"step {ev['step']}: policy_params={ev['policy_params']!r} but step "
                f"{first['step']} had policy_params={first['policy_params']!r}"
            )
        if ev["budget_bytes"] != first["budget_bytes"]:
            raise ProvenanceMismatchError(
                f"step {ev['step']}: budget_bytes={ev['budget_bytes']} but step "
                f"{first['step']} had budget_bytes={first['budget_bytes']}"
            )
    return first["policy"], first["policy_params"], first["budget_bytes"]


def _build_policy(policy_id, params):
    decay = float(params["decay_factor"])
    interval = int(params["aging_interval_steps"])
    if policy_id == "b_selected":
        return AgedFrequency(decay, interval)
    if policy_id == "predicted_protect":
        pred_decay = float(params["predictor_decay"])
        if abs(pred_decay - FROZEN_PREDICTOR_DECAY) > 1e-12:
            raise ProvenanceMismatchError(
                f"trace reports predictor_decay={pred_decay}, but the frozen "
                f"PredictedProtectPolicy reference hardcodes {FROZEN_PREDICTOR_DECAY}"
            )
        return PredictedProtectPolicy(decay, interval)
    raise TraceFormatError(f"unknown policy id {policy_id!r}")


def _patch_evict_one(policy, policy_id, evicted_log):
    if policy_id == "b_selected":
        def patched(self):
            victim = self._choose_victim()
            if victim is None:
                return False
            sz = self.resident.pop(victim)
            self.scores.pop(victim, None)
            self.last_ref.pop(victim, None)
            self.resident_bytes -= sz
            self.evictions += 1
            evicted_log.append(list(victim))
            return True
    else:
        def patched(self, layer):
            victim = self._choose_victim(layer)
            if victim is None:
                return False
            sz = self.resident.pop(victim)
            self.scores.pop(victim, None)
            self.last_ref.pop(victim, None)
            self.resident_bytes -= sz
            self.evictions += 1
            evicted_log.append(list(victim))
            return True
    policy._evict_one = patched.__get__(policy, type(policy))


def _protected_for_layer(policy, policy_id, layer):
    if policy_id != "predicted_protect":
        return []
    return sorted(policy._predicted_next.get(layer, frozenset()))


def compare(events):
    """Replay `events` through the frozen Python reference and compare
    decision-by-decision against what the real trace recorded. Returns
    (ok, mismatch_or_None, summary). Stops at the FIRST mismatch."""
    policy_id, params, budget_bytes = check_provenance(events)

    layers = sorted({ev["layer"] for ev in events})
    sizes = {ev["layer"]: ev["bytes_of_layer"] for ev in events}
    n_experts = int(params["n_experts"]) if params.get("n_experts") else (
        max(max(ev["requested_experts"], default=0) for ev in events) + 1
    )
    ctx = Context(layers=layers, n_experts=n_experts, size=sizes, budget=budget_bytes,
                   top_k=int(params.get("top_k") or 0))

    policy = _build_policy(policy_id, params)
    policy.start(ctx)
    evicted_log = []
    _patch_evict_one(policy, policy_id, evicted_log)

    for ev in events:
        protected_before = _protected_for_layer(policy, policy_id, ev["layer"])
        evicted_log.clear()
        missed = policy.access(ev["step"], ev["layer"], ev["requested_experts"])
        py_evicted = list(evicted_log)
        py_resident = sorted(list(o) for o in policy.resident.keys())

        trace_evicted = [[e["layer"], e["expert"]] for e in ev["evicted"]]
        trace_resident_after = sorted([r["layer"], r["expert"]] for r in ev["resident_after"])
        trace_requested = sorted(ev["requested_experts"])
        trace_hit = sorted(ev["hit_experts"])
        trace_miss = sorted(ev["miss_experts"])

        py_hit = sorted(e for e in trace_requested if e not in missed)
        py_miss = sorted(missed)
        trace_protected = sorted(ev["protected_before"])

        checks = [
            ("protected_before", protected_before, trace_protected),
            ("hit_experts", py_hit, trace_hit),
            ("miss_experts", py_miss, trace_miss),
            ("evicted", py_evicted, trace_evicted),
            ("resident_after", py_resident, trace_resident_after),
            ("policy_resident_bytes_after", policy.resident_bytes, ev["policy_resident_bytes_after"]),
        ]
        for field, py_val, trace_val in checks:
            if py_val != trace_val:
                return False, {
                    "step": ev["step"],
                    "layer": ev["layer"],
                    "requested_experts": trace_requested,
                    "resident_before": sorted([r["layer"], r["expert"]] for r in ev["resident_before"]),
                    "protected_before_trace": trace_protected,
                    "field": field,
                    "python_value": py_val,
                    "real_runtime_value": trace_val,
                }, None

    return True, None, {
        "events_compared": len(events),
        "policy_id": policy_id,
        "final_resident": sorted(list(o) for o in policy.resident.keys()),
        "final_resident_bytes": policy.resident_bytes,
        "total_evictions": policy.evictions,
    }


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("uso: r2_12_real_trace_conformance.py <trace.jsonl>\n")
        sys.exit(2)
    with open(sys.argv[1]) as f:
        lines = f.readlines()
    try:
        events = parse_trace(lines)
        ok, mismatch, summary = compare(events)
    except (TraceFormatError, ProvenanceMismatchError) as e:
        print(json.dumps({"result": "BLOCKED_INVALID", "reason": str(e)}))
        sys.exit(1)
    if ok:
        print(json.dumps({"result": "MATCH", **summary}))
        sys.exit(0)
    print(json.dumps({"result": "MISMATCH", **mismatch}))
    sys.exit(1)


if __name__ == "__main__":
    main()
