"""Research 5 Phase 2 -- frozen mechanism metrics (R5-00 ENDPOINTS.json "mechanism") from the traced arms B, C0_I, C_I.

    validate(lines, arm)  telemetry validity only (pass/fail, no metric). Used right after each traced launch.
    metrics(lines, arm)   the frozen per-launch mechanism quantities. Used ONLY by the one-shot Phase 2 analysis.

Critical-path rule (frozen): a node spans min(hook_in)..max(hook_out) over its threads; its wait is the MAXIMUM over
threads of (wait_end - wait_start). Thread 0 alone, means over threads and sums over threads are never used.

Operational choices (pre-declared in PROTOCOL_2.json, before any evaluation data exist):
  * decode tokens = the one-token graphs after the last multi-token (prefill) graph; a launch has 511 (the first of the
    512 tokens comes from the prompt's last graph), as in Phase 1b; if the prompt's last micro-batch itself had one token,
    the first of 512 trailing one-token graphs is that prefill graph and is excluded; per-token values divide by 511.
  * exposed_wait_ms_per_token = sum over decode expert nodes of max_thread(wait_end - wait_start), / decode tokens.
  * exposed_store_ms_per_token = sum over decode expert nodes of (max_thread hook_out - min_thread hook_in), / tokens.
  * a read belongs to the node whose window [min hook_in, max hook_out] contains its issue time (exactly one, else the
    trace is invalid); it is DEFERRED if its kind differs from the issuing node's kind (ENDPOINTS: "reads of kinds not
    consumed by the issuing node"); its consuming node is the node of the same layer event with the read's kind.
  * hidden (C_I only): a deferred read whose recorded completion (exact, or an upper bound: flag 2) is at or before the
    earliest wait start over the consuming node's threads; a read flagged SYNCHRONOUS (4), FALLBACK (8) or
    SYNC_IN_ISSUE (16) was paid synchronously and is never hidden. If no thread of the consuming node recorded a wait,
    its earliest hook entry is used (stricter). hidden_deferred_bytes_fraction = hidden bytes / deferred bytes; a C_I
    launch with no deferred byte counts as 0 (conservative; G4 (b) cannot pass on undefined values).
  * the telemetry checks are those of the Phase 1b estimator, with the arm's own trace mode (layer 0, layer_touch 1,
    kind 2) and configuration echo instead of the baseline's.
"""
from __future__ import annotations

import gzip
import importlib.util
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ETS_TRACE = HERE.parents[0] / "R5-01-ets-implementation" / "ets_trace.py"
EXPECTED_THREADS = 12
DECODE_GRAPHS = 511
NOT_HIDDEN = 4 | 8 | 16
MODE = {"layer": 0, "layer_touch": 1, "kind": 2}
TRACED_ARMS = {"B": "layer", "C0_I": "layer_touch", "C_I": "kind"}


class TelemetryError(ValueError):
    """The trace cannot support the mechanism metrics: fail closed, never guessed."""


def _ets():
    if "ets_trace" in sys.modules:
        return sys.modules["ets_trace"]
    spec = importlib.util.spec_from_file_location("ets_trace", ETS_TRACE)
    mod = importlib.util.module_from_spec(spec)            # type: ignore[arg-type]
    sys.modules["ets_trace"] = mod
    spec.loader.exec_module(mod)                           # type: ignore[union-attr]
    return mod


def read_lines(path: Path) -> List[str]:
    op = gzip.open if str(path).endswith(".gz") else open
    try:
        with op(path, "rt", encoding="utf-8", errors="strict", newline="") as fh:
            return fh.read().splitlines()
    except (OSError, UnicodeDecodeError, EOFError) as e:
        raise TelemetryError(f"unreadable trace {path}: {type(e).__name__}: {e}") from e


@dataclass
class Checked:
    dec: set
    nodes: Dict[Tuple[int, int, int], List]
    events: Dict[Tuple[int, int], List[Tuple[int, int, int]]]       # (graph, layer) -> node keys ordered by min hook_in
    reads_at: Dict[Tuple[int, int, int], List]                        # issuing node key -> reads
    n_reads: int = 0


