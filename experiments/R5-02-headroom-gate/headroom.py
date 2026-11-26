"""Research 5 Phase 1b -- the headroom bound H (R5-00 ENDPOINTS.json headroom_bound_H) and gate G2.

Frozen definition (ENDPOINTS.json): for every decode layer event, T_tail = time from the latest completion (over
threads) of a first-node-kind read to the latest completion of any read issued at the first node, clipped to the first
node's critical-path wait interval; W = gap from the last thread leaving the first node's hook to the first thread
entering the hook of the layer's last expert node; H_layer = min(T_tail, W). H = sum of H_layer / decode tokens,
expressed as a fraction of the mean decode ms/token of the same 8 launches. G2 (DECISION_RULES.json): one-shot,
H >= 0.04, otherwise R5_NO_MEANINGFUL_OVERLAP_HEADROOM. H is an optimistic bound, never an achievable gain.

Operational choices, fixed here before any Phase 1b data exist (PROTOCOL_1B.json repeats them; revised after the
independent review, still before any data):
  * critical-path wait interval of the first node = [wait_start, wait_end] of the thread whose wait ENDS LAST (ties ->
    the longer wait): that thread ends the node's store phase (ENDPOINTS critical_path_rule: "ends when the last thread
    leaves"). Its clipped tail is at least what moving the wait could save at the first node, so H stays an upper
    bound, and it is at most that thread's wait, so H_layer <= the first node's critical-path (maximum) wait. It may
    count time other threads spend issuing; that errs optimistic, as the frozen "optimistic, loose" bound allows.
  * "reads issued at the first node" = I records of the event's layer whose issue time lies in the first node's
    critical-path window [min hook_in, max hook_out]; every decode-event read must be attributable to exactly one node
    window of its layer (else the telemetry is invalid).
  * a read that was not an overlapped asynchronous transfer -- completed inside its issuing call (flag 16), synchronous
    (flag 4) or a synchronous fallback (flag 8) -- cannot run behind compute: it still marks when first-kind data are
    ready (c_first), but it never extends the deferred tail (c_all). A bounded completion (flag 2: the recorded time is
    an upper bound observed at a poll/wait; the read finished between issue and that time) is taken at its most
    optimistic end on each side: its recorded time on the deferred side (c_all) and its ISSUE time on the first-kind
    side (c_first), so a late poll can never shrink H. Bounded reads are counted.
  * T_tail lies inside a wait interval and W is a hook gap (the frozen text's compute window; it may contain the middle
    nodes' own waits, which only makes H more optimistic), so read/read overlap is never counted as
    read/compute overlap; one H_layer per layer event, so overlapping reads are never counted twice.
  * units: a decode token is a decode graph of the trace, which must number predicted_n - 1 (the first token comes from
    the prompt graph); llama-server's predicted_ms spans exactly those decode steps (its own per-token figure divides by
    predicted_n - 1). Numerator and denominator therefore use the same decode steps: per launch the H fraction is
    sum(H_layer) / predicted_ms, and over the 8 launches (equal token counts) H = sum of all H_layer / sum of the 8
    predicted_ms, i.e. the pooled H_ms_per_token over the mean decode ms per decode step. The gate is decided in exact
    rational arithmetic: H >= 1/25 (inclusive).
  * thread identity: the trace's thread id is a process-wide first-come counter (arx_thread_id), not ggml's thread
    index: every node must carry exactly 12 T records with 12 distinct ids, the same id set in every node, and every
    read's thread id must be in that set.
"""
from __future__ import annotations

import gzip
import importlib.util
import sys
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ETS_TRACE = REPO / "experiments" / "R5-01-ets-implementation" / "ets_trace.py"
HEADROOM_GATE = Fraction(1, 25)     # R5-00 DECISION_RULES thresholds.headroom_gate = 0.04 (never changed)
SYNC_IN_ISSUE = 16
COMPLETE_BOUND = 2
NOT_OVERLAPPED = 4 | 8 | 16          # synchronous, synchronous fallback, completed inside the issuing call
EXPECTED_THREADS = 12               # WORKLOADS launch.threads
LABEL_PASS = "R5_G2_HEADROOM_GATE_PASS"
LABEL_FAIL = "R5_NO_MEANINGFUL_OVERLAP_HEADROOM"


