"""R3-03A trace adapter: streaming, fail-closed analysis of the additive R3-03A JSONL schema.

The R3-03A runtime writes, per semantic policy event, TWO JSONL rows:
  POLICY_EVENT       the R2-12 decision row (+ additive "semantic_event" block)
  SERVICE_COMPLETION {"step", "complete": 1, "cache_resident_bytes_post_service"}
The frozen R2-12 parser (tools/r2_12_real_trace_conformance.py) treats every row as a policy
event and is left UNCHANGED; its replay building blocks (_build_policy, _patch_evict_one,
_protected_for_layer, Context) are reused so the reference semantics are the frozen ones.
Unknown rows fail closed.  Traces are streamed (they can exceed 5 GB); analysis can be
checkpointed and resumed.  No timing data is read.

CLI:  python tools/r3_03a_trace.py <trace.jsonl> --n-layers 48 --expected-passes 514
          [--sham] [--checkpoint ck.pkl --max-seconds 150] [--out result.json]
Exit: 0 pass, 1 fail, 3 in progress (checkpoint written).
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import r2_12_real_trace_conformance as rtc  # noqa: E402  (frozen reference; not modified)
from argine.research.policies import Context  # noqa: E402

POLICY_EVENT, SERVICE_COMPLETION, UNKNOWN = "POLICY_EVENT", "SERVICE_COMPLETION", "UNKNOWN"
COMPLETION_KEYS = {"step", "complete", "cache_resident_bytes_post_service"}
SEMANTIC_KEYS = {"schema", "pass", "layer", "node_kind", "first_node"}


class TraceAnalysisError(Exception):
    """Structural / provenance failure (fail closed)."""


def _is_int(x) -> bool:
    return type(x) is int


def classify_row(row: Any) -> str:
    if not isinstance(row, dict):
        return UNKNOWN
    if "complete" not in row and all(k in row for k in rtc.REQUIRED_FIELDS):
        return POLICY_EVENT
    if set(row) == COMPLETION_KEYS:
        return SERVICE_COMPLETION
    return UNKNOWN


class StreamAnalyzer:
    def __init__(self, n_layers: int, expected_passes: Optional[int], sham: bool = False,
                 require_completion: bool = True):
        self.n_layers, self.expected_passes = n_layers, expected_passes
        self.sham, self.require_completion = sham, require_completion
        self.n_events = self.n_completions = 0
        self.pending: Optional[int] = None          # step awaiting its completion row
        self.last_completed: Optional[int] = None
        self.cur_pass = 0
        self.next_layer = 0
        self.n_passes = 0
        self.seen = set()
        self.first = None                           # (policy, params, budget_bytes)
        self.sizes: Dict[int, int] = {}
        self.policy = None
        self.ctx = None
        self.evicted_log = []
        self.policy_id = None
        self.max_policy_bytes = 0
        self.max_post_service = 0
        self.post_service_over_budget = 0
        self.before_exceeds_prev_post = 0
        self.before_equals_prev_post = 0
        self.prev_post: Optional[int] = None
        self.score_max_abs_diff = 0.0
        self.lastref_mismatch = 0
        self.protected_compared = 0
        self.rows_policy = self.rows_completion = 0
        self.total_evictions_trace = 0

    # -- reference setup ---------------------------------------------------
    def _init_reference(self, row):
        params = row["policy_params"]
        miss = [f for f in rtc.REQUIRED_PARAM_FIELDS if f not in params]
        if miss:
            raise TraceAnalysisError(f"policy_params missing {miss}")
        self.first = (row["policy"], params, row["budget_bytes"])
        self.policy_id = row["policy"]
        replay_id = "b_selected" if (self.sham or self.policy_id == "predicted_protect_sham") else self.policy_id
        self.replay_id = replay_id
        nexp = int(params["n_experts"]) if params.get("n_experts") else (max(row["requested_experts"], default=0) + 1)
        if replay_id == "predicted_protect" and not params.get("n_experts"):
            raise TraceAnalysisError("predicted_protect trace without n_experts")
        self.ctx = Context(layers=list(range(self.n_layers)), n_experts=nexp, size=self.sizes,
                           budget=row["budget_bytes"], top_k=int(params.get("top_k") or 0))
        self._build_policy()

    def _build_policy(self, state=None):
        pparams = dict(self.first[1])
        self.policy = rtc._build_policy(self.replay_id, pparams)
        self.policy.start(self.ctx)
        if state is not None:
            vars(self.policy).update(state)
        self.evicted_log = []
        rtc._patch_evict_one(self.policy, self.replay_id, self.evicted_log)

    # -- row handling ------------------------------------------------------
    def feed(self, line) -> None:
        if isinstance(line, (bytes, bytearray)):
            line = line.decode("utf-8")
        line = line.strip()
        if not line:
            return
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise TraceAnalysisError(f"row {self.rows_policy + self.rows_completion}: invalid JSON ({e})")
        kind = classify_row(row)
        if kind == POLICY_EVENT:
            self._policy_row(row)
        elif kind == SERVICE_COMPLETION:
            self._completion_row(row)
        else:
            raise TraceAnalysisError(f"unknown row kind at row {self.rows_policy + self.rows_completion}: keys={sorted(row)[:8] if isinstance(row, dict) else type(row).__name__}")

    def _completion_row(self, row):
        self.rows_completion += 1
        step = row["step"]
        if row["complete"] != 1 or not _is_int(row["complete"]):
            raise TraceAnalysisError(f"completion step {step}: complete != 1")
        if not _is_int(step) or not _is_int(row["cache_resident_bytes_post_service"]) or row["cache_resident_bytes_post_service"] < 0:
            raise TraceAnalysisError(f"malformed completion row {row}")
        if self.pending is None:
            kind = "duplicate" if self.last_completed == step else "orphan"
            raise TraceAnalysisError(f"{kind} completion row for step {step}")
        if step != self.pending:
            raise TraceAnalysisError(f"completion step {step} != pending policy step {self.pending}")
        post = row["cache_resident_bytes_post_service"]
        self.n_completions += 1
        self.last_completed, self.pending = step, None
        self.max_post_service = max(self.max_post_service, post)
        if post > self.first[2]:
            self.post_service_over_budget += 1
        self.prev_post = post

    def _policy_row(self, row):
        self.rows_policy += 1
        if self.pending is not None:
            raise TraceAnalysisError(f"missing completion row for step {self.pending}")
        step = row["step"]
        if step != self.n_events:
            raise TraceAnalysisError(f"step {step} != expected {self.n_events}")
        se = row.get("semantic_event")
        if not isinstance(se, dict) or set(se) != SEMANTIC_KEYS or se["schema"] != "r3-03a-v1":
            raise TraceAnalysisError(f"step {step}: missing/invalid semantic_event")
        if se["first_node"] != 1 or se["layer"] != row["layer"] or not _is_int(se["pass"]):
            raise TraceAnalysisError(f"step {step}: semantic_event inconsistent")
        if self.first is None:
            self._init_reference(row)
        elif (row["policy"], row["policy_params"], row["budget_bytes"]) != self.first:
            raise TraceAnalysisError(f"step {step}: provenance (policy/params/budget) changed")
        # structural pass/layer progression
        p, l = se["pass"], se["layer"]
        if (p, l) in self.seen:
            raise TraceAnalysisError(f"duplicate semantic event (pass={p}, layer={l})")
        if self.n_events == 0:
            ok = (p == 0 and l == 0)
        elif self.next_layer < self.n_layers:
            ok = (p == self.cur_pass and l == self.next_layer)
        else:
            ok = (p == self.cur_pass + 1 and l == 0)
        if not ok:
            raise TraceAnalysisError(f"semantic event (pass={p}, layer={l}) out of order/missing (expected pass {self.cur_pass}, layer {self.next_layer})")
        self.seen.add((p, l))
        if l == 0:
            self.n_passes += 1
        self.cur_pass, self.next_layer = p, l + 1
        # sizes / budget
        if self.sizes.setdefault(l, row["bytes_of_layer"]) != row["bytes_of_layer"]:
            raise TraceAnalysisError(f"step {step}: bytes_of_layer changed for layer {l}")
        if row["policy_resident_bytes_after"] > row["budget_bytes"]:
            raise TraceAnalysisError(f"step {step}: budget violation {row['policy_resident_bytes_after']} > {row['budget_bytes']}")
        self.max_policy_bytes = max(self.max_policy_bytes, row["policy_resident_bytes_after"])
        if self.prev_post is not None:
            b = row["cache_resident_bytes_before"]
            if b > self.prev_post:
                self.before_exceeds_prev_post += 1
            elif b == self.prev_post:
                self.before_equals_prev_post += 1
        # frozen R2 reference replay (decision by decision)
        pol = self.policy
        protected_before = rtc._protected_for_layer(pol, self.replay_id, l)
        self.evicted_log.clear()
        missed = pol.access(step, l, row["requested_experts"])
        py_evicted = list(self.evicted_log)
        req = sorted(row["requested_experts"])
        py_hit = sorted(e for e in req if e not in missed)
        py_res = sorted(list(o) for o in pol.resident.keys())
        tr_res = sorted([r["layer"], r["expert"]] for r in row["resident_after"])
        checks = [("hit_experts", py_hit, sorted(row["hit_experts"])), ("miss_experts", sorted(missed), sorted(row["miss_experts"])),
                  ("evicted", py_evicted, [[e["layer"], e["expert"]] for e in row["evicted"]]),
                  ("resident_after", py_res, tr_res),
                  ("policy_resident_bytes_after", pol.resident_bytes, row["policy_resident_bytes_after"])]
        if not self.sham and self.policy_id != "predicted_protect_sham":
            checks.insert(0, ("protected_before", protected_before, sorted(row["protected_before"])))
            self.protected_compared += 1
        for field, a, b in checks:
            if a != b:
                raise TraceAnalysisError(f"R2 replay mismatch step {step} layer {l} field {field}")
        for r in row["resident_after"]:                     # history (last_ref) and decay (score)
            o = (r["layer"], r["expert"])
            d = abs(float(pol.scores[o]) - float(r["score"]))
            self.score_max_abs_diff = max(self.score_max_abs_diff, d)
            if d > 1e-9 * max(1.0, abs(float(r["score"]))):
                raise TraceAnalysisError(f"decay/score mismatch step {step} {o}: {pol.scores[o]} vs {r['score']}")
            if pol.last_ref[o] != r["last_ref"]:
                self.lastref_mismatch += 1
                raise TraceAnalysisError(f"history/last_ref mismatch step {step} {o}")
        self.total_evictions_trace += len(row["evicted"])
        self.n_events += 1
        if self.require_completion:
            self.pending = step

    # -- completion --------------------------------------------------------
    def finish(self) -> Dict[str, Any]:
        if self.n_events == 0:
            raise TraceAnalysisError("trace is empty")
        if self.pending is not None and self.require_completion:
            raise TraceAnalysisError(f"missing completion row for final step {self.pending}")
        if self.require_completion and self.n_completions != self.n_events:
            raise TraceAnalysisError(f"completions {self.n_completions} != policy events {self.n_events}")
        if self.next_layer != self.n_layers:
            raise TraceAnalysisError(f"last pass incomplete: {self.next_layer}/{self.n_layers} layers")
        if self.expected_passes is not None and self.n_passes != self.expected_passes:
            raise TraceAnalysisError(f"passes {self.n_passes} != expected {self.expected_passes}")
        return {"ok": True, "policy_id": self.policy_id, "policy_event_rows": self.rows_policy,
                "completion_rows": self.rows_completion, "passes": self.n_passes,
                "events_per_pass": self.n_layers, "semantic_events": self.n_events,
                "duplicate_events": 0, "missing_events": 0, "budget_bytes": self.first[2],
                "budget_violations": 0, "max_policy_resident_bytes": self.max_policy_bytes,
                "physical": {"max_post_service_bytes": self.max_post_service,
                             "post_service_over_budget": self.post_service_over_budget,
                             "next_before_greater_than_prev_post_service": self.before_exceeds_prev_post,
                             "next_before_equal_prev_post_service": self.before_equals_prev_post,
                             "note": "post-service bytes may exceed policy bytes only by physical margin/transient slices; informational"},
                "r2_replay": "MATCH", "protected_before_compared_events": self.protected_compared,
                "history_last_ref": "MATCH", "decay_score_max_abs_diff": self.score_max_abs_diff,
                "total_evictions": self.total_evictions_trace}

    # -- checkpointing -------------------------------------------------------
    def get_state(self):
        d = {k: v for k, v in self.__dict__.items() if k not in ("policy", "ctx", "evicted_log")}
        pol = None if self.policy is None else {k: v for k, v in vars(self.policy).items() if k not in ("_evict_one", "ctx")}
        return {"a": d, "pol": pol}

    @classmethod
    def from_state(cls, st):
        o = cls.__new__(cls)
        o.__dict__.update(st["a"])
        o.policy = o.ctx = None
        o.evicted_log = []
        if st["pol"] is not None:
            p0, params, budget = o.first
            top_k = int(params.get("top_k") or 0)
            nexp = int(params["n_experts"]) if params.get("n_experts") else 1
            o.ctx = Context(layers=list(range(o.n_layers)), n_experts=nexp, size=o.sizes, budget=budget, top_k=top_k)
            o._build_policy(st["pol"])
        return o


def analyse_file(path, *, n_layers, expected_passes, sham=False, checkpoint=None, max_seconds=None,
                 require_completion=True) -> Dict[str, Any]:
    """Stream-analyse a trace.  Returns {'ok':bool,...}; {'in_progress':True} when time budget hit."""
    offset, an = 0, None
    ck = Path(checkpoint) if checkpoint else None
    if ck and ck.exists():
        st = pickle.loads(ck.read_bytes())
        offset, an = st["offset"], StreamAnalyzer.from_state(st["state"])
    if an is None:
        an = StreamAnalyzer(n_layers, expected_passes, sham, require_completion)
    t0 = time.monotonic()
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            while True:
                line = f.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    raise TraceAnalysisError("trace ends with a truncated (non-terminated) row")
                an.feed(line)
                offset += len(line)
                if max_seconds is not None and time.monotonic() - t0 > max_seconds:
                    if ck is None:
                        raise TraceAnalysisError("time budget reached without a checkpoint path")
                    tmp = ck.with_suffix(".tmp")
                    tmp.write_bytes(pickle.dumps({"offset": offset, "state": an.get_state()}))
                    tmp.replace(ck)
                    return {"ok": None, "in_progress": True, "bytes_done": offset, "events_done": an.n_events}
        res = an.finish()
        res["trace_bytes"] = offset
        return res
    except TraceAnalysisError as e:
        return {"ok": False, "failure": str(e), "events_before_failure": an.n_events, "bytes_done": offset}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("--n-layers", type=int, required=True)
    ap.add_argument("--expected-passes", type=int)
    ap.add_argument("--sham", action="store_true")
    ap.add_argument("--checkpoint")
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    res = analyse_file(a.trace, n_layers=a.n_layers, expected_passes=a.expected_passes, sham=a.sham,
                       checkpoint=a.checkpoint, max_seconds=a.max_seconds)
    txt = json.dumps(res, indent=1, sort_keys=True, default=str)
    print(txt)
    if a.out and not res.get("in_progress"):
        Path(a.out).write_text(txt, encoding="utf-8")
    return 3 if res.get("in_progress") else (0 if res["ok"] else 1)


if __name__ == "__main__":
    sys.exit(main())
