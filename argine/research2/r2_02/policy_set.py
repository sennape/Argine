"""R2-02 section 2/17: the declared policy set.

Reuses Research Part 1's existing, already-validated policy classes
verbatim (`argine.research.policies`, `argine.research.oracle`) -- this
module invents no new policy and no new replay semantics. Every entry
explicitly records the metadata section 17 requires: name, version,
causal flag, and configuration (capacity bytes is filled in at replay
time, once the fixed 4 GiB budget is known -- see orchestrator.py).

Only policies whose semantics are explicit, deterministic, and already
validated by Part 1 are included (section 2: "prefer scientific clarity
over baseline count"). Candidates investigated and explicitly excluded:

* An LFU variant is included (`argine.research.policies.LFU`, halflife=0,
  in_cache_only=False) because its semantics -- frequency scope (global),
  tie-breaking (least-recent-use among equal counts), admission/eviction,
  cold start, frequency init, and update ordering -- are all predeclared
  and deterministic in Part 1's own implementation. No *additional*,
  underspecified LFU variant is invented merely to raise the policy count.
* No shard-constrained Oracle exists in Part 1 (`argine.research.oracle`
  defines only `BeladyGlobal` and `BeladyPerLayer`, the latter per-LAYER
  not per-SHARD). Section 19's own escape clause applies: this is reported
  as a limitation (see docs/research2/METHODOLOGY.md and the R2-02 README)
  rather than inventing an unvalidated sharded oracle. The sharding
  decomposition (section 19/20) is instead built from the three *existing*
  policies below, using Global-LRU as the shared pivot between the
  structural (sharding) gap and the policy-information gap -- see
  orchestrator.compute_comparison().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from ...research.oracle import BeladyGlobal
from ...research.policies import GlobalLRU, LFU, Policy, ShardedLRU
from ...research.trace import PHASE_DECODE, Trace

LABEL_RUNTIME_SHARDED_LRU = "Runtime-Sharded-LRU"
LABEL_GLOBAL_LRU = "Global-LRU"
LABEL_LFU = "LFU"
LABEL_ORACLE_GLOBAL_BELADY = "Oracle-Global-Belady"

# The real runtime's own shard count at a 4 GiB budget, per R2-01B's
# confirmed discovery (argine.research.storereplay.runtime_shard_count):
# clamp(budget // 32MiB, 1, 16) == 16 at 4 GiB. Not re-derived here --
# this module trusts R2-01B's already-validated finding rather than
# recomputing it, since R2-02's own trace/budget binding is fixed
# independently (see prerequisite.py / orchestrator.py).
RUNTIME_SHARD_COUNT_AT_4GIB = 16


@dataclass(frozen=True)
class PolicySpec:
    label: str
    version: str
    causal: bool
    configuration: Dict[str, Any]
    factory: Callable[[Trace], Policy] = field(repr=False)

    def build(self, trace: Trace) -> Policy:
        return self.factory(trace)

    def metadata(self, *, capacity_bytes: int) -> Dict[str, Any]:
        """Section 17's required policy metadata record."""
        return {
            "name": self.label,
            "version": self.version,
            "causal": self.causal,
            "capacity_bytes": int(capacity_bytes),
            "configuration": dict(self.configuration),
        }


DECLARED_POLICIES: List[PolicySpec] = [
    PolicySpec(
        label=LABEL_RUNTIME_SHARDED_LRU, version="1", causal=True,
        configuration={"base_policy": "sharded-lru", "shards": RUNTIME_SHARD_COUNT_AT_4GIB,
                      "seed": 0,
                      "note": "statistical shard-count match to the real runtime's own "
                              "16-shard architecture at 4 GiB (R2-01B); the shard-to-key "
                              "MAPPING is not the runtime's exact one -- the runtime hashes "
                              "real memory addresses, not reproducible offline (see "
                              "ShardedLRU's own docstring)."},
        factory=lambda trace: ShardedLRU(shards=RUNTIME_SHARD_COUNT_AT_4GIB, seed=0),
    ),
    PolicySpec(
        label=LABEL_GLOBAL_LRU, version="1", causal=True,
        configuration={"base_policy": "global-lru"},
        factory=lambda trace: GlobalLRU(),
    ),
    PolicySpec(
        label=LABEL_LFU, version="1", causal=True,
        configuration={"base_policy": "lfu", "halflife": 0, "in_cache_only": False},
        factory=lambda trace: LFU(halflife=0, in_cache_only=False),
    ),
    PolicySpec(
        label=LABEL_ORACLE_GLOBAL_BELADY, version="1", causal=False,
        configuration={"base_policy": "belady-global", "phase": "decode",
                       "semantics": "v2",
                       "note": "non-causal by construction: built from the trace's FULL "
                               "future event list (argine.research.oracle.event_list / "
                               "next_uses) before replay starts; never deployable."},
        factory=lambda trace: BeladyGlobal(trace, phase=PHASE_DECODE, semantics="v2"),
    ),
]

CAUSAL_BASELINE_LABELS = [p.label for p in DECLARED_POLICIES if p.causal]
ORACLE_LABEL = next(p.label for p in DECLARED_POLICIES if not p.causal)


def spec_by_label(label: str) -> PolicySpec:
    for p in DECLARED_POLICIES:
        if p.label == label:
            return p
    raise KeyError(f"no declared R2-02 policy named {label!r}")
