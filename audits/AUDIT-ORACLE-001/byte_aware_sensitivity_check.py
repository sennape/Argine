"""Deterministic offline byte-aware clairvoyant sensitivity check for AUDIT-ORACLE-001.

Falsificatory only: if any comparator beats canonical R2-02 Oracle-Global-Belady
miss bytes on the same trace/budget/semantics, that is a counterexample to O_B
byte-optimality. A non-beating result is NO_COUNTEREXAMPLE_FOUND, not optimality.

No model execution, no network, no trace mutation.
"""
from __future__ import annotations

import heapq
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argine.research.oracle import event_list, next_uses, INF
from argine.research.replay import make_context, replay
from argine.research.trace import Trace, PHASE_DECODE


TRACE = Path(r"C:\Users\alpel\AppData\Local\Argine3\research\R2-01\captures\286a41477ce54d5fb9ccc24f4e508f22\run-00-4G\trace.jsonl.gz")
BUDGET = 4 * (1 << 30)
CANONICAL_O_B_MISS_BYTES = 42296352768
CANONICAL_O_B_MISSES = 14754


class ClairvoyantBase:
    family = "oracle-sensitivity"
    needs_future = True
    category = "audit-sensitivity"

    def __init__(self, trace: Trace, name: str, phase=PHASE_DECODE, semantics="v2"):
        self.events = event_list(trace, phase, semantics)
        self.nu = next_uses(self.events)
        self.name = name

    def start(self, ctx):
        self.ctx = ctx
        self.j = 0
        self.version = {}
        self.res = {}
        self.resident_bytes = 0
        self.evictions = 0
        self.overflow_events = 0
        self.overflow_misses = 0
        self.monitor_s = 0.0

    def describe(self):
        return {"name": self.name, "audit": "AUDIT-ORACLE-001"}

    def allocation(self):
        return None

    def end_step(self, step):
        return None

    def access(self, step, layer, experts):
        return self._access(step, layer, experts)

    def _expect(self, step, layer, experts):
        i, l, ex = self.events[self.j]
        if i != step or l != layer or tuple(experts) != tuple(ex):
            raise RuntimeError(f"{self.name}: replay divergence at j={self.j}")
        nu = self.nu[self.j]
        self.j += 1
        return nu


class BeladySizeTieBreak(ClairvoyantBase):
    """Furthest next use; ties broken by evicting larger object (frees bytes)."""

    def start(self, ctx):
        super().start(ctx)
        self.heap = []  # (-next_use, -size, version, key)

    def _access(self, step, layer, experts):
        nu = self._expect(step, layer, experts)
        b = self.ctx.bytes_of(layer)
        members = {(layer, e) for e in experts}
        misses = []
        overflow = False
        for e in experts:
            k = (layer, e)
            v = self.version.get(k, 0) + 1
            self.version[k] = v
            size = b
            if k in self.res:
                heapq.heappush(self.heap, (-nu[e], -size, v, k))
                continue
            misses.append(e)
            ok = True
            held = []
            while self.resident_bytes + b > self.ctx.budget:
                victim = None
                while self.heap:
                    negn, negs, ver, kk = heapq.heappop(self.heap)
                    if kk not in self.res or self.version.get(kk) != ver:
                        continue
                    if kk in members:
                        held.append((negn, negs, ver, kk))
                        continue
                    victim = kk
                    break
                if victim is None:
                    ok = False
                    break
                self.resident_bytes -= self.res.pop(victim)
                self.evictions += 1
            for h in held:
                heapq.heappush(self.heap, h)
            if ok:
                self.res[k] = b
                self.resident_bytes += b
                heapq.heappush(self.heap, (-nu[e], -size, v, k))
            else:
                overflow = True
                self.overflow_misses += 1
        if overflow:
            self.overflow_events += 1
        return misses


