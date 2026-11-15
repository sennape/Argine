"""Memory-streaming equivalent of tools/r2_12_real_trace_conformance.py.

This sandbox has ~3.9 GiB RAM and no swap; the unmodified comparator's
main() calls f.readlines() then parse_trace(), which materializes every
one of the 9504 decision events (each carrying growing
resident_before/resident_after arrays, up to ~1600 entries late in the
run) as a single in-memory list before replay -- this OOM-killed (exit
137) on the real ~1.83 GiB b_selected.decisions.jsonl file here.

This script does NOT modify tools/r2_12_real_trace_conformance.py and
does NOT reimplement any frozen policy logic. It imports the exact same
frozen classes from the exact same modules
(argine.research.policies.Context, argine.research2.r2_09a.policies.AgedFrequency,
argine.research2.r2_bridge01.retention_policies.PredictedProtectPolicy)
and reproduces, verbatim, the comparator's own _build_policy,
_patch_evict_one, _protected_for_layer helpers and the exact per-event
field-by-field comparison from compare() in tools/r2_12_real_trace_conformance.py.
The only change is doing a first streaming pass to collect
layers/sizes/n_experts/provenance from one parsed line at a time
(discarding each immediately), then a second streaming pass that
compares one event at a time against the frozen policy, never holding
more than one event's trace row in memory at once.

Usage: python3 streaming_conformance.py <trace.jsonl> <repo_root>
Exit code 0 = MATCH (same semantics as the original). Exit 1 = MISMATCH
or BLOCKED_INVALID. Prints the same JSON result shape as the original.
"""
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[2])
sys.path.insert(0, str(ROOT))

from argine.research.policies import Context  # noqa: E402
from argine.research2.r2_09a.policies import AgedFrequency  # noqa: E402
from argine.research2.r2_bridge01.retention_policies import (  # noqa: E402
    PredictedProtectPolicy,
    FROZEN_PREDICTOR_DECAY,
)

REQUIRED_FIELDS = (
    "step", "policy", "layer", "bytes_of_layer", "requested_experts",
    "hit_experts", "miss_experts", "protected_before", "resident_before",
    "evicted", "resident_after", "cache_resident_bytes_before",
    "cache_resident_bytes_after", "policy_resident_bytes_after",
    "budget_bytes", "policy_params",
)
REQUIRED_PARAM_FIELDS = ("decay_factor", "aging_interval_steps", "predictor_decay", "top_k", "n_experts")


class TraceFormatError(Exception):
    pass


class ProvenanceMismatchError(Exception):
    pass


def _validate_line(ev, lineno):
    if not isinstance(ev, dict):
        raise TraceFormatError(f"line {lineno}: not a JSON object")
    missing = [f for f in REQUIRED_FIELDS if f not in ev]
    if missing:
        raise TraceFormatError(f"line {lineno}: missing required field(s) {missing}")
    pmissing = [f for f in REQUIRED_PARAM_FIELDS if f not in ev["policy_params"]]
    if pmissing:
        raise TraceFormatError(f"line {lineno}: policy_params missing field(s) {pmissing}")


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


def main():
    path = sys.argv[1]

    layers = set()
    sizes = {}
    max_expert = -1
    first_policy = None
    first_params = None
    first_budget = None
    n_lines = 0
    with open(path) as f:
        for lineno, raw in enumerate(f):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError as e:
                print(json.dumps({"result": "BLOCKED_INVALID", "reason": f"line {lineno}: not valid JSON ({e})"}))
                sys.exit(1)
            try:
                _validate_line(ev, lineno)
            except TraceFormatError as e:
                print(json.dumps({"result": "BLOCKED_INVALID", "reason": str(e)}))
                sys.exit(1)
            n_lines += 1
            if first_policy is None:
                first_policy, first_params, first_budget = ev["policy"], ev["policy_params"], ev["budget_bytes"]
            else:
                if ev["policy"] != first_policy:
                    print(json.dumps({"result": "BLOCKED_INVALID",
                                       "reason": f"step {ev['step']}: policy={ev['policy']!r} but earlier policy={first_policy!r}"}))
                    sys.exit(1)
                if ev["policy_params"] != first_params:
                    print(json.dumps({"result": "BLOCKED_INVALID",
                                       "reason": f"step {ev['step']}: policy_params mismatch vs earlier events"}))
                    sys.exit(1)
                if ev["budget_bytes"] != first_budget:
                    print(json.dumps({"result": "BLOCKED_INVALID",
                                       "reason": f"step {ev['step']}: budget_bytes mismatch vs earlier events"}))
                    sys.exit(1)
            layers.add(ev["layer"])
            sizes[ev["layer"]] = ev["bytes_of_layer"]
            if ev["requested_experts"]:
                m = max(ev["requested_experts"])
                if m > max_expert:
                    max_expert = m
            del ev

    if n_lines == 0:
        print(json.dumps({"result": "BLOCKED_INVALID", "reason": "trace is empty"}))
        sys.exit(1)

    policy_id, params, budget_bytes = first_policy, first_params, first_budget
    layers = sorted(layers)
    n_experts = int(params["n_experts"]) if params.get("n_experts") else (max_expert + 1)
    ctx = Context(layers=layers, n_experts=n_experts, size=sizes, budget=budget_bytes,
                   top_k=int(params.get("top_k") or 0))

    policy = _build_policy(policy_id, params)
    policy.start(ctx)
    evicted_log = []
    _patch_evict_one(policy, policy_id, evicted_log)

    events_compared = 0
    with open(path) as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            ev = json.loads(raw)

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
                    print(json.dumps({
                        "result": "MISMATCH",
                        "step": ev["step"],
                        "layer": ev["layer"],
                        "requested_experts": trace_requested,
                        "resident_before": sorted([r["layer"], r["expert"]] for r in ev["resident_before"]),
                        "protected_before_trace": trace_protected,
                        "field": field,
                        "python_value": py_val,
                        "real_runtime_value": trace_val,
                    }))
                    sys.exit(1)
            events_compared += 1
            del ev

    print(json.dumps({
        "result": "MATCH",
        "events_compared": events_compared,
        "policy_id": policy_id,
        "final_resident": sorted(list(o) for o in policy.resident.keys()),
        "final_resident_bytes": policy.resident_bytes,
        "total_evictions": policy.evictions,
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
