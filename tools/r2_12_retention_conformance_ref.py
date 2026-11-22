"""R2-12 realization/conformance phase -- Python reference-side driver.

Runs a synthetic/recorded trace through the ACTUAL frozen Python classes
(argine.research2.r2_09a.policies.AgedFrequency and
argine.research2.r2_bridge01.retention_policies.PredictedProtectPolicy),
never a reimplementation, and emits one JSON line per event in exactly the
format the C++ conformance harness (argine-retention-conformance-main.cpp)
emits, so the two can be diffed byte-for-byte.

Usage:
    python3 retention_conformance_ref.py <policy_id> <decay> <interval> \
        [<predictor_decay> <top_k> <n_experts>] <budget_bytes> < trace.jsonl

No real model, no real runtime, no R2-12 counted/warmup run -- pure
synthetic-trace replay against already-frozen Python code.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve()
# locate repo root (contains "argine/" package) by walking up
root = ROOT.parent
while not (root / "argine").is_dir():
    if root.parent == root:
        raise SystemExit("could not locate repo root containing argine/")
    root = root.parent
sys.path.insert(0, str(root))

from argine.research.policies import Context  # noqa: E402
from argine.research2.r2_09a.policies import AgedFrequency  # noqa: E402
from argine.research2.r2_bridge01.retention_policies import PredictedProtectPolicy  # noqa: E402


def main():
    argv = sys.argv[1:]
    policy_id = argv[0]
    decay = float(argv[1])
    interval = int(argv[2])
    if policy_id == "predicted_protect":
        pred_decay = float(argv[3])
        top_k = int(argv[4])
        n_experts = int(argv[5])
        budget_bytes = int(argv[6])
    else:
        budget_bytes = int(argv[3])
        pred_decay = None
        top_k = None
        n_experts = None

    layers = set()
    sizes = {}
    events = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)
        layers.add(ev["layer"])
        sizes[ev["layer"]] = ev["bytes_of_layer"]
        events.append(ev)

    ctx = Context(
        layers=sorted(layers),
        n_experts=(n_experts if n_experts else max(max(e["experts"]) for e in events) + 1),
        size=sizes,
        budget=budget_bytes,
        top_k=(top_k if top_k else 0),
    )

    if policy_id == "b_selected":
        policy = AgedFrequency(decay, interval)
    elif policy_id == "predicted_protect":
        # PredictedProtectPolicy's predictor is hardcoded to
        # FROZEN_PREDICTOR_DECAY = 0.99 inside the reference module itself
        # (it does not take a predictor-decay constructor argument) --
        # that is the frozen R2-12 predictor and is not configurable here,
        # matching the fact that R2-12's contract pins it. We still require
        # the CLI's pred_decay argument to equal 0.99 so a mismatched
        # harness invocation is caught rather than silently ignored.
        if abs(pred_decay - 0.99) > 1e-12:
            raise SystemExit(
                f"predictor_decay={pred_decay} requested, but the frozen "
                f"PredictedProtectPolicy reference hardcodes 0.99; refusing to "
                f"silently diverge from the frozen reference."
            )
        policy = PredictedProtectPolicy(decay, interval)
    else:
        raise SystemExit(f"unknown policy_id {policy_id!r}")

    policy.start(ctx)

    hits_total = 0
    misses_total = 0
    # Record every real eviction AS IT HAPPENS, not by diffing resident-set
    # snapshots before/after access(): the frozen AgedFrequency/
    # PredictedProtectPolicy core can evict a member of the CURRENT event and
    # then re-admit that exact same (layer, expert) object later within that
    # SAME event (this is a real, legal, already-frozen behavior -- see
    # policies.py's own _evict_one -- not something this phase introduces).
    # A naive before/after set diff is blind to that transient eviction
    # because the net key membership is unchanged; wrapping the real
    # `_evict_one` call is the only way to see every eviction the policy
    # itself actually performed, which is what the C++ harness already logs.
    current_event_evictions = []

    if policy_id == "b_selected":
        # AgedFrequency._evict_one(self) -- no layer argument.
        def patched_evict_one(self):
            victim = self._choose_victim()
            if victim is None:
                return False
            sz = self.resident.pop(victim)
            self.scores.pop(victim, None)
            self.last_ref.pop(victim, None)
            self.resident_bytes -= sz
            self.evictions += 1
            current_event_evictions.append(list(victim))
            return True
    else:
        # _BaseBridgePolicy._evict_one(self, layer) -- PredictedProtectPolicy.
        def patched_evict_one(self, layer):
            victim = self._choose_victim(layer)
            if victim is None:
                return False
            sz = self.resident.pop(victim)
            self.scores.pop(victim, None)
            self.last_ref.pop(victim, None)
            self.resident_bytes -= sz
            self.evictions += 1
            current_event_evictions.append(list(victim))
            return True

    policy._evict_one = patched_evict_one.__get__(policy, type(policy))

    for ev in events:
        current_event_evictions.clear()
        missed = policy.access(ev["step"], ev["layer"], ev["experts"])
        evicted_objs = list(current_event_evictions)
        uniq_served = sorted(set(ev["experts"]))
        misses_total += len(missed)
        hits_total += len(uniq_served) - len(missed)
        print(json.dumps({
            "step": ev["step"],
            "layer": ev["layer"],
            "missed": sorted(missed),
            "evicted": evicted_objs,
            "resident": sorted([list(o) for o in policy.resident.keys()]),
            "resident_bytes": policy.resident_bytes,
        }))

    sys.stderr.write(
        f"FINAL hits={hits_total} misses={misses_total} evictions={policy.evictions} "
        f"resident_bytes={policy.resident_bytes} policy_id={policy_id} "
        f"overflow_events={policy.overflow_events} overflow_misses={policy.overflow_misses}\n"
    )


if __name__ == "__main__":
    main()