class BeladyByteDensity(ClairvoyantBase):
    """Clairvoyant heuristic: evict max(next_use / size), i.e. keep objects with
    nearer next use per byte. INF next use stays most evictable. Ties by larger size.
    Not claimed optimal."""

    def start(self, ctx):
        super().start(ctx)
        self.heap = []  # (-score, -size, version, key)

    @staticmethod
    def score(next_use, size):
        if next_use == INF:
            return INF
        return next_use / max(1, size)

    def _access(self, step, layer, experts):
        nu = self._expect(step, layer, experts)
        b = self.ctx.bytes_of(layer)
        members = {(layer, e) for e in experts}
        misses = []
        overflow = False
        for e in experts:
            k = (layer, e)
            v = self.version.get(k, 0) + 1
            self.version[k] = v
            size = b
            if k in self.res:
                sc = self.score(nu[e], size)
                heapq.heappush(self.heap, (-sc, -size, v, k))
                continue
            misses.append(e)
            ok = True
            held = []
            while self.resident_bytes + b > self.ctx.budget:
                victim = None
                while self.heap:
                    negsc, negs, ver, kk = heapq.heappop(self.heap)
                    if kk not in self.res or self.version.get(kk) != ver:
                        continue
                    if kk in members:
                        held.append((negsc, negs, ver, kk))
                        continue
                    victim = kk
                    break
                if victim is None:
                    ok = False
                    break
                self.resident_bytes -= self.res.pop(victim)
                self.evictions += 1
            for h in held:
                heapq.heappush(self.heap, h)
            if ok:
                self.res[k] = b
                self.resident_bytes += b
                sc = self.score(nu[e], size)
                heapq.heappush(self.heap, (-sc, -size, v, k))
            else:
                overflow = True
                self.overflow_misses += 1
        if overflow:
            self.overflow_events += 1
        return misses


def main():
    trace = Trace.load(TRACE)
    results = []
    for cls, name in [
        (BeladySizeTieBreak, "belady-size-tiebreak"),
        (BeladyByteDensity, "belady-byte-density"),
    ]:
        pol = cls(trace, name, phase=PHASE_DECODE, semantics="v2")
        rr = replay(trace, pol, BUDGET, phase=PHASE_DECODE, semantics="v2")
        row = {
            "comparator": name,
            "misses": rr.misses,
            "miss_bytes": rr.bytes_missed,
            "evictions": rr.evictions,
            "peak_resident_bytes": rr.peak_resident,
            "budget_violations": 0,  # replay raises on violation; reaching here means none
            "beats_canonical_O_B_miss_bytes": rr.bytes_missed < CANONICAL_O_B_MISS_BYTES,
            "beats_canonical_O_B_misses": rr.misses < CANONICAL_O_B_MISSES,
            "delta_miss_bytes_vs_O_B": rr.bytes_missed - CANONICAL_O_B_MISS_BYTES,
            "delta_misses_vs_O_B": rr.misses - CANONICAL_O_B_MISSES,
        }
        results.append(row)
        print(json.dumps(row))
    beaten = any(r["beats_canonical_O_B_miss_bytes"] for r in results)
    summary = {
        "audit": "AUDIT-ORACLE-001",
        "trace": str(TRACE),
        "budget": BUDGET,
        "phase": "decode",
        "semantics": "v2",
        "canonical_O_B_miss_bytes": CANONICAL_O_B_MISS_BYTES,
        "canonical_O_B_misses": CANONICAL_O_B_MISSES,
        "comparators": results,
        "classification": "BELADY_BEATEN" if beaten else "NO_COUNTEREXAMPLE_FOUND",
        "interpretation": "Falsificatory only. BELADY_BEATEN => O_B not byte-optimal on this canonical trace. NO_COUNTEREXAMPLE_FOUND => no counterexample found by these heuristics; does NOT establish optimality or near-optimality.",
    }
    out = ROOT / "audits" / "AUDIT-ORACLE-001" / "byte_aware_sensitivity.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("CLASS=" + summary["classification"])


if __name__ == "__main__":
    main()