def _check(lines: Iterable[str], wait_mode: str, expected_decode_tokens: Optional[int] = DECODE_GRAPHS) -> Checked:
    if wait_mode not in MODE:
        raise TelemetryError(f"unknown arm wait mode {wait_mode!r}")
    log = _ets().parse_lines(lines)
    if log.bad_lines:
        raise TelemetryError(f"{log.bad_lines} unparseable records")
    if log.config.get("ets_schema") != "r5-ets-v1":
        raise TelemetryError(f"trace schema {log.config.get('ets_schema')!r}, expected r5-ets-v1")
    if log.config.get("expert_wait") != wait_mode:
        raise TelemetryError(f"trace arm {log.config.get('expert_wait')!r}, expected {wait_mode!r}")
    if not log.t:
        raise TelemetryError("no T records")
    one = sorted(g for g, n in log.graph_tokens.items() if n == 1)
    multi = [g for g, n in log.graph_tokens.items() if n != 1]
    tail = [g for g in one if not multi or g > max(multi)]
    if expected_decode_tokens is not None and len(tail) == expected_decode_tokens + 1:
        tail = tail[1:]                  # the prompt's last micro-batch had one token: it is the prefill graph of the first token
    dec = set(tail)
    if not dec:
        raise TelemetryError("no decode graph")
    if expected_decode_tokens is not None and len(dec) != expected_decode_tokens:
        raise TelemetryError(f"{len(dec)} decode graphs, expected {expected_decode_tokens}")
    nodes: Dict[Tuple[int, int, int], List] = defaultdict(list)
    for r in log.t:
        if not (r.hook_in <= r.hook_out) or (r.wait_start and not (r.hook_in <= r.wait_start <= r.wait_end <= r.hook_out)) \
                or (not r.wait_start and r.wait_end):
            raise TelemetryError(f"incoherent T record (graph {r.graph}, layer {r.layer}, kind {r.kind}, tid {r.tid})")
        if r.mode != MODE[wait_mode]:
            raise TelemetryError(f"T record of mode {r.mode} in a {wait_mode} trace")
        nodes[(r.graph, r.layer, r.kind)].append(r)
    tidset = None
    for k, rs in nodes.items():
        ids = [x.tid for x in rs]
        if len(ids) != EXPECTED_THREADS or len(set(ids)) != EXPECTED_THREADS:
            raise TelemetryError(f"node {k}: {len(ids)} T records with {len(set(ids))} distinct thread ids, expected {EXPECTED_THREADS}")
        if tidset is None:
            tidset = set(ids)
        elif set(ids) != tidset:
            raise TelemetryError(f"node {k}: thread-id set differs from the other nodes")
        if len({x.first for x in rs}) != 1:
            raise TelemetryError(f"node {k}: inconsistent first flag")
    windows: Dict[int, List[Tuple[int, int, int, int]]] = defaultdict(list)
    for (g, l, k), rs in nodes.items():
        windows[l].append((min(r.hook_in for r in rs), max(r.hook_out for r in rs), g, k))
    for l in windows:
        windows[l].sort()
        for a, b in zip(windows[l], windows[l][1:]):
            if b[0] < a[1]:
                raise TelemetryError(f"overlapping node windows on layer {l}")
    reads_at: Dict[Tuple[int, int, int], List] = defaultdict(list)
    for r in log.i:
        if r.issue > r.complete or r.ok != 1:
            raise TelemetryError(f"incoherent or failed read {r.req}")
        if r.tid not in tidset:
            raise TelemetryError(f"read {r.req} by thread {r.tid}, which has no T records")
        hit = [w for w in windows.get(r.layer, []) if w[0] <= r.issue <= w[1]]
        if len(hit) != 1:
            raise TelemetryError(f"read {r.req} (layer {r.layer}) not attributable to exactly one node window")
        reads_at[(hit[0][2], r.layer, hit[0][3])].append(r)
    events: Dict[Tuple[int, int], List[Tuple[int, int, int]]] = defaultdict(list)
    for key in nodes:
        events[(key[0], key[1])].append(key)
    n_events = 0
    for (g, l), keys in events.items():
        keys.sort(key=lambda k: min(r.hook_in for r in nodes[k]))
        if g not in dec:
            continue
        firsts = [k for k in keys if nodes[k][0].first == 1]
        if len(firsts) != 1 or firsts[0] != keys[0]:
            raise TelemetryError(f"event (graph {g}, layer {l}): {len(firsts)} first nodes, or the first node is not the earliest")
        if len(keys) < 2:
            raise TelemetryError(f"event (graph {g}, layer {l}) has a single expert node")
        if len({k[2] for k in keys}) != len(keys):
            raise TelemetryError(f"event (graph {g}, layer {l}) has two nodes of the same kind")
        n_events += 1
    if n_events == 0:
        raise TelemetryError("no decode layer event")
    for (g, l), keys in events.items():               # every deferred read must have its consuming node (checked at acquisition)
        if g not in dec:
            continue
        kinds = {k[2] for k in keys}
        for k in keys:
            for r in reads_at.get(k, []):
                if r.kind != k[2] and r.kind not in kinds:
                    raise TelemetryError(f"deferred read {r.req} (graph {g}, layer {l}) has no consuming node of kind {r.kind}")
    return Checked(dec=dec, nodes=nodes, events=events, reads_at=reads_at, n_reads=len(log.i))


