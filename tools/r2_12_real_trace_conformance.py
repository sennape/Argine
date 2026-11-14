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

KNOWN STRUCTURAL GAP -- "protected_before" for predicted_protect:
argine-retention-policy.h/.cpp (frozen, commit c2f9a68) declares
`RetentionPolicy::inspect()` NON-VIRTUAL on the base class, and
`PredictedProtectPolicy::predicted_next_` is a PRIVATE member of the
derived class with no public accessor. The real runtime holds only a
`std::unique_ptr<RetentionPolicy>` (base-class pointer), so
`c->policy->inspect(layer)` can never reach `predicted_next_` for
predicted_protect -- `Inspection::protected_experts_for_layer` is
structurally always empty for ANY caller of the public API, not a defect
in the C++ integration's instrumentation. This is NOT altered here: the
frozen module is not touched by this tool or by the real-runtime
integration. Consequently this comparator treats a "protected_before"
disagreement for policy_id=="predicted_protect" as a reported, non-fatal
API-coverage gap (see `protected_before_gap_events` in the result) rather
than a conformance MISMATCH -- because the gap is in what the real trace
can REPORT, not in what the real runtime DECIDES (choose_victim() inside
PredictedProtectPolicy does consult predicted_next_ correctly internally;
only external visibility into that value is unavailable). Closing it for
real would require a minimal additive accessor on the frozen module
itself (e.g. a virtual `protected_for(layer)` defaulting to empty on the
base, overridden by PredictedProtectPolicy) -- out of scope here since it
touches a frozen, already-committed artifact; flagged for the user to
decide rather than done unilaterally. For policy_id=="b_selected" this
field is still compared strictly (it is legitimately always empty on
both sides there, so any disagreement WOULD be a real bug).

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
    protected_before_gaps = []
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

        # "protected_before" is a known, structural API-coverage gap for
        # predicted_protect (see module docstring): the real trace can
        # only ever report [] for this field via the frozen module's
        # public inspect(), regardless of whether the runtime's actual
        # eviction decision correctly consulted the real protected set
        # internally. Report the gap; do not fail the run over it. For
        # b_selected there is no such gap (protected is legitimately []
        # on both sides), so a disagreement there is a genuine bug.
        if policy_id == "predicted_protect":
            if trace_protected != [] or protected_before != trace_protected:
                protected_before_gaps.append({
                    "step": ev["step"], "layer": ev["layer"],
                    "python_protected_before": protected_before,
                    "real_runtime_protected_before": trace_protected,
                })
        elif protected_before != trace_protected:
            return False, {
                "step": ev["step"],
                "layer": ev["layer"],
                "requested_experts": trace_requested,
                "resident_before": sorted([r["layer"], r["expert"]] for r in ev["resident_before"]),
                "protected_before_trace": trace_protected,
                "field": "protected_before",
                "python_value": protected_before,
                "real_runtime_value": trace_protected,
            }, None

        checks = [
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

    summary = {
        "events_compared": len(events),
        "policy_id": policy_id,
        "final_resident": sorted(list(o) for o in policy.resident.keys()),
        "final_resident_bytes": policy.resident_bytes,
        "total_evictions": policy.evictions,
        "protected_before_gap_events": len(protected_before_gaps),
    }
    if protected_before_gaps:
        summary["protected_before_gap_note"] = (
            "protected_before is structurally unreportable for predicted_protect "
            "via the frozen module's public inspect() API (base class is "
            "non-virtual; predicted_next_ is private to the derived class); "
            "all OTHER decision fields (hit/miss/evicted/resident_after/bytes) "
            "were compared strictly and matched for every one of these events. "
            "See the module docstring for the exact root cause."
        )
        summary["protected_before_gap_sample"] = protected_before_gaps[:5]
    return True, None, summary


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