class TelemetryError(ValueError):
    """The trace cannot support H: the gate is not computed (fail closed), never guessed."""


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
    except (OSError, UnicodeDecodeError, EOFError) as e:      # truncated gzip, bad bytes, missing file
        raise TelemetryError(f"unreadable trace {path}: {type(e).__name__}: {e}") from e


@dataclass
class LaunchH:
    decode_tokens: int
    h_ns: int                    # sum of H_layer over decode events
    events: int
    events_positive: int
    tail_ns: int                 # sum of T_tail (diagnostic)
    gap_ns: int                  # sum of W (diagnostic)
    first_wait_ns: int           # sum of the first nodes' critical-path (maximum) waits: H can never exceed it
    first_node_reads: int
    not_overlapped_reads: int    # flags 4/8/16 among first-node reads (never extend the tail)
    bounded_completion_reads: int


@dataclass
class Checked:
    dec: set
    events: Dict[Tuple[int, int], List[List]]
    reads_at: Dict[Tuple[int, int, int], List]


def _check(lines: Iterable[str], expected_decode_tokens: Optional[int]) -> Checked:
    """Every telemetry validity check (raises TelemetryError). Computes no H."""
    et = _ets()
    log = et.parse_lines(lines)
    if log.bad_lines:
        raise TelemetryError(f"{log.bad_lines} unparseable records")
    if log.config.get("ets_schema") != "r5-ets-v1":
        raise TelemetryError(f"trace schema {log.config.get('ets_schema')!r}, expected r5-ets-v1")
    if log.config.get("expert_wait") != "layer":
        raise TelemetryError(f"trace arm {log.config.get('expert_wait')!r}: H is defined on the baseline arm B (layer)")
    if not log.t:
        raise TelemetryError("no T records")
    dec = {g for g, n in log.graph_tokens.items() if n == 1}
    if not dec:
        raise TelemetryError("no decode graph")
    if expected_decode_tokens is not None and len(dec) != expected_decode_tokens:
        raise TelemetryError(f"{len(dec)} decode graphs, expected {expected_decode_tokens}")
    nodes: Dict[Tuple[int, int, int], List] = defaultdict(list)
    for r in log.t:
        if not (r.hook_in <= r.hook_out) or (r.wait_start and not (r.hook_in <= r.wait_start <= r.wait_end <= r.hook_out)) \
                or (not r.wait_start and r.wait_end):
            raise TelemetryError(f"incoherent T record (graph {r.graph}, layer {r.layer}, kind {r.kind}, tid {r.tid})")
        if r.mode != 0:
            raise TelemetryError("T record of a non-layer arm in a B trace")
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
    events: Dict[Tuple[int, int], List[List]] = defaultdict(list)
    for (g, l, _k), rs in nodes.items():
        events[(g, l)].append(rs)
    windows: Dict[int, List[Tuple[int, int, int, int]]] = defaultdict(list)   # layer -> (in_min, out_max, graph, kind)
    for (g, l, k), rs in nodes.items():
        windows[l].append((min(r.hook_in for r in rs), max(r.hook_out for r in rs), g, k))
    for l in windows:
        windows[l].sort()
        for a, b in zip(windows[l], windows[l][1:]):
            if b[0] < a[1]:
                raise TelemetryError(f"overlapping node windows on layer {l}")
    reads_at: Dict[Tuple[int, int, int], List] = defaultdict(list)                  # (graph, layer, kind of the issuing node)
    for r in log.i:
        if r.issue > r.complete or r.ok != 1:
            raise TelemetryError(f"incoherent or failed read {r.req}")
        if r.tid not in tidset:
            raise TelemetryError(f"read {r.req} by thread {r.tid}, which has no T records")
        hit = [w for w in windows.get(r.layer, []) if w[0] <= r.issue <= w[1]]
        if len(hit) != 1:       # every read is issued inside a hook: an unattributable read means incomplete telemetry
            raise TelemetryError(f"read {r.req} (layer {r.layer}) not attributable to exactly one node window")
        reads_at[(hit[0][2], r.layer, hit[0][3])].append(r)
    n_events = 0
    for (g, l), ns in events.items():
        if g not in dec:
            continue
        ns.sort(key=lambda rs: min(r.hook_in for r in rs))
        firsts = [rs for rs in ns if max(r.first for r in rs) == 1]
        if len(firsts) != 1 or firsts[0] is not ns[0]:
            raise TelemetryError(f"event (graph {g}, layer {l}): {len(firsts)} first nodes, or the first node is not the earliest")
        if len(ns) < 2:
            raise TelemetryError(f"event (graph {g}, layer {l}) has a single expert node")
        n_events += 1
    if n_events == 0:
        raise TelemetryError("no decode layer event")
    return Checked(dec=dec, events=events, reads_at=reads_at)


