"""Research 5 (ETS) raw-trace analysis: critical-path mechanism metrics from the R5 trace records.

Reads the "argine-raw" log written by ARGINE_RESEARCH_TRACE (patches/r5-ets/argine-research.h documents the
records). Two entry points, deliberately separated:

  structure(path)  instrumentation completeness only: are the records needed by the frozen metrics present and
                   coherent? Returns booleans and counts, never a timing metric. This is the ONLY function Phase 1a
                   may use: it cannot reveal headroom or an arm contrast.
  metrics(path, decode_ms_per_token)
                   the frozen mechanism metrics (ENDPOINTS.json of R5-00): exposed_store_ms_per_token,
                   exposed_wait_ms_per_token, non_hook_ms_per_token, hidden_deferred_bytes_fraction,
                   deferred_read_bytes_per_token, and the headroom bound H. For Phase 1b (H, one shot) and Phase 2.

Critical-path rule (frozen): a node starts when the FIRST thread enters the hook and its store phase ends when the
LAST thread leaves it; its wait is the MAXIMUM over threads. Means or sums over threads are never used.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

KINDS = {0: "gate", 1: "up", 2: "down"}
SYNC_IN_ISSUE = 16   # I-record flag: completed inside ReadFile (R5)


@dataclass
class TRec:
    graph: int; layer: int; kind: int; tid: int; mode: int; first: int
    hook_in: int; wait_start: int; wait_end: int; hook_out: int
    wait_mask: int; n_awaited: int; bytes_awaited: int; n_done: int; bytes_done: int; pending_after: int


@dataclass
class IRec:
    req: int; tid: int; layer: int; expert: int; kind: int; bytes: int
    issue: int; complete: int; wait_start: int; wait_end: int; ok: int; flags: int


@dataclass
class Log:
    t: List[TRec] = field(default_factory=list)
    i: List[IRec] = field(default_factory=list)
    graph_tokens: Dict[int, int] = field(default_factory=dict)   # graph -> n_tokens (from R records)
    config: Dict[str, str] = field(default_factory=dict)
    bad_lines: int = 0


def parse_lines(lines: Iterable[str]) -> Log:
    log = Log()
    for raw in lines:
        s = raw.rstrip("\r\n")
        if not s:
            continue
        f = s.split("\t")
        try:
            if f[0] == "T":
                v = [int(x) for x in f[1:17]]
                if len(v) != 16:
                    raise ValueError
                log.t.append(TRec(*v))
            elif f[0] == "I":
                v = [int(x) for x in f[1:13]]
                if len(v) != 12:
                    raise ValueError
                log.i.append(IRec(*v))
            elif f[0] == "R":
                log.graph_tokens.setdefault(int(f[1]), int(f[4]))
            elif f[0] == "C" and len(f) >= 3:
                log.config[f[1]] = f[2]
        except (ValueError, IndexError):
            log.bad_lines += 1
    return log


def parse(path: str | Path) -> Log:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse_lines(fh)


@dataclass
class Node:
    graph: int; layer: int; kind: int; first: int
    in_min: int; out_max: int; wait_max: int; wait_start_min: int; wait_end_max: int; threads: int


def nodes(log: Log) -> Dict[Tuple[int, int, int], Node]:
    g: Dict[Tuple[int, int, int], List[TRec]] = defaultdict(list)
    for r in log.t:
        g[(r.graph, r.layer, r.kind)].append(r)
    out = {}
    for k, rs in g.items():
        waits = [r for r in rs if r.wait_start > 0 and r.wait_end >= r.wait_start]
        out[k] = Node(graph=k[0], layer=k[1], kind=k[2], first=max(r.first for r in rs),
                      in_min=min(r.hook_in for r in rs), out_max=max(r.hook_out for r in rs),
                      wait_max=max((r.wait_end - r.wait_start for r in waits), default=0),
                      wait_start_min=min((r.wait_start for r in waits), default=0),
                      wait_end_max=max((r.wait_end for r in waits), default=0), threads=len(rs))
    return out


def decode_graphs(log: Log) -> List[int]:
    return sorted(g for g, n in log.graph_tokens.items() if n == 1)


def _events(nd: Dict[Tuple[int, int, int], Node]) -> Dict[Tuple[int, int], List[Node]]:
    ev: Dict[Tuple[int, int], List[Node]] = defaultdict(list)
    for n in nd.values():
        ev[(n.graph, n.layer)].append(n)
    for v in ev.values():
        v.sort(key=lambda n: n.in_min)
    return ev


def _attribute_reads(log: Log, ev: Dict[Tuple[int, int], List[Node]]) -> Dict[Tuple[int, int], List[IRec]]:
    """Each read belongs to the layer event whose FIRST node window [in_min, out_max] contains its issue time."""
    win: Dict[int, List[Tuple[int, int, int]]] = defaultdict(list)   # layer -> (in_min, out_max, graph)
    for (g, l), ns in ev.items():
        f = ns[0]
        win[l].append((f.in_min, f.out_max, g))
    for l in win:
        win[l].sort()
    by: Dict[Tuple[int, int], List[IRec]] = defaultdict(list)
    for r in log.i:
        w = win.get(r.layer, [])
        j = bisect.bisect_right(w, (r.issue, float("inf"), 0)) - 1
        if j >= 0 and w[j][0] <= r.issue <= w[j][1]:
            by[(w[j][2], r.layer)].append(r)
    return by


def structure(path: str | Path) -> Dict[str, object]:
    """Instrumentation completeness. Booleans and counts only (no timing metric, no arm contrast)."""
    log = parse(path)
    nd = nodes(log)
    dec = set(decode_graphs(log))
    ev = _events(nd)
    by = _attribute_reads(log, ev)
    n_attr = sum(len(v) for v in by.values())
    coherent = all(r.issue <= r.complete and r.wait_start <= r.wait_end for r in log.i)
    t_coherent = all(r.hook_in <= r.hook_out and (r.wait_start == 0 or (r.hook_in <= r.wait_start <= r.wait_end <= r.hook_out))
                     and r.n_done <= r.n_awaited and r.bytes_done <= r.bytes_awaited for r in log.t)
    first_per_event = all(sum(1 for n in ns if n.first) == 1 and ns[0].first == 1 for ns in ev.values())
    threads = {n.threads for n in nd.values()}
    return {
        "records_T": len(log.t), "records_I": len(log.i), "bad_lines": log.bad_lines,
        "decode_graphs": len(dec), "moe_nodes": len(nd), "events": len(ev),
        "threads_per_node": sorted(threads),
        "expert_wait_config": log.config.get("expert_wait"), "ets_schema": log.config.get("ets_schema"),
        "checks": {
            "T_records_present": len(log.t) > 0,
            "one_T_record_per_thread_per_node": len(threads) == 1,
            "T_records_coherent": t_coherent,
            "I_records_coherent": coherent,
            "exactly_one_first_node_per_event": first_per_event,
            "every_read_attributed_to_an_event": n_attr == len(log.i),
            "decode_graphs_identified": len(dec) > 0,
            "schema_declared": log.config.get("ets_schema") == "r5-ets-v1",
            "no_unparseable_records": log.bad_lines == 0,
        },
    }


def metrics(path: str | Path, decode_ms_per_token: Optional[float] = None) -> Dict[str, object]:
    """Frozen mechanism metrics (R5-00 ENDPOINTS.json). For Phase 1b (H) and Phase 2 only."""
    return metrics_from_log(parse(path), decode_ms_per_token)


def metrics_from_log(log: Log, decode_ms_per_token: Optional[float] = None) -> Dict[str, object]:
    nd = nodes(log)
    dec = set(decode_graphs(log))
    tok = len(dec)
    if tok == 0:
        raise ValueError("no decode graph in the trace")
    ev = {k: v for k, v in _events(nd).items() if k[0] in dec}
    by = _attribute_reads(log, ev)
    store_ns = sum(n.out_max - n.in_min for ns in ev.values() for n in ns)
    wait_ns = sum(n.wait_max for ns in ev.values() for n in ns)
    deferred_b = hidden_b = 0
    h_ns = 0
    for key, ns in ev.items():
        first = ns[0]
        kind_node = {n.kind: n for n in ns}
        reads = by.get(key, [])
        for r in reads:
            if r.kind == first.kind:
                continue
            deferred_b += r.bytes
            cons = kind_node.get(r.kind)
            # hidden = completed in the background before the earliest wait of its consuming node; a read that
            # completed inside its issuing call (flag 16, SYNC_IN_ISSUE) was paid by the issuer and is never hidden
            if cons is not None and cons.wait_start_min > 0 and r.complete <= cons.wait_start_min and not (r.flags & SYNC_IN_ISSUE):
                hidden_b += r.bytes
        # headroom bound H for this event (meaningful on the baseline arm B)
        if reads and len(ns) > 1 and first.wait_end_max > 0:
            c_first = max((r.complete for r in reads if r.kind == first.kind), default=0)
            c_all = max(r.complete for r in reads)
            lo = max(c_first, first.wait_start_min)
            hi = min(c_all, first.wait_end_max)
            t_tail = max(0, hi - lo)
            w = max(0, ns[-1].in_min - first.out_max)
            h_ns += min(t_tail, w)
    out = {
        "decode_tokens": tok,
        "exposed_store_ms_per_token": store_ns / tok / 1e6,
        "exposed_wait_ms_per_token": wait_ns / tok / 1e6,
        "deferred_read_bytes_per_token": deferred_b / tok,
        "hidden_deferred_bytes_fraction": (hidden_b / deferred_b) if deferred_b else None,
        "H_ms_per_token": h_ns / tok / 1e6,
    }
    if decode_ms_per_token is not None:
        out["non_hook_ms_per_token"] = decode_ms_per_token - out["exposed_store_ms_per_token"]
        out["H_fraction_of_decode"] = out["H_ms_per_token"] / decode_ms_per_token
    return out


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) >= 3 and sys.argv[1] == "structure":
        print(json.dumps(structure(sys.argv[2]), indent=1))
    else:
        print("usage: ets_trace.py structure <trace>   (metrics are computed only by the Phase 1b / Phase 2 tools)")
        sys.exit(2)