def validate(lines: Iterable[str], wait_mode: str, expected_decode_tokens: Optional[int] = DECODE_GRAPHS) -> Dict[str, object]:
    """Telemetry validity only (pass/fail). Never computes or returns a mechanism metric."""
    c = _check(lines, wait_mode, expected_decode_tokens)
    return {"valid": True, "decode_graphs": len(c.dec), "events": sum(1 for (g, _l) in c.events if g in c.dec), "reads": c.n_reads}


@dataclass
class LaunchMechanism:
    decode_tokens: int
    exposed_wait_ns: int
    exposed_store_ns: int
    deferred_bytes: int
    hidden_deferred_bytes: int
    deferred_reads: int
    not_hidden_sync_reads: int
    bounded_deferred_reads: int

    @property
    def exposed_wait_ms_per_token(self) -> float:
        return self.exposed_wait_ns / self.decode_tokens / 1e6

    @property
    def exposed_store_ms_per_token(self) -> float:
        return self.exposed_store_ns / self.decode_tokens / 1e6

    @property
    def deferred_read_bytes_per_token(self) -> float:
        return self.deferred_bytes / self.decode_tokens

    @property
    def hidden_deferred_bytes_fraction(self) -> float:
        return self.hidden_deferred_bytes / self.deferred_bytes if self.deferred_bytes else 0.0

    def as_dict(self) -> Dict[str, object]:
        d = asdict(self)
        d.update(exposed_wait_ms_per_token=self.exposed_wait_ms_per_token, exposed_store_ms_per_token=self.exposed_store_ms_per_token,
                 deferred_read_bytes_per_token=self.deferred_read_bytes_per_token,
                 hidden_deferred_bytes_fraction=self.hidden_deferred_bytes_fraction)
        return d


def metrics(lines: Iterable[str], wait_mode: str, expected_decode_tokens: Optional[int] = DECODE_GRAPHS) -> LaunchMechanism:
    c = _check(lines, wait_mode, expected_decode_tokens)
    out = LaunchMechanism(decode_tokens=len(c.dec), exposed_wait_ns=0, exposed_store_ns=0, deferred_bytes=0, hidden_deferred_bytes=0,
                          deferred_reads=0, not_hidden_sync_reads=0, bounded_deferred_reads=0)
    for (g, l), keys in c.events.items():
        if g not in c.dec:
            continue
        by_kind = {k[2]: k for k in keys}
        for k in keys:
            rs = c.nodes[k]
            out.exposed_store_ns += max(r.hook_out for r in rs) - min(r.hook_in for r in rs)
            out.exposed_wait_ns += max((r.wait_end - r.wait_start for r in rs if r.wait_start > 0), default=0)
        for k in keys:
            for r in c.reads_at.get(k, []):
                if r.kind == k[2]:
                    continue                     # consumed by the issuing node: not deferred
                cons = by_kind.get(r.kind)
                if cons is None:
                    raise TelemetryError(f"deferred read {r.req} (graph {g}, layer {l}) has no consuming node of kind {r.kind}")
                out.deferred_reads += 1
                out.deferred_bytes += r.bytes
                out.bounded_deferred_reads += bool(r.flags & 2)
                if r.flags & NOT_HIDDEN:
                    out.not_hidden_sync_reads += 1
                    continue
                crs = c.nodes[cons]
                starts = [x.wait_start for x in crs if x.wait_start > 0]
                needed = min(starts) if starts else min(x.hook_in for x in crs)
                if r.complete <= needed:
                    out.hidden_deferred_bytes += r.bytes
    return out
