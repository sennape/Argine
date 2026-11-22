"""R3 event-semantics audit (read-only, no model, no policy-by-timing analysis).

Question: is the predictor event stream seen by the R3 runtime policy hook the
same semantic event stream that the R2-10 / R2-11 predictor was validated on?

R2 definition (argine/research/trace.py::Trace.events, research2 replay):
    ONE event per (forward-pass step, layer), carrying the sorted UNION of the
    experts routed in that step; access() is called once per such event.

This module reads the frozen R3-02 retention-decision traces (one JSON row per
policy->access() call) and quantifies how the runtime stream differs.

It never reads TIMING records, R3-02G Session 1 evidence or any model file.

CLI (stream the zstd trace on stdin; the repo never stores decompressed traces):
    zstd -dc B-4GiB.zst | python tools/r3_event_semantics_audit.py extract - rows.jsonl
    python tools/r3_event_semantics_audit.py analyze rows.jsonl audit_raw.json
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from typing import Dict, Iterable, Iterator, List, Sequence, Tuple

N_EXPERTS = 128
TOP_K = 8
PREDICTOR_DECAY = 0.99
_PREFIX_END = b',"resident_before"'


# ----------------------------------------------------------------- extract --
def extract_rows(lines: Iterable[bytes]) -> Iterator[dict]:
    """Yield one compact dict per policy decision row of an R3 retention trace.

    Rows are compact JSON whose leading fields are step, policy, layer,
    bytes_of_layer, requested_experts, hit_experts, miss_experts,
    protected_before, followed by the (large) resident_before list.  Only the
    prefix is parsed.  Completion rows ({"step":N,"complete":1,...}) are skipped.
    """
    for raw in lines:
        if not raw.startswith(b'{"step":') or b'"layer":' not in raw[:256]:
            continue
        k = raw.find(_PREFIX_END)
        if k < 0:
            continue
        d = json.loads(raw[:k] + b"}")
        a = raw.find(b',"evicted":[')
        b = raw.find(b',"resident_after"')
        n_ev = raw.count(b"{", a, b) if 0 <= a < b else 0
        yield {
            "step": d["step"],
            "layer": d["layer"],
            "policy": d["policy"],
            "bytes_of_layer": d["bytes_of_layer"],
            "requested": d["requested_experts"],
            "hit": len(d["hit_experts"]),
            "miss": len(d["miss_experts"]),
            "protected": d["protected_before"],
            "evicted": n_ev,
        }


# --------------------------------------------------------------- grouping --
def build_layer_groups(rows: Sequence[dict]) -> List[dict]:
    """Group consecutive rows of the same layer, then number forward passes.

    A new forward pass starts when a group's layer index is <= the previous
    group's (llama.cpp executes MoE layers in ascending order within a pass).
    """
    groups: List[dict] = []
    cur = None
    for r in rows:
        if cur is not None and r["layer"] == cur["layer"]:
            cur["rows"].append(r)
        else:
            cur = {"layer": r["layer"], "rows": [r]}
            groups.append(cur)
    p, prev = -1, None
    for g in groups:
        if prev is None or g["layer"] <= prev:
            p += 1
        g["pass"] = p
        prev = g["layer"]
    return groups


def pass_kinds(groups: Sequence[dict], top_k: int = TOP_K) -> Dict[int, str]:
    """'decode' if every layer of the pass requested exactly top_k experts
    (one token), otherwise 'multi_token' (warm-up / prompt ubatch unions)."""
    sizes: Dict[int, set] = defaultdict(set)
    for g in groups:
        for r in g["rows"]:
            sizes[g["pass"]].add(len(set(r["requested"])))
    return {p: ("decode" if s == {top_k} else "multi_token") for p, s in sizes.items()}


# ------------------------------------------------------------ event counts --
def event_counts(rows: Sequence[dict], groups: Sequence[dict], n_generated: int,
                 n_layers: int = 48) -> dict:
    passes = max(g["pass"] for g in groups) + 1
    kinds = pass_kinds(groups)
    sizes = Counter(len(g["rows"]) for g in groups)
    layers_per_pass = Counter(g["pass"] for g in groups)
    steps = [r["step"] for r in rows]
    n_decode = sum(1 for k in kinds.values() if k == "decode")
    semantic = passes * n_layers
    return {
        "policy_rows": len(rows),
        "policy_step_contiguous": steps == list(range(len(rows))),
        "forward_passes": passes,
        "decode_passes": n_decode,
        "multi_token_passes": sorted(p for p, k in kinds.items() if k == "multi_token"),
        "layers": n_layers,
        "layer_groups": len(groups),
        "calls_per_layer_group_histogram": dict(sorted(sizes.items())),
        "layers_per_pass_histogram": dict(sorted(Counter(layers_per_pass.values()).items())),
        "semantic_events_if_one_per_layer_per_pass": semantic,
        "actual_over_semantic": len(rows) / semantic,
        "tokens_generated": n_generated,
        "calls_per_generated_token": len(rows) / n_generated,
        "decode_calls": sum(len(g["rows"]) for g in groups if kinds[g["pass"]] == "decode"),
        "decode_calls_per_decode_pass": sum(len(g["rows"]) for g in groups if kinds[g["pass"]] == "decode") / max(n_decode, 1),
        "calls_per_layer_per_decode_pass": sum(len(g["rows"]) for g in groups if kinds[g["pass"]] == "decode")
                                           / max(n_decode * n_layers, 1),
    }


# -------------------------------------------------------- set identity audit --
def triplet_identity(groups: Sequence[dict]) -> dict:
    kinds = pass_kinds(groups)
    out = {"all": Counter(), "decode": Counter(), "multi_token": Counter()}
    first_mismatch = None
    same_bytes = True
    for g in groups:
        rs = g["rows"]
        if len(rs) != 3:
            out["all"]["not_a_triplet"] += 1
            continue
        sets = [frozenset(r["requested"]) for r in rs]
        if len({r["bytes_of_layer"] for r in rs}) != 1:
            same_bytes = False
        if sets[0] == sets[1] == sets[2]:
            c = "identical"
        elif all(not (sets[i] & sets[j]) for i in range(3) for j in range(i + 1, 3)):
            c = "fully_different"
        else:
            c = "partially_different"
        out["all"][c] += 1
        out[kinds[g["pass"]]][c] += 1
        if c != "identical" and first_mismatch is None:
            first_mismatch = {"pass": g["pass"], "layer": g["layer"],
                              "steps": [r["step"] for r in rs],
                              "sets": [sorted(s) for s in sets]}
    def frac(c: Counter) -> dict:
        n = sum(v for k, v in c.items() if k != "not_a_triplet")
        return {"n_triplets": n, "counts": dict(c),
                **{k: (c.get(k, 0) / n if n else None)
                   for k in ("identical", "partially_different", "fully_different")}}
    return {"all": frac(out["all"]), "decode": frac(out["decode"]),
            "multi_token": frac(out["multi_token"]),
            "bytes_of_layer_identical_within_triplets": same_bytes,
            "first_mismatch": first_mismatch}


# ------------------------------------------------- predictor simulation ------
class DecayedTransitionSim:
    """Faithful, dependency-free mirror of DecayedTransitionPredictor
    (argine/research2/r2_10/predictors.py) and of the C++ PredictedProtectPolicy
    after_event()/predict_for_layer().  observe() == history.append + update +
    predict, in that order.  Float operations are applied in the reference's
    order so ties and scores are bit-identical."""

    def __init__(self, n_experts: int = N_EXPERTS, decay: float = PREDICTOR_DECAY, k: int = TOP_K):
        self.n, self.decay, self.k = n_experts, decay, k
        self.hist: List[Tuple[int, ...]] = []
        self.state: Dict[int, Dict[int, Tuple[float, int]]] = {}
        self.cell_updates = 0

    def observe(self, experts: Iterable[int]) -> Tuple[int, ...]:
        s = tuple(sorted(set(int(x) for x in experts)))
        self.hist.append(s)
        i = len(self.hist) - 1
        if i >= 1:
            for c in self.hist[i - 1]:
                row = self.state.setdefault(c, {})
                for e in s:
                    val, last = row.get(e, (0.0, 0))
                    row[e] = (val * (self.decay ** (i - last)) + 1.0, i)
                    self.cell_updates += 1
        return self.predict()

    def predict(self) -> Tuple[int, ...]:
        i = len(self.hist) - 1
        scores = [0.0] * self.n
        for c in self.hist[i]:
            row = self.state.get(c)
            if not row:
                continue
            for e, (val, last) in row.items():
                scores[e] += val * (self.decay ** (i - last))
        ranked = sorted(range(self.n), key=lambda e: (-scores[e], e))
        return tuple(sorted(ranked[: self.k]))


def _jac(a: Sequence[int], b: Sequence[int]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if (sa or sb) else 1.0


def simulate(groups: Sequence[dict], top_k: int = TOP_K) -> dict:
    """Replay the runtime stream (3 observations per layer per pass, as logged)
    and the R2 stream (1 observation per layer per pass) through the same
    predictor and compare them."""
    kinds = pass_kinds(groups)
    by_layer: Dict[int, List[dict]] = defaultdict(list)
    for g in groups:
        by_layer[g["layer"]].append(g)

    conf_match = conf_total = 0
    conf_first_mismatch = None
    first_div = None
    n_tb = 0
    sums = Counter()
    cell3 = cell1 = hist3 = hist1 = 0
    for layer, gl in sorted(by_layer.items()):
        s3, s1 = DecayedTransitionSim(), DecayedTransitionSim()
        p3_after: List[List[Tuple[int, ...]]] = []   # per group: prediction after each call
        p1_after: List[Tuple[int, ...]] = []         # per group: R2 prediction after the event
        prev_logged = None
        for g in gl:
            preds = []
            for r in g["rows"]:
                if prev_logged is not None:
                    conf_total += 1
                    if tuple(sorted(r["protected"])) == prev_logged:
                        conf_match += 1
                    elif conf_first_mismatch is None:
                        conf_first_mismatch = {"layer": layer, "pass": g["pass"], "step": r["step"]}
                elif r["protected"]:
                    conf_total += 1
                    if conf_first_mismatch is None:
                        conf_first_mismatch = {"layer": layer, "pass": g["pass"], "step": r["step"],
                                               "note": "first row should have empty protected set"}
                else:
                    conf_total += 1
                    conf_match += 1
                prev_logged = s3.observe(r["requested"])
                preds.append(prev_logged)
            p3_after.append(preds)
            # R2 semantics: ONE observation of the step's event set
            p1_after.append(s1.observe(g["rows"][0]["requested"]))
        cell3 += s3.cell_updates
        cell1 += s1.cell_updates
        hist3 += len(s3.hist)
        hist1 += len(s1.hist)

        # decode -> decode token boundaries
        for t in range(len(gl) - 1):
            a, b = gl[t], gl[t + 1]
            if kinds[a["pass"]] != "decode" or kinds[b["pass"]] != "decode":
                continue
            if len(a["rows"]) != 3 or len(b["rows"]) != 3:
                continue
            S_t = tuple(sorted(set(a["rows"][0]["requested"])))
            S_n = tuple(sorted(set(b["rows"][0]["requested"])))
            P2 = p1_after[t]                    # consulted by R2 at event t+1
            P3 = p3_after[t][2]                 # consulted by runtime at first call of t+1
            n_tb += 1
            if P2 != P3:
                cand = {"step": b["rows"][0]["step"], "pass": b["pass"], "layer": layer,
                        "R2_protected_set": list(P2), "R3_protected_set": list(P3)}
                if first_div is None or cand["step"] < first_div["step"]:
                    first_div = cand
            sums["eq_first_call"] += int(P2 == P3)
            sums["jac_first_call"] += _jac(P2, P3)
            sums["recall_R2"] += len(set(P2) & set(S_n)) / top_k
            sums["recall_R3"] += len(set(P3) & set(S_n)) / top_k
            sums["recall_persistence"] += len(set(S_t) & set(S_n)) / top_k
            sums["R2_overlap_current"] += len(set(P2) & set(S_t)) / top_k
            sums["R3_overlap_current"] += len(set(P3) & set(S_t)) / top_k
            sums["R3_equals_current"] += int(P3 == S_t)
            sums["R2_equals_current"] += int(P2 == S_t)
            for j in (0, 1):                    # sets consulted at calls 2 and 3 of token t
                Q = p3_after[t][j]
                sums[f"call{j+2}_overlap_current"] += len(set(Q) & set(S_t)) / top_k
                sums[f"call{j+2}_equals_current"] += int(Q == S_t)
                sums[f"call{j+2}_recall_next"] += len(set(Q) & set(S_n)) / top_k
    m = lambda key: (sums[key] / n_tb) if n_tb else None
    return {
        "runtime_stream_conformance_vs_logged_protected_before": {
            "comparable_rows": conf_total, "matching_rows": conf_match,
            "fraction": conf_match / conf_total if conf_total else None,
            "first_mismatch": conf_first_mismatch,
            "note": "simulated predictor fed the LOGGED 3x stream vs logged protected_before",
        },
        "decode_token_boundaries_compared": n_tb,
        "first_protected_set_divergence_at_comparable_event": first_div,
        "protected_set_consulted_at_first_call_of_next_token": {
            "fraction_identical_R2_vs_R3": m("eq_first_call"),
            "mean_jaccard_R2_vs_R3": m("jac_first_call"),
        },
        "next_token_recall_at_k": {
            "R2_semantics": m("recall_R2"), "R3_runtime_semantics": m("recall_R3"),
            "persistence_baseline_previous_set": m("recall_persistence"),
        },
        "collapse_onto_current_set": {
            "R2_mean_overlap_with_current_set": m("R2_overlap_current"),
            "R3_mean_overlap_with_current_set": m("R3_overlap_current"),
            "R2_fraction_equal_to_current_set": m("R2_equals_current"),
            "R3_fraction_equal_to_current_set": m("R3_equals_current"),
        },
        "within_token_sets_consulted": {
            "call2_mean_overlap_with_current_set": m("call2_overlap_current"),
            "call3_mean_overlap_with_current_set": m("call3_overlap_current"),
            "call2_fraction_equal_to_current_set": m("call2_equals_current"),
            "call3_fraction_equal_to_current_set": m("call3_equals_current"),
            "call2_recall_of_next_token": m("call2_recall_next"),
            "call3_recall_of_next_token": m("call3_recall_next"),
        },
        "state_effect": {
            "history_entries_R3": hist3, "history_entries_R2": hist1,
            "history_ratio": hist3 / hist1 if hist1 else None,
            "transition_cell_updates_R3": cell3, "transition_cell_updates_R2": cell1,
            "transition_update_ratio": cell3 / cell1 if cell1 else None,
            "effective_decay_per_token_R2": PREDICTOR_DECAY,
            "effective_decay_per_token_R3": PREDICTOR_DECAY ** 3,
            "effective_time_constant_tokens_R2": 1.0 / (1.0 - PREDICTOR_DECAY),
            "effective_time_constant_tokens_R3": 1.0 / (1.0 - PREDICTOR_DECAY ** 3),
        },
    }


def miss_by_call_position(groups: Sequence[dict]) -> dict:
    """Where do logical misses / evictions occur inside a triplet?

    If calls 2 and 3 were pure replays of call 1 they would always be all-hit.
    Under a binding budget they are not: members of an event can evict each
    other (same-event self-eviction, a documented property of the frozen
    policy), so the repeated calls re-miss and re-read them."""
    kinds = pass_kinds(groups)
    tab = {k: {"miss": [0, 0, 0], "hit": [0, 0, 0], "evicted": [0, 0, 0]}
           for k in ("decode", "multi_token")}
    for g in groups:
        if len(g["rows"]) != 3:
            continue
        for j, r in enumerate(g["rows"]):
            t = tab[kinds[g["pass"]]]
            t["miss"][j] += r["miss"]
            t["hit"][j] += r["hit"]
            t["evicted"][j] += r["evicted"]
    miss = [sum(tab[k]["miss"][j] for k in tab) for j in range(3)]
    hit = [sum(tab[k]["hit"][j] for k in tab) for j in range(3)]
    ev = [sum(tab[k]["evicted"][j] for k in tab) for j in range(3)]
    req = [m + h for m, h in zip(miss, hit)]
    tot_req, tot_hit, tot_miss = sum(req), sum(hit), sum(miss)
    return {
        "misses_by_call_position": miss, "hits_by_call_position": hit,
        "evictions_by_call_position": ev,
        "by_pass_kind": tab,
        "total_misses": tot_miss,
        "misses_at_calls_2_and_3": miss[1] + miss[2],
        "fraction_of_misses_at_calls_2_and_3": (miss[1] + miss[2]) / tot_miss if tot_miss else None,
        "extra_misses_over_first_call_only": (miss[1] + miss[2]) / miss[0] if miss[0] else None,
        "logical_hit_rate_as_logged_all_calls": tot_hit / tot_req if tot_req else None,
        "logical_hit_rate_first_call_only": hit[0] / req[0] if req[0] else None,
    }


def consecutive_identity(groups: Sequence[dict]) -> dict:
    """Under R2 semantics (one event per step/layer): how often are two
    consecutive decode events of the same layer IDENTICAL sets?"""
    kinds = pass_kinds(groups)
    by_layer: Dict[int, List[dict]] = defaultdict(list)
    for g in groups:
        by_layer[g["layer"]].append(g)
    n = same = 0
    jac = 0.0
    for gl in by_layer.values():
        for a, b in zip(gl, gl[1:]):
            if kinds[a["pass"]] != "decode" or kinds[b["pass"]] != "decode":
                continue
            sa, sb = set(a["rows"][0]["requested"]), set(b["rows"][0]["requested"])
            n += 1
            same += int(sa == sb)
            jac += len(sa & sb) / len(sa | sb)
    return {"decode_adjacent_pairs": n,
            "fraction_identical_sets": same / n if n else None,
            "mean_jaccard": jac / n if n else None}


def analyze(rows: Sequence[dict], n_generated: int) -> dict:
    groups = build_layer_groups(rows)
    return {
        "event_counts": event_counts(rows, groups, n_generated),
        "set_identity": triplet_identity(groups),
        "miss_position": miss_by_call_position(groups),
        "r2_consecutive_event_identity": consecutive_identity(groups),
        "simulation": simulate(groups),
    }


# ------------------------------------------------------------------- CLI ----
def _cmd_extract(src: str, dst: str) -> None:
    fh = sys.stdin.buffer if src == "-" else open(src, "rb")
    n = 0
    with open(dst, "w", encoding="utf-8") as out:
        for row in extract_rows(fh):
            out.write(json.dumps(row, separators=(",", ":")) + "\n")
            n += 1
    print(f"extracted {n} policy rows -> {dst}", file=sys.stderr)


def _cmd_analyze(src: str, dst: str, n_generated: int) -> None:
    with open(src, encoding="utf-8") as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    res = analyze(rows, n_generated)
    with open(dst, "w", encoding="utf-8") as out:
        json.dump(res, out, indent=1, sort_keys=True)
    print(json.dumps(res, indent=1, sort_keys=True))


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "extract":
        _cmd_extract(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 4 and sys.argv[1] == "analyze":
        _cmd_analyze(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 512)
    else:
        print(__doc__)
        sys.exit(2)