def validate(lines: Iterable[str], expected_decode_tokens: Optional[int] = None) -> Dict[str, object]:
    """Telemetry validity only (pass/fail). Used right after each launch; never computes or returns H."""
    c = _check(lines, expected_decode_tokens)
    return {"valid": True, "decode_graphs": len(c.dec), "events": sum(1 for (g, _l) in c.events if g in c.dec)}


def launch_h(lines: Iterable[str], expected_decode_tokens: Optional[int] = None) -> LaunchH:
    """H for one B launch (after _check). Called only by the one-shot gate."""
    c = _check(lines, expected_decode_tokens)
    out = LaunchH(decode_tokens=len(c.dec), h_ns=0, events=0, events_positive=0, tail_ns=0, gap_ns=0, first_wait_ns=0,
                  first_node_reads=0, not_overlapped_reads=0, bounded_completion_reads=0)
    for (g, l), ns in c.events.items():
        if g not in c.dec:
            continue
        first, last = ns[0], ns[-1]
        out.events += 1
        first_kind = first[0].kind
        reads = c.reads_at.get((g, l, first_kind), [])
        out.first_node_reads += len(reads)
        out.not_overlapped_reads += sum(1 for r in reads if r.flags & NOT_OVERLAPPED)
        out.bounded_completion_reads += sum(1 for r in reads if r.flags & COMPLETE_BOUND)
        gap = max(0, min(r.hook_in for r in last) - max(r.hook_out for r in first))
        out.gap_ns += gap
        waits = [r for r in first if r.wait_start > 0]
        if not waits:
            continue
        out.first_wait_ns += max(r.wait_end - r.wait_start for r in waits)
        crit = max(waits, key=lambda r: (r.wait_end, r.wait_end - r.wait_start))      # the thread whose wait ends last
        ws, we = crit.wait_start, crit.wait_end
        first_kind_c = [r.issue if r.flags & COMPLETE_BOUND else r.complete for r in reads if r.kind == first_kind]
        deferred_c = [r.complete for r in reads if not (r.flags & NOT_OVERLAPPED)]
        if not deferred_c:
            continue
        lo = max(max(first_kind_c, default=ws), ws)
        hi = min(max(deferred_c), we)
        tail = max(0, hi - lo)
        h = min(tail, gap)
        out.tail_ns += tail
        out.h_ns += h
        out.events_positive += h > 0
    if out.h_ns > out.first_wait_ns:
        raise TelemetryError("H exceeds the first nodes' critical-path wait (estimator invariant violated)")
    return out


def decide(launches: Sequence[LaunchH], predicted: Sequence[Tuple[float, int]]) -> Dict[str, object]:
    """G2 from the 8 launches. predicted: (predicted_ms, predicted_n) per launch, same order; each launch's decode graphs
    must number predicted_n - 1, so that predicted_ms and the H_layer cover the same decode steps."""
    if len(launches) != 8 or len(predicted) != 8:
        raise TelemetryError("G2 is defined on exactly 8 B launches")
    for x, (ms, n) in zip(launches, predicted):
        if not (ms > 0 and n > 1):
            raise TelemetryError("missing or invalid server decode timing")
        if x.decode_tokens != n - 1:
            raise TelemetryError(f"{x.decode_tokens} decode graphs for predicted_n {n} (expected predicted_n - 1)")
    h_ms = Fraction(sum(x.h_ns for x in launches), 1_000_000)
    dec_ms = sum((Fraction(ms) for ms, _ in predicted), Fraction(0))
    steps = sum(x.decode_tokens for x in launches)
    H = h_ms / dec_ms
    passed = H >= HEADROOM_GATE
    return {
        "H": float(H), "H_exact": f"{H.numerator}/{H.denominator}", "threshold": float(HEADROOM_GATE),
        "H_ms_per_decode_step": float(h_ms / steps), "decode_ms_per_decode_step_mean": float(dec_ms / steps),
        "gate_G2": "PASS" if passed else "FAIL", "label": LABEL_PASS if passed else LABEL_FAIL,
        "per_launch_H_fraction": [float(Fraction(x.h_ns, 1_000_000) / Fraction(ms)) for x, (ms, _n) in zip(launches, predicted)],
    }
